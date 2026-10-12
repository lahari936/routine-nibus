"""One-way account linking endpoints.

POST /ring/token-exchange  Ring sends an auth code; exchange it within 60 s and park the tokens as unclaimed.
GET  /ring/link            Ring sends the user here with ?nonce&time; ask which email gets the alerts.
POST /ring/link            Match nonce to an unclaimed token, complete the link, create the home, start backfill.
"""
import html
import json
import os
import time
import uuid
from dataclasses import asdict

import boto3

from core import linking
from core.models import Caregiver, Home
from core.ring_client import exchange_code
from lambdas.common.http import form, page, query
from lambdas.common.ring import app_secret, client_from_tokens, hmac_key
from lambdas.common.store import DynamoStore

store = DynamoStore()
lambda_client = boto3.client("lambda")


def token_exchange(event) -> dict:
    # The inbound body format is undocumented: accept JSON or form, field "code".
    code = form(event).get("code")
    if not code:
        return {"statusCode": 400}
    s = app_secret()
    tokens = exchange_code(code, s["client_id"], s["client_secret"])
    store.put("UNCLAIMED", f"{int(time.time())}#{uuid.uuid4()}", asdict(tokens), ttl=900)
    return {"statusCode": 200}


def link_form(event) -> dict:
    q = query(event)
    hidden = "".join(f"<input type=hidden name={k} value='{html.escape(q.get(k, ''))}'>" for k in ("nonce", "time"))
    return page(200, "Connect Routine Guardian", f"""
        <p>Who should hear about routine changes at this home?</p>
        <form method=post>{hidden}
          <p><label>Your email<br><input name=email type=email required style='font:inherit;width:100%'></label></p>
          <p><button style='font:inherit;padding:.5rem 1rem'>Connect</button></p>
        </form>""")


def link(event) -> dict:
    f = form(event)
    unclaimed = store.query("UNCLAIMED")

    def claimed(account_id: str, client) -> None:
        for item in unclaimed:
            if json.loads(item["doc"])["access_token"] == client.tokens.access_token:
                store.delete("UNCLAIMED", item["sk"])
        existing = store.get(f"ACCT#{account_id}", "META")
        home_id = existing["home_id"] if existing else str(uuid.uuid4())
        # Device resources carry no type, so doorbells are recognised by name. Editable later in settings.
        outdoor = [d.id for d in client.devices() if "doorbell" in d.name.lower()]
        store.put(f"ACCT#{account_id}", "META", {"home_id": home_id, "outdoor": outdoor, "tokens": asdict(client.tokens)})
        if not store.home(home_id):
            store.put_home(Home(id=home_id, name="Home", account_id=account_id,
                                caregivers=[Caregiver(name=f["email"], email=f["email"])]))
        lambda_client.invoke(FunctionName=os.environ["BACKFILL_FUNCTION"], InvocationType="Event",
                             Payload=json.dumps({"account_id": account_id}))

    clients = [client_from_tokens(json.loads(i["doc"])) for i in unclaimed]
    ok = linking.claim(hmac_key(), f.get("nonce", ""), f.get("time", "0"), f.get("email", ""), clients,
                       on_claimed=claimed)
    if ok:
        return page(200, "Connected", "<p>Routine Guardian is learning this home's routine. "
                                      "Expect a 'learning' badge for the first week.</p>")
    return page(403, "Link expired", "<p>Please start again from the Ring app.</p>")


def handler(event, context):
    route = event["routeKey"]
    if route == "POST /ring/token-exchange":
        return token_exchange(event)
    if route == "GET /ring/link":
        return link_form(event)
    return link(event)

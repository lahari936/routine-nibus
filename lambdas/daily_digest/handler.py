"""Daily: email caregivers a short digest of yesterday. Claude (Bedrock) only writes prose from structured
facts; if Bedrock is unavailable or declines, a plain template is sent instead. Input {"home_id", "day"} optional."""
import json
import os
from datetime import date, datetime, timedelta, timezone

import anthropic
import boto3

from ring_routine_baseline.facts import day_summary, plain_digest
from lambdas.common.store import DynamoStore

store = DynamoStore()
ses = boto3.client("ses")
MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "anthropic.claude-opus-5-5")

SYSTEM = """Write a short daily digest for family caregivers of an older person who lives alone, from the
JSON facts the user sends (Ring motion, doorbell and door-sensor events compared with the home's usual routine).
3 to 5 plain sentences, no headings or lists. Lead with whether the day looked like a usual day. Mention
anything that differed from usual and every alert with its status. Use only the facts given; do not guess at
causes, health or medical conditions. If "learning" is true, say the routine is still being learned."""


def write(facts: dict) -> str:
    try:
        client = anthropic.AnthropicBedrockMantle(aws_region=os.environ.get("AWS_REGION", "us-east-1"))
        msg = client.messages.create(model=MODEL_ID, max_tokens=2000, system=SYSTEM,
                                     output_config={"effort": "low"},  # short summary of given facts
                                     messages=[{"role": "user", "content": json.dumps(facts)}])
        text = "".join(b.text for b in msg.content if b.type == "text").strip()
        if msg.stop_reason == "end_turn" and text:
            return text
        print(f"digest model stopped with {msg.stop_reason}; using template")
    except Exception as e:  # API, auth or region errors: never lose the digest
        print(f"Bedrock digest failed: {e!r}; using template")
    return plain_digest(facts)


def digest(home_id: str, day: date | None = None) -> dict | None:
    home = store.home(home_id)
    if not home or home.paused:
        return None
    day = day or datetime.now(timezone.utc).astimezone(home.tz).date() - timedelta(days=1)
    start = datetime(day.year, day.month, day.day, tzinfo=home.tz)
    facts = day_summary(home, store.baseline(home_id), store.events(home_id, start, start + timedelta(days=1)),
                        store.alerts(home_id, day.isoformat(), day.isoformat() + "~"), day)
    text = write(facts)
    store.put(f"HOME#{home_id}", f"DIGEST#{day.isoformat()}", {"date": day.isoformat(), "text": text, "facts": facts})
    to = [c.email for c in home.caregivers if c.email]
    if to:
        ses.send_email(Source=os.environ["SES_FROM"], Destination={"ToAddresses": to},
                       Message={"Subject": {"Data": f"{home.name}: {facts['weekday']} digest"},
                                "Body": {"Text": {"Data": text + "\n\nRoutine Guardian notices routine changes. "
                                                                 "It is not a medical device."}}})
    return {"home_id": home_id, "date": day.isoformat(), "text": text}


def handler(event, context):
    event = event or {}
    day = date.fromisoformat(event["day"]) if event.get("day") else None
    ids = [event["home_id"]] if event.get("home_id") else store.home_ids()
    return {"digests": [d for hid in ids if (d := digest(hid, day))]}

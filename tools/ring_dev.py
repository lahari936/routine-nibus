"""Local Ring integration harness for Phase 2 (before the AWS pipeline exists).

  python -m tools.ring_dev serve [--port 8080]   # webhook + token-exchange + account-link endpoints
  python -m tools.ring_dev smoke                 # list devices and last 24 h of event history
  python -m tools.ring_dev serve --demo          # also map account ava1.ring.account.demo -> home-1 for simulator.replay

Expose `serve` publicly (e.g. `cloudflared tunnel --url http://localhost:8080`) and set in the Ring console:
  Webhook URL        https://<tunnel>/ring/webhook
  Token Exchange URL https://<tunnel>/ring/token-exchange
  Account Link URL   https://<tunnel>/ring/link?email=<you>

Env: RING_CLIENT_ID, RING_CLIENT_SECRET, RING_HMAC_KEY, optional RING_API_BASE.
State lives in .devdata/ (gitignored): tokens, account -> home mapping, seen request ids, events.jsonl.
"""
import argparse
import json
import os
import sys
import urllib.parse
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock

from ring_routine_baseline import linking, webhook
from ring_routine_baseline.models import Event
from ring_routine_baseline.ring_client import API_BASE, RingClient, Tokens, exchange_code

DATA = Path(".devdata")
STATE = DATA / "state.json"
EVENTS = DATA / "events.jsonl"
CLIENT_ID = os.environ.get("RING_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("RING_CLIENT_SECRET", "")
HMAC_KEY = os.environ.get("RING_HMAC_KEY", "").encode()
BASE = os.environ.get("RING_API_BASE", API_BASE)


class FileStore:
    """Dev stand-in for the DynamoDB table. One process, one lock."""

    def __init__(self):
        DATA.mkdir(exist_ok=True)
        self.lock = Lock()
        self.state = json.loads(STATE.read_text()) if STATE.exists() else \
            {"seen": [], "accounts": {}, "tokens": {}, "unclaimed": []}

    def _save(self):
        STATE.write_text(json.dumps(self.state, indent=2))

    def seen(self, request_id: str) -> bool:
        return request_id in self.state["seen"]

    def mark_seen(self, request_id: str) -> None:
        with self.lock:
            self.state["seen"].append(request_id)
            self._save()

    def home_for_account(self, account_id: str):
        acct = self.state["accounts"].get(account_id)
        return (acct["home_id"], set(acct["outdoor"])) if acct else None

    def save_event(self, event: Event) -> None:
        with self.lock, EVENTS.open("a") as f:
            f.write(event.model_dump_json() + "\n")
        print(f"stored {event.kind:14} {event.device_id} {event.ts:%H:%M:%S} id={event.event_id}")

    def unlink(self, account_id: str) -> None:
        with self.lock:
            acct = self.state["accounts"].pop(account_id, None)
            self.state["tokens"].pop(account_id, None)
            self._save()
        if acct and EVENTS.exists():
            keep = [l for l in EVENTS.read_text().splitlines() if json.loads(l)["home_id"] != acct["home_id"]]
            EVENTS.write_text("".join(l + "\n" for l in keep))
        print(f"unlinked {account_id}, household data deleted")

    def client(self, tokens: dict, account_id: str | None = None) -> RingClient:
        def persist(t: Tokens):  # refresh tokens rotate; losing the new one means relinking
            with self.lock:
                if account_id:
                    self.state["tokens"][account_id] = asdict(t)
                self._save()
        return RingClient(CLIENT_ID, CLIENT_SECRET, Tokens(**tokens), on_refresh=persist, base=BASE)


store: FileStore


class Handler(BaseHTTPRequestHandler):
    def _reply(self, status: int, text: str = "") -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(text.encode())

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if path == "/ring/webhook":
            try:
                status = webhook.handle(body, dict(self.headers), HMAC_KEY, store)
            except Exception as e:  # 5xx so Ring retries
                print(f"webhook error: {e!r}", file=sys.stderr)
                status = 500
            print(f"webhook -> {status}")
            return self._reply(status)
        if path == "/ring/token-exchange":
            # Inbound format is undocumented: accept JSON or form, field "code".
            try:
                fields = json.loads(body)
            except ValueError:
                fields = {k: v[0] for k, v in urllib.parse.parse_qs(body.decode()).items()}
            tokens = exchange_code(fields["code"], CLIENT_ID, CLIENT_SECRET)
            with store.lock:
                store.state["unclaimed"].append(asdict(tokens))
                store._save()
            print("token exchanged; waiting for account link")
            return self._reply(200)
        self._reply(404)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        if url.path != "/ring/link":
            return self._reply(404)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        unclaimed = [store.client(t) for t in store.state["unclaimed"]]

        def claimed(account_id: str, client: RingClient):
            with store.lock:
                store.state["unclaimed"] = [t for t in store.state["unclaimed"]
                                            if t["access_token"] != client.tokens.access_token]
                store.state["tokens"][account_id] = asdict(client.tokens)
                doorbells = [d.id for d in client.devices() if "doorbell" in d.name.lower()]
                store.state["accounts"][account_id] = {"home_id": account_id, "outdoor": doorbells}
                store._save()

        ok = linking.claim(HMAC_KEY, q.get("nonce", ""), q.get("time", "0"), q.get("email", "dev@localhost"),
                           unclaimed, on_claimed=claimed)
        self._reply(200 if ok else 403, "Linked. You can close this page." if ok else "Link request not recognised.")


def smoke() -> None:
    accounts = store.state["tokens"]
    if not accounts:
        sys.exit("No linked account in .devdata/state.json. Link one via `serve` first.")
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    for account_id, tokens in accounts.items():
        client = store.client(tokens, account_id)
        print(f"account {account_id}")
        for d in client.devices():
            print(f"  device {d.id} {d.name!r} online={d.online} capabilities={sorted(d.capabilities)}")
            for item in client.history(d.id, since=since):
                a = item["attributes"]
                print(f"    {datetime.fromtimestamp(a['start'] / 1000, timezone.utc):%m-%d %H:%M:%S}Z "
                      f"{a['event_type']}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["serve", "smoke"])
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--demo", action="store_true")
    a = p.parse_args()
    store = FileStore()
    if a.demo:
        store.state["accounts"]["ava1.ring.account.demo"] = {"home_id": "home-1", "outdoor": ["doorbell"]}
    if a.cmd == "smoke":
        smoke()
    else:
        print(f"listening on :{a.port}")
        ThreadingHTTPServer(("", a.port), Handler).serve_forever()

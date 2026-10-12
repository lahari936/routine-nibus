"""Send a scenario as signed Ring v1.1 webhooks to a local or deployed endpoint.

python -m simulator.replay simulator/scenarios/missed_morning.yml http://localhost:8080/ring/webhook \
    --account ava1.ring.account.demo [--date 2026-10-19] [--repeat 2]
Needs RING_HMAC_KEY in the environment. --repeat resends each webhook to show retries are stored once.
"""
import argparse
import hashlib
import hmac
import json
import os
import urllib.error
import urllib.request
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from core.models import Event
from simulator.evaluate import load_scenario

RING_TYPES = {"ding": "button_press", "contact_open": "contact_sensor_faulted",
              "contact_close": "contact_sensor_cleared", "online": "device_online", "offline": "device_offline"}


def to_webhook(event: Event, account_id: str) -> dict:
    attrs = {"source": event.device_id, "source_type": "devices", "timestamp": int(event.ts.timestamp() * 1000)}
    if event.kind.startswith("motion"):
        rtype = "motion_detected"
        attrs["sub_type"] = "human" if event.kind == "motion_human" else "other"
    else:
        rtype = RING_TYPES[event.kind]
    return {
        "meta": {"version": "1.1", "time": datetime.now(timezone.utc).isoformat(),
                 "request_id": str(uuid.uuid5(uuid.NAMESPACE_URL, event.event_id)), "account_id": account_id},
        "data": {"id": event.event_id, "type": rtype, "attributes": attrs,
                 "relationships": {"devices": {"links": {"self": f"/v1/devices/{event.device_id}"}}}},
    }


def sign(key: bytes, body: bytes) -> str:
    return "sha256=" + hmac.new(key, body, hashlib.sha256).hexdigest()


def post(url: str, body: bytes, key: bytes) -> int:
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json", "X-Signature": sign(key, body)})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("scenario", type=Path)
    p.add_argument("url")
    p.add_argument("--account", required=True)
    p.add_argument("--date", type=date.fromisoformat, help="replay the scenario on this date instead")
    p.add_argument("--repeat", type=int, default=1)
    a = p.parse_args()
    key = os.environ["RING_HMAC_KEY"].encode()
    day, _, events = load_scenario(a.scenario)
    for e in events:
        if a.date:
            e = e.model_copy(update={"ts": e.ts + (datetime.combine(a.date, datetime.min.time())
                                                   - datetime.combine(day, datetime.min.time()))})
        body = json.dumps(to_webhook(e, a.account)).encode()
        statuses = [post(a.url, body, key) for _ in range(a.repeat)]
        print(f"{e.ts:%Y-%m-%d %H:%M}Z {e.device_id:15} {e.kind:14} -> {statuses}")

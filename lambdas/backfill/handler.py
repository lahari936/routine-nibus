"""Invoked async right after linking: pull 28 days of Ring event history, then build the first baseline."""
from datetime import datetime, timedelta, timezone

from lambdas.common.ring import client_for_account
from lambdas.common.store import DynamoStore
from lambdas.nightly_baseline.handler import rebuild

store = DynamoStore()


def handler(event, context):
    account_id = event["account_id"]
    home_id, outdoor = store.home_for_account(account_id)
    client = client_for_account(store, account_id)
    since = datetime.now(timezone.utc) - timedelta(days=28)
    n = 0
    for device in client.devices():
        try:
            events = client.history_events(home_id, device.id, device.id not in outdoor, since)
        except Exception as e:  # sensors may have no history endpoint; keep going
            print(f"history failed for {device.id}: {e!r}")
            continue
        store.save_events(events)
        n += len(events)
    rebuild(home_id)
    return {"home_id": home_id, "events": n}

"""Nightly: recompute each home's baseline from its last 28 days. Input {"home_id"} limits it to one home."""
from datetime import datetime, time, timedelta, timezone

from core.baseline import compute_baseline
from lambdas.common.store import DynamoStore

store = DynamoStore()


def rebuild(home_id: str, now: datetime | None = None) -> None:
    home = store.home(home_id)
    if not home:
        return
    today = (now or datetime.now(timezone.utc)).astimezone(home.tz).date()
    start = datetime.combine(today - timedelta(days=28), time(), tzinfo=home.tz)
    end = datetime.combine(today, time(), tzinfo=home.tz)
    store.put_baseline(compute_baseline(home, store.events(home_id, start, end), today))


def handler(event, context):
    event = event or {}
    ids = [event["home_id"]] if event.get("home_id") else store.home_ids()
    for hid in ids:
        rebuild(hid)
    return {"homes": len(ids)}

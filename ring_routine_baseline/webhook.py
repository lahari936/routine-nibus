"""Ring webhooks (payload v1.1): HMAC verification, normalization to Event, idempotent handling."""
import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any, Protocol

from ring_routine_baseline.models import Event, EventKind

# Ring event type -> our kind. Motion is split by attributes.sub_type below.
KINDS: dict[str, EventKind] = {
    "button_press": "ding",
    "contact_sensor_faulted": "contact_open",
    "contact_sensor_cleared": "contact_close",
    "device_online": "online",
    "device_offline": "offline",
}
HUMAN_SUBTYPES = {"human", "person", "motion.human"}


def verify_signature(key: bytes, raw_body: bytes, header: str | None) -> bool:
    """X-Signature: sha256=<hex HMAC-SHA256 of the raw body>. Never verify re-serialized JSON."""
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(key, raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256=").strip().lower())


def normalize(payload: dict[str, Any], home_id: str, outdoor_devices: set[str]) -> Event | None:
    """Device events -> Event. Returns None for account/subscription lifecycle events."""
    data = payload["data"]
    attrs = data.get("attributes", {})
    rtype = data["type"]
    if attrs.get("source_type") != "devices":
        return None
    if rtype == "motion_detected":
        kind: EventKind = "motion_human" if attrs.get("sub_type") in HUMAN_SUBTYPES else "motion_other"
    elif rtype in KINDS:
        kind = KINDS[rtype]
    else:
        return None
    device_id = attrs["source"]
    return Event(
        event_id=data["id"], home_id=home_id, device_id=device_id, kind=kind,
        ts=datetime.fromtimestamp(attrs["timestamp"] / 1000, timezone.utc),
        indoor=kind.startswith("contact") or (device_id not in outdoor_devices and kind != "ding"),
        raw=payload,
    )


class Store(Protocol):
    def seen(self, request_id: str) -> bool: ...
    def mark_seen(self, request_id: str) -> None: ...  # ttl 7 days
    def home_for_account(self, account_id: str) -> tuple[str, set[str]] | None: ...  # (home_id, outdoor devices)
    def save_event(self, event: Event) -> None: ...
    def unlink(self, account_id: str) -> None: ...  # delete the household's data


def handle(raw_body: bytes, headers: dict[str, str], key: bytes, store: Store) -> int:
    """Returns the HTTP status. Must stay fast: Ring times out after 5 s and retries 5xx.
    4xx is permanent (no retry), so only use it for requests that can never succeed."""
    sig = {k.lower(): v for k, v in headers.items()}.get("x-signature")
    if not verify_signature(key, raw_body, sig):
        return 401
    try:
        payload = json.loads(raw_body)
        meta, data = payload["meta"], payload["data"]
    except (ValueError, KeyError, TypeError):
        return 400
    if store.seen(meta["request_id"]):
        return 200  # retry of something we already have
    # Mark only after the work succeeds: an exception becomes a 5xx and Ring retries.
    # A racing duplicate is harmless because events are keyed by event_id.
    if data["type"] == "app_integration_removed":
        store.unlink(meta["account_id"])
    elif (home := store.home_for_account(meta["account_id"])) and (event := normalize(payload, *home)):
        store.save_event(event)
    store.mark_seen(meta["request_id"])
    return 200

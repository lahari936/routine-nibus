import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone

from core import linking, webhook
from core.ring_client import RingClient, Tokens, exchange_code
from simulator.evaluate import SCENARIOS, load_scenario
from simulator.replay import sign, to_webhook

KEY = b"test-hmac-key"


class FakeRing:
    """Scripted transport: queue of responses per (method, path-prefix), records every call."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        for (m, prefix), responses in self.routes.items():
            if m == method and prefix in url:
                status, hdrs, payload = responses.pop(0) if len(responses) > 1 else responses[0]
                return status, hdrs, payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        raise AssertionError(f"unexpected {method} {url}")


TOKEN_OK = (200, {}, {"access_token": "new-access", "refresh_token": "new-refresh",
                      "expires_in": 14400, "token_type": "Bearer"})


def client(routes, expires_in=3600, **kw):
    fake = FakeRing(routes)
    return RingClient("cid", "secret", Tokens("old-access", "old-refresh", time.time() + expires_in),
                      send=fake, sleep=lambda s: fake.calls.append(("SLEEP", s, None, None)), **kw), fake


# --- webhook signature + normalization ---

def test_signature_is_hex_hmac_of_raw_body():
    body = b'{"meta":{},"data":{}}'
    good = "sha256=" + hmac.new(KEY, body, hashlib.sha256).hexdigest()
    assert webhook.verify_signature(KEY, body, good)
    assert not webhook.verify_signature(KEY, body + b" ", good)  # re-serialized body fails
    assert not webhook.verify_signature(KEY, body, good.removeprefix("sha256="))
    assert not webhook.verify_signature(KEY, body, None)
    assert not webhook.verify_signature(b"other", body, good)


def payload(rtype, source="doorbell", **attrs):
    return {"meta": {"version": "1.1", "request_id": "r1", "account_id": "acct"},
            "data": {"id": "evt-1", "type": rtype,
                     "attributes": {"source": source, "source_type": "devices", "timestamp": 1760000000000, **attrs}}}


def test_normalize_maps_ring_types():
    outdoor = {"doorbell"}
    n = lambda p: webhook.normalize(p, "home-1", outdoor)
    assert (n(payload("motion_detected", sub_type="human")).kind, n(payload("motion_detected")).kind) == \
        ("motion_human", "motion_other")
    assert not n(payload("motion_detected", sub_type="human")).indoor
    assert n(payload("motion_detected", "hall_cam", sub_type="human")).indoor
    assert n(payload("button_press")).kind == "ding" and not n(payload("button_press")).indoor
    door = n(payload("contact_sensor_faulted", "front_door"))
    assert door.kind == "contact_open" and door.indoor
    assert n(payload("contact_sensor_cleared", "front_door")).kind == "contact_close"
    assert n(payload("device_offline")).kind == "offline"
    e = n(payload("button_press"))
    assert e.event_id == "evt-1" and e.ts == datetime.fromtimestamp(1760000000, timezone.utc)
    sub = payload("subscription_activated")
    sub["data"]["attributes"]["source_type"] = "subscriptions"
    assert n(sub) is None


class MemStore:
    def __init__(self):
        self.ids, self.events, self.unlinked = set(), [], []

    def seen(self, rid): return rid in self.ids
    def mark_seen(self, rid): self.ids.add(rid)
    def home_for_account(self, acct): return ("home-1", {"doorbell"}) if acct == "ava1.ring.account.demo" else None
    def save_event(self, e): self.events.append(e)
    def unlink(self, acct): self.unlinked.append(acct)


def test_replayed_scenario_is_verified_and_stored_once_despite_retries():
    _, _, events = load_scenario(SCENARIOS / "unanswered_doorbell.yml")
    store = MemStore()
    for e in events:
        body = json.dumps(to_webhook(e, "ava1.ring.account.demo")).encode()
        for _ in range(3):  # Ring retries on timeouts / 5xx
            assert webhook.handle(body, {"X-Signature": sign(KEY, body)}, KEY, store) == 200
    assert [(e.kind, e.ts, e.indoor) for e in store.events] == [(e.kind, e.ts, e.indoor) for e in events]


def test_handle_rejects_bad_signature_and_unlinks():
    store = MemStore()
    body = json.dumps(payload("button_press")).encode()
    assert webhook.handle(body, {"X-Signature": sign(b"wrong", body)}, KEY, store) == 401
    assert webhook.handle(b"not json", {"X-Signature": sign(KEY, b"not json")}, KEY, store) == 400
    removed = {"meta": {"request_id": "r9", "account_id": "acct"},
               "data": {"id": "x", "type": "app_integration_removed",
                        "attributes": {"source": "acct", "source_type": "users", "timestamp": 1}}}
    body = json.dumps(removed).encode()
    assert webhook.handle(body, {"x-signature": sign(KEY, body)}, KEY, store) == 200
    assert store.unlinked == ["acct"] and not store.events


def test_failed_processing_is_not_marked_seen():
    class Boom(MemStore):
        def save_event(self, e): raise RuntimeError("dynamo down")
    store = Boom()
    body = json.dumps(to_webhook(load_scenario(SCENARIOS / "normal_day.yml")[2][0], "ava1.ring.account.demo")).encode()
    try:
        webhook.handle(body, {"X-Signature": sign(KEY, body)}, KEY, store)
    except RuntimeError:
        pass
    assert not store.ids  # Ring's retry will be processed


# --- account linking ---

def test_link_nonce_is_urlsafe_base64_without_padding():
    expected = base64.urlsafe_b64encode(hmac.new(KEY, b"1771130906289:acct-1", hashlib.sha256).digest()).rstrip(b"=")
    assert linking.link_nonce(KEY, 1771130906289, "acct-1") == expected.decode()
    assert "=" not in linking.link_nonce(KEY, 1, "a")


def test_link_time_window():
    assert linking.fresh(1_000_000, now_ms=1_000_000 + 599_000)
    assert not linking.fresh(1_000_000, now_ms=1_000_000 + 601_000)
    assert not linking.fresh(1_000_000, now_ms=999_000)  # future


def test_claim_matches_token_and_completes_link():
    now = 1771130906289
    nonce = linking.link_nonce(KEY, now, "acct-2")
    routes = lambda acct: {("GET", "/v1/users/me"): [(200, {}, {"data": {"type": "users", "id": acct}})],
                           ("POST", "/app-integrations"): [(201, {}, {})],
                           ("PATCH", "/app-integrations"): [(200, {}, {})]}
    (c1, f1), (c2, f2) = client(routes("acct-1")), client(routes("acct-2"))
    assert linking.claim(KEY, nonce, now, "mom@example.com", [c1, c2], now_ms=now + 1000) is c2
    assert [c[0] for c in f1.calls] == ["GET"]
    assert [c[0] for c in f2.calls] == ["GET", "POST", "PATCH"]
    assert json.loads(f2.calls[1][3]) == {"account_identifier": "mom@example.com", "nonce": nonce}
    assert json.loads(f2.calls[2][3]) == {"status": "completed"}
    assert linking.claim(KEY, nonce, now, "x", [c2], now_ms=now + 700_000) is None  # stale


# --- client ---

def test_exchange_and_refresh_send_client_credentials_in_form():
    fake = FakeRing({("POST", "oauth.ring.com"): [TOKEN_OK]})
    t = exchange_code("the-code", "cid", "secret", send=fake)
    assert t.access_token == "new-access"
    assert b"grant_type=authorization_code" in fake.calls[0][3] and b"client_secret=secret" in fake.calls[0][3]

    saved = []
    c, fake = client({("POST", "oauth.ring.com"): [TOKEN_OK], ("GET", "/v1/devices"): [(200, {}, {"data": []})]},
                     expires_in=10, on_refresh=saved.append)
    c.devices()  # token nearly expired -> refresh first
    assert b"refresh_token=old-refresh" in fake.calls[0][3]
    assert fake.calls[1][2]["Authorization"] == "Bearer new-access"
    assert saved[0].refresh_token == "new-refresh"  # rotated token persisted


def test_401_refreshes_once_and_429_honours_retry_after():
    c, fake = client({("POST", "oauth.ring.com"): [TOKEN_OK],
                      ("GET", "/v1/users/me"): [(401, {}, {}), (429, {"Retry-After": "7"}, {}), (200, {}, {"data": {"id": "a"}})]})
    assert c.users_me() == {"data": {"id": "a"}}
    assert [x[0] for x in fake.calls] == ["GET", "POST", "GET", "SLEEP", "GET"]
    assert fake.calls[3][1] == 7.0


def test_devices_joins_included_status_and_capabilities():
    doc = {"data": [{"type": "devices", "id": "d1", "attributes": {"name": "Front Doorbell"},
                     "relationships": {"status": {"data": {"type": "device-status", "id": "d1"}},
                                       "capabilities": {"data": {"type": "device-capabilities", "id": "d1"}}}}],
           "included": [{"type": "device-status", "id": "d1", "attributes": {"online": True}},
                        {"type": "device-capabilities", "id": "d1", "attributes": {"motion_detection": {}}}]}
    c, fake = client({("GET", "/v1/devices"): [(200, {}, doc)]})
    [d] = c.devices()
    assert (d.id, d.name, d.online, list(d.capabilities)) == ("d1", "Front Doorbell", True, ["motion_detection"])
    assert "include=status,capabilities" in fake.calls[0][1]


def test_history_paginates_and_backfills_events():
    now = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)
    ms = lambda h: int((now - timedelta(hours=h)).timestamp() * 1000)
    item = lambda i, kind, h: {"type": "history-events", "id": i, "attributes": {"event_type": kind, "start": ms(h), "end": ms(h)}}
    page1 = {"data": [item("a", "motion", 1), item("b", "ding", 2)],
             "links": {"next": "/v1/history/devices/d1/events?event_types=motion%2Cding&page[key]=x"}}
    page2 = {"data": [item("c", "motion", 3), item("d", "motion", 30)], "links": {"next": "/should-not-fetch"}}
    c, fake = client({("GET", "event_types=motion.human"): [(200, {}, {"data": [item("a", "motion", 1)]})],
                      ("GET", "page[key]=x"): [(200, {}, page2)],
                      ("GET", "event_types=motion%2Cding"): [(200, {}, page1)]})
    events = c.history_events("home-1", "d1", indoor=False, since=now - timedelta(hours=24))
    assert [(e.event_id, e.kind) for e in events] == [("a", "motion_human"), ("b", "ding"), ("c", "motion_other")]
    assert not any("should-not-fetch" in call[1] for call in fake.calls)


def test_snapshot_returns_presigned_location_without_following():
    c, fake = client({("POST", "/media/image/download"): [(303, {"Location": "https://s3/presigned?X-Amz-Expires=300"}, b"")]})
    start = datetime(2026, 10, 19, tzinfo=timezone.utc)
    assert c.snapshot_url("d1", start) == "https://s3/presigned?X-Amz-Expires=300"
    assert json.loads(fake.calls[0][3]) == {"type": "latest_in_range", "start_timestamp": int(start.timestamp() * 1000)}

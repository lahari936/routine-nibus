"""Typed Ring (Amazon Vision) API client: tokens, devices, event history, snapshots, app integration.

Stdlib only, so it runs in Lambda without layers. Server-side only: Ring blocks browser (CORS) calls.
Docs: https://developer.amazon.com/docs/ring/api-documentation.html
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ring_routine_baseline.models import Event

API_BASE = "https://api.amazonvision.com"
TOKEN_URL = "https://oauth.ring.com/oauth/token"

# (method, url, headers, body) -> (status, headers, body). Swappable for tests.
Transport = Callable[[str, str, dict[str, str], bytes | None], tuple[int, dict[str, str], bytes]]


class RingError(Exception):
    def __init__(self, status: int, body: bytes):
        super().__init__(f"Ring API {status}: {body[:500]!r}")
        self.status = status
        self.body = body


@dataclass
class Tokens:
    access_token: str
    refresh_token: str
    expires_at: float  # unix seconds

    @classmethod
    def from_response(cls, data: dict[str, Any]) -> "Tokens":
        return cls(data["access_token"], data["refresh_token"], time.time() + int(data.get("expires_in", 14400)))


@dataclass
class Device:
    id: str
    name: str
    online: bool | None
    capabilities: dict[str, Any]
    raw: dict[str, Any]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # snapshot 303s must not be followed with our Bearer token
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def urllib_transport(method: str, url: str, headers: dict[str, str], body: bytes | None):
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with _opener.open(req, timeout=30) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def _token_request(form: dict[str, str], send: Transport) -> Tokens:
    status, _, body = send("POST", TOKEN_URL, {"Content-Type": "application/x-www-form-urlencoded"},
                           urllib.parse.urlencode(form).encode())
    if status != 200:
        raise RingError(status, body)
    return Tokens.from_response(json.loads(body))


def exchange_code(code: str, client_id: str, client_secret: str, send: Transport = urllib_transport) -> Tokens:
    """One-way linking: Ring posts a code to our Token Exchange URL; exchange it within 60 s."""
    return _token_request({"grant_type": "authorization_code", "code": code,
                           "client_id": client_id, "client_secret": client_secret}, send)


class RingClient:
    def __init__(self, client_id: str, client_secret: str, tokens: Tokens, *,
                 on_refresh: Callable[[Tokens], None] | None = None,
                 base: str = API_BASE, send: Transport = urllib_transport, sleep=time.sleep):
        self.client_id, self.client_secret, self.tokens = client_id, client_secret, tokens
        self.on_refresh, self.base, self.send, self.sleep = on_refresh, base.rstrip("/"), send, sleep

    def refresh(self) -> Tokens:
        # Ring rotates refresh tokens: the old one dies, so the new pair must be persisted (on_refresh).
        self.tokens = _token_request({"grant_type": "refresh_token", "refresh_token": self.tokens.refresh_token,
                                      "client_id": self.client_id, "client_secret": self.client_secret}, self.send)
        if self.on_refresh:
            self.on_refresh(self.tokens)
        return self.tokens

    def request(self, method: str, path: str, body: Any = None, *,
                ok: tuple[int, ...] = (200,)) -> tuple[int, dict[str, str], bytes]:
        if time.time() > self.tokens.expires_at - 60:
            self.refresh()
        url = path if path.startswith("http") else self.base + path
        data = json.dumps(body).encode() if body is not None else None
        refreshed = False
        for attempt in range(5):
            headers = {"Authorization": f"Bearer {self.tokens.access_token}", "Accept": "application/vnd.api+json"}
            if data is not None:
                headers["Content-Type"] = "application/json"
            status, resp_headers, resp = self.send(method, url, headers, data)
            if status in ok:
                return status, resp_headers, resp
            if status == 401 and not refreshed:
                self.refresh()
                refreshed = True
            elif status in (429, 500, 503):
                retry_after = {k.lower(): v for k, v in resp_headers.items()}.get("retry-after")
                self.sleep(float(retry_after) if retry_after else 2 ** attempt)
            else:
                break
        raise RingError(status, resp)

    def get(self, path: str) -> dict[str, Any]:
        return json.loads(self.request("GET", path)[2])

    # --- account ---

    def users_me(self) -> dict[str, Any]:
        return self.get("/v1/users/me")

    def complete_link(self, account_identifier: str, nonce: str) -> None:
        """One-way linking, after the nonce matched: POST (-> awaiting), then PATCH (-> completed). Both required."""
        self.request("POST", "/v1/accounts/me/app-integrations",
                     {"account_identifier": account_identifier, "nonce": nonce}, ok=(200, 201, 204))
        self.request("PATCH", "/v1/accounts/me/app-integrations", {"status": "completed"}, ok=(200, 204))

    # --- devices ---

    def devices(self) -> list[Device]:
        doc = self.get("/v1/devices?include=status,capabilities")
        included = {(i["type"], i["id"]): i.get("attributes", {}) for i in doc.get("included", [])}

        def related(d: dict, name: str) -> dict[str, Any]:
            ref = (d.get("relationships", {}).get(name) or {}).get("data") or {}
            return included.get((ref.get("type"), ref.get("id")), {})

        return [
            Device(id=d["id"], name=d.get("attributes", {}).get("name", d["id"]),
                   online=related(d, "status").get("online"), capabilities=related(d, "capabilities"), raw=d)
            for d in doc.get("data", [])
        ]

    # --- history ---

    def history(self, device_id: str, event_types: str | None = None,
                since: datetime | None = None) -> Iterator[dict[str, Any]]:
        """History items newest first, following links.next until older than `since`."""
        q = urllib.parse.urlencode({"event_types": event_types}) if event_types else ""
        path: str | None = f"/v1/history/devices/{device_id}/events" + (f"?{q}" if q else "")
        cutoff = since.timestamp() * 1000 if since else None
        while path:
            doc = self.get(path)
            for item in doc.get("data", []):
                if cutoff is not None and item["attributes"]["start"] < cutoff:
                    return
                yield item
            path = (doc.get("links") or {}).get("next")

    def history_events(self, home_id: str, device_id: str, indoor: bool, since: datetime) -> list[Event]:
        """Backfill: camera history (motion, ding) as Events. Human motion comes from a second, filtered query."""
        human = {i["id"] for i in self.history(device_id, "motion.human", since)}
        events = []
        for item in self.history(device_id, "motion,ding", since):
            kind = item["attributes"]["event_type"]
            if kind == "motion":
                kind = "motion_human" if item["id"] in human else "motion_other"
            elif kind != "ding":
                continue
            events.append(Event(
                event_id=item["id"], home_id=home_id, device_id=device_id, kind=kind,
                ts=datetime.fromtimestamp(item["attributes"]["start"] / 1000, timezone.utc),
                indoor=indoor and kind != "ding", raw=item,
            ))
        return events

    # --- media ---

    def snapshot_url(self, device_id: str, start: datetime, end: datetime | None = None) -> str:
        """Pre-signed, expiring URL of the latest image in [start, end]. Only call during an active, opted-in alert."""
        body: dict[str, Any] = {"type": "latest_in_range", "start_timestamp": int(start.timestamp() * 1000)}
        if end:
            body["end_timestamp"] = int(end.timestamp() * 1000)
        _, headers, _ = self.request("POST", f"/v1/devices/{device_id}/media/image/download", body, ok=(303,))
        return {k.lower(): v for k, v in headers.items()}["location"]

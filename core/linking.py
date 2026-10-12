"""One-way (Ring-driven) account linking.

1. Ring posts an authorization code to our Token Exchange URL -> exchange_code() within 60 s,
   keep the tokens as "unclaimed".
2. The user lands on our Account Link URL with ?nonce=...&time=... (time in ms).
3. For each unclaimed token: GET /v1/users/me -> account_id, recompute the nonce, compare.
4. On a match: POST then PATCH /v1/accounts/me/app-integrations (RingClient.complete_link).
"""
import base64
import hashlib
import hmac
import time
from collections.abc import Callable, Iterable

from core.ring_client import RingClient

MAX_AGE_MS = 600_000


def link_nonce(key: bytes, time_ms: int | str, account_id: str) -> str:
    """HMAC-SHA256 over "<time>:<account_id>", URL-safe Base64 without padding (webhooks use hex instead)."""
    mac = hmac.new(key, f"{time_ms}:{account_id}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).rstrip(b"=").decode()


def fresh(time_ms: int | str, now_ms: float | None = None) -> bool:
    now_ms = time.time() * 1000 if now_ms is None else now_ms
    return 0 <= now_ms - int(time_ms) <= MAX_AGE_MS


def account_id_of(me: dict) -> str:
    # The docs don't show the /v1/users/me body; accept JSON:API (data.id) or a flat id.
    data = me.get("data", me)
    return data.get("id") or data.get("attributes", {}).get("account_id")


def claim(key: bytes, nonce: str, time_ms: int | str, account_identifier: str,
          unclaimed: Iterable[RingClient], now_ms: float | None = None,
          on_claimed: Callable[[str, RingClient], None] | None = None) -> RingClient | None:
    """Match the link request to an unclaimed token and complete the link. None if nothing matches."""
    if not fresh(time_ms, now_ms):
        return None
    for client in unclaimed:
        account_id = account_id_of(client.users_me())
        if account_id and hmac.compare_digest(link_nonce(key, time_ms, account_id), nonce):
            client.complete_link(account_identifier, nonce)
            if on_claimed:
                on_claimed(account_id, client)
            return client
    return None

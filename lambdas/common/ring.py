"""Ring app credentials (Secrets Manager) and per-account API clients."""
import json
import os
from dataclasses import asdict
from functools import cache

import boto3

from ring_routine_baseline.ring_client import API_BASE, RingClient, Tokens
from lambdas.common.store import DynamoStore


@cache
def app_secret() -> dict[str, str]:
    """{"client_id", "client_secret", "hmac_key"}: shown once at app registration, so they live only here."""
    return json.loads(boto3.client("secretsmanager").get_secret_value(SecretId=os.environ["RING_SECRET_ARN"])["SecretString"])


def hmac_key() -> bytes:
    return app_secret()["hmac_key"].encode()


def client_from_tokens(tokens: dict, on_refresh=None) -> RingClient:
    s = app_secret()
    return RingClient(s["client_id"], s["client_secret"], Tokens(**tokens), on_refresh=on_refresh,
                      base=os.environ.get("RING_API_BASE", API_BASE))


def client_for_account(store: DynamoStore, account_id: str) -> RingClient:
    acct = store.get(f"ACCT#{account_id}", "META")

    def persist(t: Tokens) -> None:  # refresh tokens rotate: the old one is dead once this returns
        # ponytail: last-writer-wins; two Lambdas refreshing the same account at once can lose a token.
        # Add a conditional write on the old refresh token if that ever happens.
        store.put(f"ACCT#{account_id}", "META", {**acct, "tokens": asdict(t)})

    return client_from_tokens(acct["tokens"], persist)

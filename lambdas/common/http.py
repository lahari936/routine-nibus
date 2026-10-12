"""API Gateway HTTP API (payload v2) helpers."""
import base64
import html
import json
import urllib.parse
from typing import Any


def raw_body(event: dict) -> bytes:
    body = event.get("body") or ""
    return base64.b64decode(body) if event.get("isBase64Encoded") else body.encode()


def form(event: dict) -> dict[str, str]:
    body = raw_body(event)
    try:
        data = json.loads(body)
        return {k: str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except ValueError:
        return {k: v[0] for k, v in urllib.parse.parse_qs(body.decode()).items()}


def query(event: dict) -> dict[str, str]:
    return event.get("queryStringParameters") or {}


def page(status: int, title: str, body_html: str) -> dict[str, Any]:
    doc = (f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
           f"<title>{html.escape(title)}</title>"
           f"<body style='font:18px system-ui;max-width:32rem;margin:3rem auto;padding:0 1rem'>"
           f"<h1>{html.escape(title)}</h1>{body_html}</body>")
    return {"statusCode": status, "headers": {"Content-Type": "text/html; charset=utf-8"}, "body": doc}


def as_json(status: int, data: Any) -> dict[str, Any]:
    return {"statusCode": status, "headers": {"Content-Type": "application/json"}, "body": json.dumps(data, default=str)}

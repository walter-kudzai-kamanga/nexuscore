from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any, Dict

SECRET = os.getenv("NEXUS_SECRET_KEY", "dev-secret-change-me")
SERVICE_API_KEY = os.getenv("NEXUS_SERVICE_API_KEY", "nexus-service-key")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def _sign(payload: str) -> str:
    return _b64url(hmac.new(SECRET.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest())


def create_token(subject: str, role: str, expires_in_seconds: int = 3600) -> str:
    body = {"sub": subject, "role": role, "exp": int(time.time()) + expires_in_seconds}
    payload = _b64url(json.dumps(body, separators=(",", ":")).encode("utf-8"))
    signature = _sign(payload)
    return f"{payload}.{signature}"


def verify_token(token: str) -> Dict[str, Any]:
    try:
        payload, signature = token.split(".", maxsplit=1)
        expected_signature = _sign(payload)
        if not hmac.compare_digest(signature, expected_signature):
            raise ValueError("Invalid token signature")
        decoded = json.loads(base64.urlsafe_b64decode(payload + "==="))
        if int(decoded.get("exp", 0)) < int(time.time()):
            raise ValueError("Token expired")
        return decoded
    except Exception as exc:  # pragma: no cover - defensive fallback
        raise ValueError(str(exc)) from exc


def verify_service_api_key(api_key: str | None) -> bool:
    if not api_key:
        return False
    return hmac.compare_digest(api_key, SERVICE_API_KEY)


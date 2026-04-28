from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
import uuid
from typing import Any, Dict

import jwt
from fastapi import Depends, HTTPException, Request

from app.core.config import require_env_or_file

SECRET = require_env_or_file("NEXUS_SECRET_KEY")
SERVICE_API_KEY = require_env_or_file("NEXUS_SERVICE_API_KEY")
ACCESS_TTL_SECONDS = int(os.getenv("NEXUS_ACCESS_TTL_SECONDS", "900"))
REFRESH_TTL_SECONDS = int(os.getenv("NEXUS_REFRESH_TTL_SECONDS", "604800"))
ALGORITHM = "HS256"
COOKIE_SECURE = os.getenv("NEXUS_COOKIE_SECURE", "true").lower() == "true"
CSRF_HEADER = "x-csrf-token"


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return f"{salt.hex()}:{digest.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        salt_hex, digest_hex = password_hash.split(":", maxsplit=1)
        expected = bytes.fromhex(digest_hex)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), 200_000)
        return hmac.compare_digest(digest, expected)
    except ValueError:
        return False


def _create_token(subject: str, role: str, token_type: str, expires_in_seconds: int) -> str:
    now = int(time.time())
    payload = {
        "sub": subject,
        "role": role,
        "type": token_type,
        "iat": now,
        "exp": now + expires_in_seconds,
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, SECRET, algorithm=ALGORITHM)


def create_access_token(subject: str, role: str) -> str:
    return _create_token(subject, role, "access", ACCESS_TTL_SECONDS)


def create_refresh_token(subject: str, role: str) -> str:
    return _create_token(subject, role, "refresh", REFRESH_TTL_SECONDS)


def verify_token(token: str, expected_type: str | None = None) -> Dict[str, Any]:
    try:
        decoded = jwt.decode(token, SECRET, algorithms=[ALGORITHM])
        token_type = decoded.get("type")
        if expected_type and token_type != expected_type:
            raise ValueError("Invalid token type")
        return decoded
    except jwt.PyJWTError as exc:
        raise ValueError(str(exc)) from exc


def verify_service_api_key(api_key: str | None) -> bool:
    if not api_key:
        return False
    return hmac.compare_digest(api_key, SERVICE_API_KEY)


def _extract_bearer_from_request(request: Request) -> str | None:
    auth = request.headers.get("Authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth.split(" ", maxsplit=1)[1]
    return request.cookies.get("nexus_access_token")


def requires_csrf(request: Request) -> bool:
    if request.method.upper() in {"GET", "HEAD", "OPTIONS"}:
        return False
    has_bearer = bool(request.headers.get("Authorization", "").lower().startswith("bearer "))
    if has_bearer:
        return False
    return bool(request.cookies.get("nexus_access_token") or request.cookies.get("nexus_refresh_token"))


def enforce_csrf(request: Request) -> None:
    if not requires_csrf(request):
        return
    csrf_cookie = request.cookies.get("nexus_csrf_token", "")
    csrf_header = request.headers.get(CSRF_HEADER, "")
    if not csrf_cookie or not csrf_header or not hmac.compare_digest(csrf_cookie, csrf_header):
        raise HTTPException(status_code=403, detail="CSRF validation failed")


def create_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def current_identity(request: Request) -> Dict[str, Any]:
    token = _extract_bearer_from_request(request)
    if not token:
        raise HTTPException(status_code=401, detail="Missing access token")
    try:
        return verify_token(token, expected_type="access")
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def require_roles(*roles: str):
    def _dependency(identity: Dict[str, Any] = Depends(current_identity)) -> Dict[str, Any]:
        role = identity.get("role")
        if role not in roles:
            raise HTTPException(status_code=403, detail="Insufficient role permissions")
        return identity

    return _dependency


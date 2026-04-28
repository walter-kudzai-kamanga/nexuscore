from __future__ import annotations

import hashlib
import hmac
import os
import time
import uuid
from typing import Any, Dict

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer

SECRET = os.getenv("NEXUS_SECRET_KEY", "dev-secret-change-me-super-long")
SERVICE_API_KEY = os.getenv("NEXUS_SERVICE_API_KEY", "nexus-service-key")
ACCESS_TTL_SECONDS = int(os.getenv("NEXUS_ACCESS_TTL_SECONDS", "900"))
REFRESH_TTL_SECONDS = int(os.getenv("NEXUS_REFRESH_TTL_SECONDS", "604800"))
ALGORITHM = "HS256"
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")


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


def current_identity(token: str = Depends(oauth2_scheme)) -> Dict[str, Any]:
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


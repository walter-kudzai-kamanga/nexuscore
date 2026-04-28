from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, Header, HTTPException
from fastapi.security import OAuth2PasswordRequestForm

from app.core.security import (
    REFRESH_TTL_SECONDS,
    create_access_token,
    create_refresh_token,
    current_identity,
    hash_password,
    verify_password,
    verify_token,
)
from app.services.state_store import store

router = APIRouter()


@router.post("/auth/login")
def login(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    username = payload.get("username", "")
    password = payload.get("password", "")
    user = store.get_user(username)
    if not user or not verify_password(password, user["password_hash"]) or not user["is_active"]:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    access_token = create_access_token(username, user["role"])
    refresh_token = create_refresh_token(username, user["role"])
    refresh_claims = verify_token(refresh_token, expected_type="refresh")
    store.save_refresh_token(
        refresh_claims["jti"],
        username,
        hashlib.sha256(refresh_token.encode("utf-8")).hexdigest(),
        (datetime.now(timezone.utc) + timedelta(seconds=REFRESH_TTL_SECONDS)).isoformat(),
    )
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "role": user["role"],
        "expires_in": 900,
    }


@router.post("/auth/token")
def token(form_data: OAuth2PasswordRequestForm = Depends()) -> Dict[str, Any]:
    return login({"username": form_data.username, "password": form_data.password})


@router.post("/auth/refresh")
def refresh(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    refresh_token = payload.get("refresh_token", "")
    if not refresh_token:
        raise HTTPException(status_code=400, detail="refresh_token is required")
    try:
        claims = verify_token(refresh_token, expected_type="refresh")
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    token_hash = hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()
    if not store.is_refresh_token_active(claims["jti"], token_hash):
        raise HTTPException(status_code=401, detail="Refresh token is revoked or expired")
    username = claims["sub"]
    user = store.get_user(username)
    if not user or not user["is_active"]:
        raise HTTPException(status_code=401, detail="User disabled")
    store.revoke_refresh_token(claims["jti"])
    access_token = create_access_token(username, user["role"])
    next_refresh = create_refresh_token(username, user["role"])
    next_claims = verify_token(next_refresh, expected_type="refresh")
    store.save_refresh_token(
        next_claims["jti"],
        username,
        hashlib.sha256(next_refresh.encode("utf-8")).hexdigest(),
        (datetime.now(timezone.utc) + timedelta(seconds=REFRESH_TTL_SECONDS)).isoformat(),
    )
    return {"access_token": access_token, "refresh_token": next_refresh, "token_type": "bearer", "expires_in": 900}


@router.post("/auth/logout")
def logout(identity: Dict[str, Any] = Depends(current_identity)) -> Dict[str, Any]:
    store.revoke_user_refresh_tokens(identity["sub"])
    return {"ok": True}


@router.get("/auth/me")
def me(authorization: str | None = Header(default=None), identity: Dict[str, Any] = Depends(current_identity)) -> Dict[str, Any]:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    return {"user": identity.get("sub"), "role": identity.get("role"), "exp": identity.get("exp")}


@router.post("/auth/bootstrap")
def bootstrap_users() -> Dict[str, Any]:
    defaults = {
        "admin": ("admin123", "admin"),
        "operator": ("operator123", "operator"),
        "developer": ("developer123", "developer"),
    }
    for username, (password, role) in defaults.items():
        store.upsert_user(username, hash_password(password), role, is_active=True)
    return {"ok": True, "users": list(defaults.keys())}


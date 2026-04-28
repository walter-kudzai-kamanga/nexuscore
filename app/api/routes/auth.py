from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from fastapi.security import OAuth2PasswordRequestForm

from app.core.config import get_env_or_file
from app.core.security import (
    ACCESS_TTL_SECONDS,
    COOKIE_SECURE,
    REFRESH_TTL_SECONDS,
    create_access_token,
    create_csrf_token,
    create_refresh_token,
    current_identity,
    enforce_csrf,
    hash_password,
    require_roles,
    verify_password,
    verify_token,
)
from app.services.state_store import store

router = APIRouter()


def _set_auth_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    csrf_token = create_csrf_token()
    response.set_cookie(
        key="nexus_access_token",
        value=access_token,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="strict",
        max_age=ACCESS_TTL_SECONDS,
        path="/",
    )
    response.set_cookie(
        key="nexus_csrf_token",
        value=csrf_token,
        httponly=False,
        secure=COOKIE_SECURE,
        samesite="strict",
        max_age=REFRESH_TTL_SECONDS,
        path="/",
    )
    response.set_cookie(
        key="nexus_refresh_token",
        value=refresh_token,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="strict",
        max_age=REFRESH_TTL_SECONDS,
        path="/api/auth",
    )


@router.post("/auth/login")
def login(payload: Dict[str, Any] = Body(...), response: Response = None) -> Dict[str, Any]:
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
    if response is not None:
        _set_auth_cookies(response, access_token, refresh_token)
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "role": user["role"],
        "expires_in": 900,
    }


@router.post("/auth/token")
def token(form_data: OAuth2PasswordRequestForm = Depends(), response: Response = None) -> Dict[str, Any]:
    return login({"username": form_data.username, "password": form_data.password}, response=response)


@router.post("/auth/refresh")
def refresh(
    payload: Dict[str, Any] | None = Body(default=None),
    request: Request = None,
    response: Response = None,
) -> Dict[str, Any]:
    if request is not None:
        enforce_csrf(request)
    body = payload or {}
    refresh_token = body.get("refresh_token") or (request.cookies.get("nexus_refresh_token") if request else "")
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
    if response is not None:
        _set_auth_cookies(response, access_token, next_refresh)
    return {"access_token": access_token, "refresh_token": next_refresh, "token_type": "bearer", "expires_in": 900}


@router.post("/auth/logout")
def logout(request: Request, response: Response, identity: Dict[str, Any] = Depends(current_identity)) -> Dict[str, Any]:
    enforce_csrf(request)
    store.revoke_user_refresh_tokens(identity["sub"])
    response.delete_cookie("nexus_access_token", path="/")
    response.delete_cookie("nexus_refresh_token", path="/api/auth")
    response.delete_cookie("nexus_csrf_token", path="/")
    return {"ok": True}


@router.get("/auth/me")
def me(identity: Dict[str, Any] = Depends(current_identity)) -> Dict[str, Any]:
    return {"user": identity.get("sub"), "role": identity.get("role"), "exp": identity.get("exp")}


@router.post("/auth/bootstrap")
def bootstrap_users(
    request: Request,
    _: Dict[str, Any] = Depends(require_roles("admin")),
) -> Dict[str, Any]:
    enforce_csrf(request)
    raw = get_env_or_file("NEXUS_BOOTSTRAP_USERS_JSON", default="{}") or "{}"
    try:
        users = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"Invalid NEXUS_BOOTSTRAP_USERS_JSON: {exc}") from exc
    created: list[str] = []
    for username, config in users.items():
        password = config.get("password")
        role = config.get("role")
        if not password or not role:
            continue
        store.upsert_user(username, hash_password(password), role, is_active=True)
        created.append(username)
    return {"ok": True, "users": created}


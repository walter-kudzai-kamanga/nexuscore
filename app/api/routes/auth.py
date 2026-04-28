from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Body, Header, HTTPException

from app.core.security import create_token, verify_token

router = APIRouter()

USERS = {
    "admin": {"password": "admin123", "role": "admin"},
    "operator": {"password": "operator123", "role": "operator"},
    "developer": {"password": "developer123", "role": "developer"},
}


@router.post("/auth/login")
def login(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    username = payload.get("username", "")
    password = payload.get("password", "")
    user = USERS.get(username)
    if not user or user["password"] != password:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_token(username, user["role"])
    return {"access_token": token, "token_type": "bearer", "role": user["role"]}


@router.get("/auth/me")
def me(authorization: str | None = Header(default=None)) -> Dict[str, Any]:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.split(" ", maxsplit=1)[1]
    try:
        decoded = verify_token(token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return {"user": decoded.get("sub"), "role": decoded.get("role"), "exp": decoded.get("exp")}


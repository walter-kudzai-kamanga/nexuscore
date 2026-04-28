from __future__ import annotations

from typing import Any, Dict

from fastapi import Depends, Header, HTTPException

from app.core.security import verify_token
from app.services.saas_platform import verify_tenant_api_key


def tenant_identity(
    x_tenant_id: str | None = Header(default=None),
    x_tenant_api_key: str | None = Header(default=None, convert_underscores=False),
    authorization: str | None = Header(default=None),
) -> Dict[str, Any]:
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", maxsplit=1)[1]
        try:
            claims = verify_token(token, expected_type="tenant_access")
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        tenant_id = claims.get("tenant_id") or claims.get("sub")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Invalid tenant token")
        return {"tenant_id": tenant_id, "auth_mode": "jwt", "claims": claims}

    if not x_tenant_id or not x_tenant_api_key:
        raise HTTPException(status_code=401, detail="Missing tenant credentials")
    if not verify_tenant_api_key(x_tenant_id, x_tenant_api_key):
        raise HTTPException(status_code=401, detail="Invalid tenant API key")
    return {"tenant_id": x_tenant_id, "auth_mode": "api_key", "claims": {"tenant_id": x_tenant_id}}


def require_tenant_scope(*scopes: str):
    def _dependency(identity: Dict[str, Any] = Depends(tenant_identity)) -> Dict[str, Any]:
        claims = identity.get("claims", {})
        token_scopes = set(claims.get("scopes", scopes))
        if scopes and not set(scopes).issubset(token_scopes):
            raise HTTPException(status_code=403, detail="Insufficient tenant scope")
        return identity

    return _dependency


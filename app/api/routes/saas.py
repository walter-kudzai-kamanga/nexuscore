from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request

from app.core.security import enforce_csrf, require_roles
from app.services.saas_platform import (
    billing_overview,
    compliance_report,
    create_tenant,
    evaluate_guarded_action,
    execute_transaction_task,
    export_evidence,
    list_tenants,
    queue_transaction_workflow,
    register_connector,
    upsert_policy,
)

router = APIRouter()


@router.post("/tenants")
def create_tenant_endpoint(
    request: Request,
    payload: Dict[str, Any] = Body(...),
    _: Dict[str, Any] = Depends(require_roles("admin")),
) -> Dict[str, Any]:
    enforce_csrf(request)
    tenant_id = payload.get("tenant_id")
    name = payload.get("name")
    plan = payload.get("plan", "starter")
    if not tenant_id or not name:
        raise HTTPException(status_code=400, detail="tenant_id and name are required")
    return create_tenant(tenant_id, name, plan)


@router.get("/tenants")
def list_tenants_endpoint(_: Dict[str, Any] = Depends(require_roles("admin", "operator"))) -> Dict[str, Any]:
    return {"items": list_tenants()}


@router.post("/connectors/register")
def register_connector_endpoint(
    request: Request,
    payload: Dict[str, Any] = Body(...),
    _: Dict[str, Any] = Depends(require_roles("admin", "operator")),
) -> Dict[str, Any]:
    enforce_csrf(request)
    try:
        return register_connector(
            payload["tenant_id"],
            payload["name"],
            payload.get("connector_type", "rest"),
            payload.get("config", {}),
        )
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"Missing required field: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/policies/upsert")
def upsert_policy_endpoint(
    request: Request,
    payload: Dict[str, Any] = Body(...),
    _: Dict[str, Any] = Depends(require_roles("admin")),
) -> Dict[str, Any]:
    enforce_csrf(request)
    tenant_id = payload.get("tenant_id")
    policy = payload.get("policy", {})
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id is required")
    return upsert_policy(tenant_id, policy)


@router.post("/policies/evaluate")
def evaluate_policy_endpoint(
    payload: Dict[str, Any] = Body(...),
    _: Dict[str, Any] = Depends(require_roles("admin", "operator")),
) -> Dict[str, Any]:
    tenant_id = payload.get("tenant_id", "default")
    action = payload.get("action", "restart")
    severity = payload.get("severity", "warning")
    context = payload.get("context", {})
    return evaluate_guarded_action(tenant_id, action, severity, context)


@router.post("/transactions/heal")
def queue_transaction_heal_endpoint(
    request: Request,
    payload: Dict[str, Any] = Body(...),
    _: Dict[str, Any] = Depends(require_roles("admin", "operator")),
) -> Dict[str, Any]:
    enforce_csrf(request)
    try:
        return queue_transaction_workflow(
            payload["tenant_id"],
            payload["workflow_type"],
            payload["reference_id"],
            payload.get("payload", {}),
        )
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"Missing required field: {exc}") from exc


@router.post("/transactions/run")
def run_transaction_heal_endpoint(
    request: Request,
    payload: Dict[str, Any] = Body(...),
    _: Dict[str, Any] = Depends(require_roles("admin", "operator")),
) -> Dict[str, Any]:
    enforce_csrf(request)
    task_id = payload.get("task_id")
    if not task_id:
        raise HTTPException(status_code=400, detail="task_id is required")
    try:
        return execute_transaction_task(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/compliance/report/{tenant_id}")
def compliance_report_endpoint(tenant_id: str, _: Dict[str, Any] = Depends(require_roles("admin", "operator"))) -> Dict[str, Any]:
    return compliance_report(tenant_id)


@router.get("/compliance/evidence/{tenant_id}")
def evidence_endpoint(
    tenant_id: str,
    limit: int = Query(default=500, ge=1, le=5000),
    _: Dict[str, Any] = Depends(require_roles("admin", "operator")),
) -> Dict[str, Any]:
    return export_evidence(tenant_id, limit=limit)


@router.get("/billing/{tenant_id}")
def billing_endpoint(tenant_id: str, _: Dict[str, Any] = Depends(require_roles("admin", "operator"))) -> Dict[str, Any]:
    return billing_overview(tenant_id)


@router.get("/architecture/blueprint")
def architecture_blueprint(_: Dict[str, Any] = Depends(require_roles("admin", "operator", "developer"))) -> Dict[str, Any]:
    return {
        "control_plane": ["tenant management", "policy engine", "billing", "compliance"],
        "data_plane": ["connector runtime", "healing execution workers", "event pipelines"],
        "tenant_model": "strict logical isolation with per-tenant keys and quotas",
        "connector_runtime": ["REST", "gRPC", "Kafka", "DB jobs", "legacy batch adapters"],
        "healing_policy_dsl": {
            "autonomy_mode": ["manual", "guarded", "auto"],
            "requires_approval_for": ["restart", "reroute", "compensate"],
            "deny_actions_for_severity": {"critical": ["reroute"]},
        },
        "pricing_model": {
            "base": "per protected service",
            "usage": "per healed transaction / workflow",
            "enterprise": "private deployment + compliance pack",
        },
    }


from __future__ import annotations

import asyncio
import json
from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.core.kafka_producer import TOPICS, kafka_observability_metrics, send_event
from app.core.security import require_roles, verify_service_api_key, verify_token
from app.services.healing_engine import evaluate_health
from app.services.registry import (
    detect_offline_services,
    get_all_services,
    get_dashboard_state,
    get_recent_alerts,
    get_recent_healing,
    get_recent_logs,
    get_recent_metrics,
    get_state,
    register_service,
)
from app.services.state_store import store

router = APIRouter()


@router.get("/services")
def services(_: Dict[str, Any] = Depends(require_roles("admin", "operator", "developer"))) -> Dict[str, Dict[str, Any]]:
    return get_all_services()


@router.get("/services/{service_name}")
def service(
    service_name: str,
    _: Dict[str, Any] = Depends(require_roles("admin", "operator", "developer")),
) -> Dict[str, Any]:
    state = get_state(service_name)
    if not state:
        raise HTTPException(status_code=404, detail="Service not found")
    return state


@router.post("/services/register")
def register(
    payload: Dict[str, Any] = Body(...),
    x_api_key: str | None = Header(default=None, convert_underscores=False),
) -> Dict[str, Any]:
    service_name = payload.get("service") or payload.get("name")
    if not service_name:
        raise HTTPException(status_code=400, detail="service is required")
    if not verify_service_api_key(x_api_key):
        raise HTTPException(status_code=401, detail="Invalid service API key")
    record = register_service(service_name, payload)
    return {"ok": True, "service": record}


@router.post("/services/heartbeat")
async def heartbeat(
    payload: Dict[str, Any] = Body(...),
    x_api_key: str | None = Header(default=None, convert_underscores=False),
) -> Dict[str, Any]:
    service_name = payload.get("service") or payload.get("name")
    if not service_name:
        raise HTTPException(status_code=400, detail="service is required")
    if not verify_service_api_key(x_api_key):
        raise HTTPException(status_code=401, detail="Invalid service API key")
    payload["service"] = service_name
    record = await evaluate_health(payload)
    await send_event(TOPICS["service_health"], {"type": "service.heartbeat", "payload": record})
    await store.events.publish("service.heartbeat", record)
    return {"ok": True, "service": record}


@router.get("/services/offline/check")
async def check_offline(_: Dict[str, Any] = Depends(require_roles("admin", "operator"))) -> Dict[str, Any]:
    changed = detect_offline_services()
    for service in changed:
        await store.events.publish("service.offline", service)
    return {"ok": True, "changed": changed}


@router.get("/dashboard/state")
async def dashboard_state(_: Dict[str, Any] = Depends(require_roles("admin", "operator", "developer"))) -> Dict[str, Any]:
    snapshot = get_dashboard_state()
    try:
        kafka_metrics = await kafka_observability_metrics()
        snapshot["kafka"] = kafka_metrics
    except Exception:
        pass
    return snapshot


@router.get("/metrics")
def metrics(
    service_name: str | None = Query(default=None),
    limit: int = Query(default=60, ge=1, le=500),
    _: Dict[str, Any] = Depends(require_roles("admin", "operator", "developer")),
) -> Dict[str, Any]:
    return {"items": get_recent_metrics(service_name=service_name, limit=limit)}


@router.get("/alerts")
def alerts(
    limit: int = Query(default=25, ge=1, le=200),
    _: Dict[str, Any] = Depends(require_roles("admin", "operator", "developer")),
) -> Dict[str, Any]:
    return {"items": get_recent_alerts(limit=limit)}


@router.get("/healing/history")
def healing_history(
    limit: int = Query(default=25, ge=1, le=200),
    _: Dict[str, Any] = Depends(require_roles("admin", "operator", "developer")),
) -> Dict[str, Any]:
    return {"items": get_recent_healing(limit=limit)}


@router.get("/logs")
def logs(
    limit: int = Query(default=25, ge=1, le=200),
    _: Dict[str, Any] = Depends(require_roles("admin", "operator", "developer")),
) -> Dict[str, Any]:
    return {"items": get_recent_logs(limit=limit)}


@router.get("/events/stream")
async def event_stream(access_token: str = Query(...)) -> StreamingResponse:
    try:
        identity = verify_token(access_token, expected_type="access")
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    if identity.get("role") not in {"admin", "operator", "developer"}:
        raise HTTPException(status_code=403, detail="Insufficient role permissions")

    async def generator():
        with store.events.subscribe() as queue:
            snapshot = get_dashboard_state()
            yield f"event: snapshot\ndata: {json.dumps(snapshot)}\n\n"
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"event: {message.event}\ndata: {json.dumps(message.payload)}\n\n"
                except asyncio.TimeoutError:
                    yield "event: ping\ndata: {}\n\n"

    return StreamingResponse(generator(), media_type="text/event-stream")

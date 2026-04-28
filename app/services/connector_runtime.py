from __future__ import annotations

import asyncio
import json
from typing import Any, Dict

import httpx

from app.services.saas_platform import append_audit, record_usage
from app.services.state_store import store, utc_iso


def get_connector(connector_id: str, tenant_id: str) -> Dict[str, Any] | None:
    return store._fetchone(
        "SELECT * FROM connector_registry WHERE connector_id = :connector_id AND tenant_id = :tenant_id AND status = 'active'",
        {"connector_id": connector_id, "tenant_id": tenant_id},
    )


async def _execute_http_connector(connector: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    config = json.loads(connector["config_json"])
    endpoint = config.get("endpoint")
    method = config.get("method", "POST").upper()
    timeout = float(config.get("timeout_seconds", 8))
    headers = config.get("headers", {})
    if not endpoint:
        raise ValueError("HTTP connector requires endpoint")
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.request(method, endpoint, json=payload, headers=headers)
    return {
        "status_code": response.status_code,
        "body": response.text[:2000],
        "endpoint": endpoint,
        "method": method,
    }


async def _execute_webhook_connector(connector: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    return await _execute_http_connector(connector, payload)


async def _execute_grpc_connector(connector: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    await asyncio.sleep(0.05)
    config = json.loads(connector["config_json"])
    return {
        "status": "simulated",
        "transport": "grpc",
        "target": config.get("target"),
        "method": config.get("rpc_method"),
        "payload_size": len(json.dumps(payload)),
    }


async def execute_connector_action(tenant_id: str, connector_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    connector = get_connector(connector_id, tenant_id)
    if not connector:
        raise ValueError("Connector not found")
    connector_type = connector["connector_type"].lower()
    if connector_type in {"rest", "http"}:
        result = await _execute_http_connector(connector, payload)
    elif connector_type == "webhook":
        result = await _execute_webhook_connector(connector, payload)
    elif connector_type == "grpc":
        result = await _execute_grpc_connector(connector, payload)
    else:
        raise ValueError(f"Unsupported connector_type: {connector_type}")

    append_audit(
        tenant_id,
        "connector-runtime",
        "connector.executed",
        {"connector_id": connector_id, "connector_type": connector_type, "result_meta": {"status": result.get("status_code", result.get("status"))}},
    )
    record_usage(tenant_id, "connector_execution", 1, {"connector_id": connector_id, "connector_type": connector_type, "ts": utc_iso()})
    return {"connector_id": connector_id, "tenant_id": tenant_id, "result": result}


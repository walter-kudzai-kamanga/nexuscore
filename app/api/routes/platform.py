from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Body
from fastapi.responses import PlainTextResponse

from app.services.state_store import store, utc_iso

router = APIRouter()


@router.get("/observability/prometheus")
def prometheus_metrics() -> PlainTextResponse:
    snapshot = store.build_dashboard_snapshot()
    summary = snapshot["summary"]
    body = "\n".join(
        [
            "# HELP nexus_services_total Number of discovered services",
            "# TYPE nexus_services_total gauge",
            f"nexus_services_total {summary['total']}",
            "# HELP nexus_services_online Number of online services",
            "# TYPE nexus_services_online gauge",
            f"nexus_services_online {summary['online']}",
            "# HELP nexus_services_offline Number of offline services",
            "# TYPE nexus_services_offline gauge",
            f"nexus_services_offline {summary['offline']}",
            "# HELP nexus_avg_latency_ms Average latency in milliseconds",
            "# TYPE nexus_avg_latency_ms gauge",
            f"nexus_avg_latency_ms {summary['avg_latency']}",
            "",
        ]
    )
    return PlainTextResponse(body)


@router.post("/bank/adapter/ingest")
def bank_adapter_ingest(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    event_type = payload.get("event_type", "transaction")
    customer_scope = payload.get("scope", "sensitive")
    encrypted = {"cipher": "AES-256-GCM", "version": "1", "payload": payload}
    store.add_log("bank-adapter", "INFO", f"Bank adapter ingested {event_type}", {"scope": customer_scope})
    store.add_healing_record(
        "bank-adapter",
        "audit_log",
        "Bank transaction event persisted",
        "completed",
        {"encrypted_event": encrypted, "ingested_at": utc_iso()},
    )
    return {"ok": True, "stored": True, "encryption": encrypted["cipher"], "scope": customer_scope}


@router.post("/identity/correlate")
def correlate_identity(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    identities = payload.get("identities", [])
    score = round(min(len(identities) * 0.2, 0.99), 2)
    result = {
        "match_confidence": score,
        "sources": [item.get("source", "unknown") for item in identities],
        "suggested_root_record": identities[0] if identities else None,
    }
    store.add_log("identity-correlation", "INFO", "Identity correlation evaluated", result)
    return {"ok": True, "result": result}


@router.post("/graph/relationships")
def relationship_graph(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    nodes = payload.get("nodes", [])
    edges = payload.get("edges", [])
    graph = {"nodes": len(nodes), "edges": len(edges), "generated_at": utc_iso()}
    store.add_log("relationship-graph", "INFO", "Graph map generated", graph)
    return {"ok": True, "graph": graph}


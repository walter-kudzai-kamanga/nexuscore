from __future__ import annotations

import asyncio
import copy
import os
import time
from typing import Any, Dict

from app.services.healing_engine import evaluate_health
from app.services.state_store import store

DEMO_HEALTH_HOLDS: dict[str, float] = {}

DEMO_SERVICES: dict[str, Dict[str, Any]] = {
    "auth-service": {
        "service": "auth-service",
        "status": "online",
        "cpu": 18,
        "memory": 34,
        "latency": 22,
        "error_rate": 0.2,
        "version": "1.0.0",
        "instances": 2,
        "metadata": {"domain": "identity", "protocol": "self-healing-v1", "tenant_id": "default"},
    },
    "payments-service": {
        "service": "payments-service",
        "status": "online",
        "cpu": 42,
        "memory": 58,
        "latency": 120,
        "error_rate": 1.4,
        "version": "1.0.0",
        "instances": 2,
        "metadata": {"domain": "payments", "protocol": "self-healing-v1", "tenant_id": "default"},
    },
    "transactions-service": {
        "service": "transactions-service",
        "status": "online",
        "cpu": 29,
        "memory": 40,
        "latency": 54,
        "error_rate": 0.3,
        "version": "1.0.0",
        "instances": 2,
        "metadata": {"domain": "ledger", "protocol": "self-healing-v1", "tenant_id": "default"},
    },
    "notification-service": {
        "service": "notification-service",
        "status": "online",
        "cpu": 14,
        "memory": 24,
        "latency": 18,
        "error_rate": 0.1,
        "version": "1.0.0",
        "instances": 1,
        "metadata": {"domain": "messaging", "protocol": "self-healing-v1", "tenant_id": "default"},
    },
    "user-service": {
        "service": "user-service",
        "status": "online",
        "cpu": 20,
        "memory": 28,
        "latency": 30,
        "error_rate": 0.2,
        "version": "1.0.0",
        "instances": 2,
        "metadata": {"domain": "users", "protocol": "self-healing-v1", "tenant_id": "default"},
    },
}

DEMO_SCENARIOS: dict[str, Dict[str, Any]] = {
    "crash": {
        "title": "POD CRASH",
        "service": "auth-service",
        "response": "Heartbeat loss simulated. Automatic restart should appear in the remediation feed.",
        "payload": {"status": "offline", "cpu": 0, "memory": 0, "latency": 0, "error_rate": 100},
    },
    "latency": {
        "title": "LATENCY SPIKE",
        "service": "payments-service",
        "response": "Latency spike injected. The healing engine should queue a traffic reroute.",
        "payload": {"cpu": 48, "memory": 62, "latency": 900, "error_rate": 2.4},
    },
    "memleak": {
        "title": "MEMORY LEAK",
        "service": "notification-service",
        "response": "Memory pressure injected. The healing engine should queue a restart.",
        "payload": {"cpu": 41, "memory": 97, "latency": 420, "error_rate": 8.5},
    },
    "netpart": {
        "title": "NETWORK PARTITION",
        "service": "transactions-service",
        "response": "Dependency instability injected. The service should be marked healing while remediation runs.",
        "payload": {"cpu": 63, "memory": 72, "latency": 1100, "error_rate": 9.5},
    },
    "dbfail": {
        "title": "DB FAILURE",
        "service": "payments-service",
        "response": "Database failure simulated. The healing engine should queue a restart.",
        "payload": {"cpu": 57, "memory": 88, "latency": 780, "error_rate": 16},
    },
    "cpu": {
        "title": "CPU STORM",
        "service": "user-service",
        "response": "CPU saturation injected. The healing engine should queue a scale-up action.",
        "payload": {"cpu": 97, "memory": 54, "latency": 170, "error_rate": 1.2},
    },
}


def demo_mode_enabled() -> bool:
    return os.getenv("NEXUS_ENABLE_DEMO_MODE", "false").lower() == "true"


def demo_service_names() -> list[str]:
    return list(DEMO_SERVICES.keys())


def _merge_payload(base: Dict[str, Any], overrides: Dict[str, Any] | None = None) -> Dict[str, Any]:
    payload = copy.deepcopy(base)
    if not overrides:
        return payload
    payload.update(overrides)
    if "metadata" in overrides:
        payload["metadata"] = {**base.get("metadata", {}), **overrides["metadata"]}
    return payload


async def emit_demo_heartbeat(
    service_name: str,
    overrides: Dict[str, Any] | None = None,
    *,
    event_name: str = "service.heartbeat",
) -> Dict[str, Any]:
    if service_name not in DEMO_SERVICES:
        raise ValueError(f"Unknown demo service: {service_name}")
    record = await evaluate_health(_merge_payload(DEMO_SERVICES[service_name], overrides))
    await store.events.publish(event_name, record)
    return record


async def inject_demo_chaos(scenario: str, service_name: str | None = None) -> Dict[str, Any]:
    if not demo_mode_enabled():
        raise RuntimeError("Demo mode is disabled")

    normalized = scenario.strip().lower()
    if normalized not in DEMO_SCENARIOS:
        raise ValueError(f"Unknown demo scenario: {scenario}")

    details = DEMO_SCENARIOS[normalized]
    target_service = service_name or details["service"]
    recovery_delay = float(os.getenv("NEXUS_DEMO_RECOVERY_DELAY_SECONDS", "5"))
    DEMO_HEALTH_HOLDS[target_service] = time.monotonic() + recovery_delay
    event_name = "service.offline" if details["payload"].get("status") == "offline" else "service.heartbeat"
    state = await emit_demo_heartbeat(target_service, details["payload"], event_name=event_name)
    result = {
        "scenario": normalized,
        "title": details["title"],
        "service": target_service,
        "response": details["response"],
        "state": state,
    }
    await store.events.publish("demo.chaos", result)
    return result


async def demo_heartbeat_scheduler() -> None:
    interval = float(os.getenv("NEXUS_DEMO_HEARTBEAT_INTERVAL_SECONDS", "8"))
    while True:
        for service_name in demo_service_names():
            if time.monotonic() < DEMO_HEALTH_HOLDS.get(service_name, 0):
                continue
            DEMO_HEALTH_HOLDS.pop(service_name, None)
            try:
                await emit_demo_heartbeat(service_name)
            except Exception as exc:  # pragma: no cover - best effort demo support
                store.add_log("demo-mode", "ERROR", "Demo heartbeat failed", {"service": service_name, "error": str(exc)})
        await asyncio.sleep(interval)

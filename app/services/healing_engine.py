from __future__ import annotations

import asyncio
import os
from collections import deque
from statistics import mean
from typing import Any, Deque, Dict

from app.core.kafka_producer import send_audit, send_command, send_event
from app.services.registry import get_state, update_service_state
from app.services.saas_platform import evaluate_guarded_action, record_usage
from app.services.state_store import store, utc_iso

THRESHOLDS = {
    "cpu": 85.0,
    "memory": 90.0,
    "latency": 300.0,
    "error_rate": 5.0,
    "prediction_risk": 0.7,
}

SERVICE_WINDOWS: dict[str, Deque[Dict[str, Any]]] = {}
HEALING_QUEUE: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()


def _window_for(service_name: str) -> Deque[Dict[str, Any]]:
    if service_name not in SERVICE_WINDOWS:
        SERVICE_WINDOWS[service_name] = deque(maxlen=12)
    return SERVICE_WINDOWS[service_name]


def _prediction_score(record: Dict[str, Any]) -> float:
    cpu = min(float(record.get("cpu", 0) or 0) / 100.0, 1.0)
    memory = min(float(record.get("memory", 0) or 0) / 100.0, 1.0)
    latency = min(float(record.get("latency", 0) or 0) / 1000.0, 1.0)
    error_rate = min(float(record.get("error_rate", 0) or 0) / 20.0, 1.0)
    return round((cpu * 0.25) + (memory * 0.25) + (latency * 0.25) + (error_rate * 0.25), 3)


def _find_anomalies(record: Dict[str, Any], window: Deque[Dict[str, Any]]) -> list[Dict[str, Any]]:
    anomalies: list[Dict[str, Any]] = []
    latency = float(record.get("latency", 0) or 0)
    error_rate = float(record.get("error_rate", 0) or 0)
    cpu = float(record.get("cpu", 0) or 0)
    memory = float(record.get("memory", 0) or 0)

    if cpu >= THRESHOLDS["cpu"]:
        anomalies.append({"category": "cpu", "severity": "warning", "message": f"CPU spike detected: {cpu}%"})
    if memory >= THRESHOLDS["memory"]:
        anomalies.append({"category": "memory", "severity": "critical", "message": f"Memory pressure detected: {memory}%"})
    if latency >= THRESHOLDS["latency"]:
        anomalies.append({"category": "latency", "severity": "warning", "message": f"Latency spike detected: {latency}ms"})
    if error_rate >= THRESHOLDS["error_rate"]:
        anomalies.append({"category": "error_rate", "severity": "critical", "message": f"Error rate spike detected: {error_rate}%"})

    if len(window) >= 3:
        baseline_latency = mean(float(item.get("latency", 0) or 0) for item in list(window)[:-1] or [record])
        baseline_errors = mean(float(item.get("error_rate", 0) or 0) for item in list(window)[:-1] or [record])
        if baseline_latency and latency > baseline_latency * 1.8:
            anomalies.append(
                {
                    "category": "latency_trend",
                    "severity": "warning",
                    "message": f"Latency trend anomaly: baseline {round(baseline_latency, 2)}ms → {latency}ms",
                }
            )
        if baseline_errors and error_rate > max(baseline_errors * 2, THRESHOLDS["error_rate"]):
            anomalies.append(
                {
                    "category": "error_trend",
                    "severity": "critical",
                    "message": f"Error trend anomaly: baseline {round(baseline_errors, 2)}% → {error_rate}%",
                }
            )

    prediction_score = _prediction_score(record)
    record["prediction_score"] = prediction_score
    record["predicted_failure"] = prediction_score >= THRESHOLDS["prediction_risk"]
    if record["predicted_failure"]:
        anomalies.append(
            {
                "category": "prediction",
                "severity": "warning",
                "message": f"Predicted failure risk elevated ({prediction_score})",
            }
        )

    return anomalies


def _root_cause_suggestion(record: Dict[str, Any], anomalies: list[Dict[str, Any]]) -> str:
    categories = {item["category"] for item in anomalies}
    if "memory" in categories:
        return "Potential memory leak or unbounded cache growth"
    if "error_rate" in categories:
        return "Downstream dependency instability or application exception burst"
    if "latency" in categories or "latency_trend" in categories:
        return "Network congestion or slow downstream dependency"
    if "cpu" in categories:
        return "Hot traffic partition or compute saturation"
    if record.get("status") == "offline":
        return "Heartbeat loss or container crash"
    return "Transient anomaly requiring continued observation"


async def evaluate_health(record: Dict[str, Any]) -> Dict[str, Any]:
    service_name = record["service"]
    window = _window_for(service_name)
    status = record.get("status", "online")

    anomalies = _find_anomalies(record, window)
    desired_action = decide_healing_action(status, anomalies)
    root_cause = _root_cause_suggestion(record, anomalies)

    if anomalies and status == "online":
        status = "degraded"
    if desired_action and status != "offline":
        status = "healing"

    enriched = {
        **record,
        "status": status,
        "anomalies": anomalies,
        "desired_action": desired_action,
        "root_cause": root_cause,
        "last_seen": record.get("last_seen", utc_iso()),
        "last_heartbeat": record.get("last_heartbeat", utc_iso()),
        "anomaly_score": round(min(len(anomalies) / 5, 1.0), 3),
        "ai_suggestion": build_ai_suggestion(record, anomalies, root_cause),
    }

    updated = update_service_state(service_name, enriched)
    window.append(updated)

    if anomalies:
        for anomaly in anomalies:
            store.add_alert(service_name, anomaly["severity"], anomaly["category"], anomaly["message"], updated)
        store.add_log(service_name, "WARN", "Anomaly detected", {"anomalies": anomalies, "root_cause": root_cause})
        await send_event(
            "service.events",
            {
                "type": "anomaly.detected",
                "service": service_name,
                "anomalies": anomalies,
                "root_cause": root_cause,
                "timestamp": utc_iso(),
            },
        )
        await store.events.publish("service.anomaly", {"service": service_name, "anomalies": anomalies, "state": updated})

    if desired_action:
        tenant_id = (updated.get("metadata") or {}).get("tenant_id", "default")
        severity = "critical" if any(item["severity"] == "critical" for item in anomalies) else "warning"
        decision = evaluate_guarded_action(
            tenant_id=tenant_id,
            action=desired_action,
            severity=severity,
            context={"service": service_name, "root_cause": root_cause},
        )
        if not decision["approved"]:
            store.add_log(service_name, "WARN", "Healing action blocked by tenant policy", {"tenant_id": tenant_id, "decision": decision})
            await store.events.publish("healing.blocked", {"service": service_name, "tenant_id": tenant_id, "decision": decision})
            return updated
        record_usage(tenant_id, "auto_heal_decision", 1, {"service": service_name, "action": desired_action, "mode": decision["mode"]})
        await enqueue_healing_task(
            {
                "service": service_name,
                "action": desired_action,
                "reason": anomalies[0]["message"] if anomalies else status,
                "state": updated,
                "tenant_id": tenant_id,
                "policy_mode": decision["mode"],
            }
        )

    return updated


def decide_healing_action(status: str, anomalies: list[Dict[str, Any]]) -> str | None:
    categories = {item["category"] for item in anomalies}
    if status == "offline":
        return "restart"
    if "memory" in categories or "error_rate" in categories:
        return "restart"
    if "cpu" in categories:
        return "scale_up"
    if "latency" in categories or "latency_trend" in categories:
        return "reroute"
    if "prediction" in categories:
        return "schedule_diagnostic"
    return None


def build_ai_suggestion(record: Dict[str, Any], anomalies: list[Dict[str, Any]], root_cause: str) -> Dict[str, Any]:
    return {
        "failure_prediction": record.get("prediction_score", 0),
        "root_cause": root_cause,
        "adaptive_rule": "restart_on_memory_or_error_rate / scale_on_cpu / reroute_on_latency",
        "log_pattern": ", ".join(item["category"] for item in anomalies) or "steady-state",
    }


async def enqueue_healing_task(task: Dict[str, Any]) -> None:
    await HEALING_QUEUE.put(task)
    await store.events.publish("healing.queued", task)


async def healing_scheduler() -> None:
    while True:
        task = await HEALING_QUEUE.get()
        service_name = task["service"]
        action = task["action"]
        reason = task["reason"]
        state = task.get("state") or get_state(service_name) or {"service": service_name}

        store.add_healing_record(service_name, action, reason, "queued", state)
        await send_audit(
            {
                "service": service_name,
                "action": action,
                "reason": reason,
                "status": "queued",
                "timestamp": utc_iso(),
            }
        )

        try:
            if action == "restart":
                await restart(service_name, reason)
            elif action == "scale_up":
                await scale_up(service_name, reason)
            elif action == "reroute":
                await reroute(service_name, reason)
            elif action == "schedule_diagnostic":
                await diagnostic(service_name, reason)
        finally:
            HEALING_QUEUE.task_done()


async def monitoring_scheduler() -> None:
    while True:
        changed = store.detect_offline_services()
        for service in changed:
            await store.events.publish("service.offline", service)
            await enqueue_healing_task(
                {
                    "service": service["service"],
                    "action": "restart",
                    "reason": "Heartbeat timeout detected",
                    "state": service,
                }
            )
        await asyncio.sleep(5)


async def restart(service_name: str, reason: str) -> None:
    orchestrator = os.getenv("NEXUS_ORCHESTRATOR", "docker")
    command = {
        "command": "restart",
        "service": service_name,
        "orchestrator": orchestrator,
        "args": {"mode": "rolling", "grace_seconds": 5},
    }
    await send_command(command)
    store.add_healing_record(service_name, "restart", reason, "completed", command)
    store.add_log(service_name, "INFO", "Automatic restart issued", command)
    await send_audit({"service": service_name, "action": "restart", "reason": reason, "timestamp": utc_iso()})
    await store.events.publish("healing.executed", {"service": service_name, "action": "restart", "reason": reason})


async def scale_up(service_name: str, reason: str) -> None:
    command = {
        "command": "scale_up",
        "service": service_name,
        "count": 1,
        "orchestrator": os.getenv("NEXUS_ORCHESTRATOR", "kubernetes"),
    }
    await send_command(command)
    store.add_healing_record(service_name, "scale_up", reason, "completed", command)
    store.add_log(service_name, "INFO", "Auto-scale command issued", command)
    await send_audit({"service": service_name, "action": "scale_up", "reason": reason, "timestamp": utc_iso()})
    await store.events.publish("healing.executed", {"service": service_name, "action": "scale_up", "reason": reason})


async def reroute(service_name: str, reason: str) -> None:
    command = {
        "command": "reroute",
        "service": service_name,
        "strategy": "traffic_shift",
        "orchestrator": os.getenv("NEXUS_ORCHESTRATOR", "mesh"),
    }
    await send_command(command)
    store.add_healing_record(service_name, "reroute", reason, "completed", command)
    store.add_log(service_name, "INFO", "Traffic reroute issued", command)
    await send_audit({"service": service_name, "action": "reroute", "reason": reason, "timestamp": utc_iso()})
    await store.events.publish("healing.executed", {"service": service_name, "action": "reroute", "reason": reason})


async def diagnostic(service_name: str, reason: str) -> None:
    command = {
        "command": "diagnostic",
        "service": service_name,
        "checks": ["logs", "latency", "dependencies"],
    }
    await send_command(command)
    store.add_healing_record(service_name, "diagnostic", reason, "completed", command)
    store.add_log(service_name, "INFO", "Diagnostic action issued", command)
    await send_audit({"service": service_name, "action": "diagnostic", "reason": reason, "timestamp": utc_iso()})
    await store.events.publish("healing.executed", {"service": service_name, "action": "diagnostic", "reason": reason})

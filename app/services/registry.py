from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.state_store import store, utc_iso


def register_service(name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    record = {
        **payload,
        "service": name,
        "last_seen": payload.get("last_seen", utc_iso()),
        "last_heartbeat": payload.get("last_heartbeat", utc_iso()),
        "status": payload.get("status", "online"),
    }
    return store.upsert_service(name, record)


def update_service_state(name: str, record: Dict[str, Any]) -> Dict[str, Any]:
    payload = {
        **record,
        "service": name,
        "last_seen": record.get("last_seen", utc_iso()),
        "last_heartbeat": record.get("last_heartbeat", utc_iso()),
    }
    store.record_metric(name, payload)
    return store.upsert_service(name, payload)


def get_all_services() -> Dict[str, Dict[str, Any]]:
    return store.get_all_services()


def get_state(name: str) -> Optional[Dict[str, Any]]:
    return store.get_service(name)


def get_recent_metrics(service_name: Optional[str] = None, limit: int = 60) -> List[Dict[str, Any]]:
    return store.get_metrics(service_name=service_name, limit=limit)


def get_recent_alerts(limit: int = 25) -> List[Dict[str, Any]]:
    return store.get_recent_alerts(limit=limit)


def get_recent_healing(limit: int = 25) -> List[Dict[str, Any]]:
    return store.get_recent_healing(limit=limit)


def get_recent_logs(limit: int = 25) -> List[Dict[str, Any]]:
    return store.get_recent_logs(limit=limit)


def detect_offline_services() -> List[Dict[str, Any]]:
    return store.detect_offline_services()


def get_dashboard_state() -> Dict[str, Any]:
    return store.build_dashboard_snapshot()

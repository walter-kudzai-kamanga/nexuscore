from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

UTC = timezone.utc
DB_PATH = Path("app/data/nexuscore.db")
OFFLINE_AFTER_SECONDS = 30
METRIC_RETENTION_PER_SERVICE = 120
ALERT_RETENTION = 200
HEALING_RETENTION = 200
EVENT_RETENTION = 300


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


def utc_iso(value: Optional[datetime] = None) -> str:
    return (value or utc_now()).isoformat()


@dataclass
class EventMessage:
    event: str
    payload: Dict[str, Any]
    timestamp: str


class EventBus:
    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[EventMessage]] = []
        self._lock = asyncio.Lock()

    async def publish(self, event: str, payload: Dict[str, Any]) -> None:
        message = EventMessage(event=event, payload=payload, timestamp=utc_iso())
        async with self._lock:
            dead: list[asyncio.Queue[EventMessage]] = []
            for queue in self._subscribers:
                try:
                    queue.put_nowait(message)
                except asyncio.QueueFull:
                    dead.append(queue)
            for queue in dead:
                if queue in self._subscribers:
                    self._subscribers.remove(queue)

    @contextmanager
    def subscribe(self) -> Generator[asyncio.Queue[EventMessage], None, None]:
        queue: asyncio.Queue[EventMessage] = asyncio.Queue(maxsize=200)
        self._subscribers.append(queue)
        try:
            yield queue
        finally:
            if queue in self._subscribers:
                self._subscribers.remove(queue)


class StateStore:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path
        self._write_lock = threading.Lock()
        self.events = EventBus()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS services (
                    service_name TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    host TEXT,
                    version TEXT,
                    instances INTEGER DEFAULT 1,
                    cpu REAL DEFAULT 0,
                    memory REAL DEFAULT 0,
                    latency REAL DEFAULT 0,
                    error_rate REAL DEFAULT 0,
                    last_heartbeat TEXT,
                    last_seen TEXT,
                    metadata_json TEXT DEFAULT '{}',
                    anomaly_score REAL DEFAULT 0,
                    predicted_failure INTEGER DEFAULT 0,
                    desired_action TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    service_name TEXT NOT NULL,
                    cpu REAL DEFAULT 0,
                    memory REAL DEFAULT 0,
                    latency REAL DEFAULT 0,
                    error_rate REAL DEFAULT 0,
                    status TEXT,
                    recorded_at TEXT NOT NULL,
                    payload_json TEXT DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS healing_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    service_name TEXT NOT NULL,
                    action TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    status TEXT NOT NULL,
                    details_json TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    service_name TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    category TEXT NOT NULL,
                    message TEXT NOT NULL,
                    details_json TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    service_name TEXT NOT NULL,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    details_json TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            conn.commit()

    def _execute(self, query: str, params: tuple[Any, ...] = ()) -> None:
        with self._write_lock:
            with self._connect() as conn:
                conn.execute(query, params)
                conn.commit()

    def _fetchall(self, query: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return list(conn.execute(query, params).fetchall())

    def _fetchone(self, query: str, params: tuple[Any, ...] = ()) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(query, params).fetchone()

    def upsert_service(self, service_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        now = utc_iso()
        normalized = {
            "service": service_name,
            "status": payload.get("status", "unknown"),
            "host": payload.get("host"),
            "version": payload.get("version"),
            "instances": int(payload.get("instances", 1) or 1),
            "cpu": float(payload.get("cpu", 0) or 0),
            "memory": float(payload.get("memory", 0) or 0),
            "latency": float(payload.get("latency", 0) or 0),
            "error_rate": float(payload.get("error_rate", 0) or 0),
            "last_heartbeat": payload.get("last_heartbeat", now),
            "last_seen": payload.get("last_seen", now),
            "metadata": payload.get("metadata", {}),
            "anomaly_score": float(payload.get("anomaly_score", 0) or 0),
            "predicted_failure": 1 if payload.get("predicted_failure") else 0,
            "desired_action": payload.get("desired_action"),
            "updated_at": now,
        }
        self._execute(
            """
            INSERT INTO services (
                service_name, status, host, version, instances, cpu, memory, latency,
                error_rate, last_heartbeat, last_seen, metadata_json, anomaly_score,
                predicted_failure, desired_action, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(service_name) DO UPDATE SET
                status=excluded.status,
                host=excluded.host,
                version=excluded.version,
                instances=excluded.instances,
                cpu=excluded.cpu,
                memory=excluded.memory,
                latency=excluded.latency,
                error_rate=excluded.error_rate,
                last_heartbeat=excluded.last_heartbeat,
                last_seen=excluded.last_seen,
                metadata_json=excluded.metadata_json,
                anomaly_score=excluded.anomaly_score,
                predicted_failure=excluded.predicted_failure,
                desired_action=excluded.desired_action,
                updated_at=excluded.updated_at
            """,
            (
                service_name,
                normalized["status"],
                normalized["host"],
                normalized["version"],
                normalized["instances"],
                normalized["cpu"],
                normalized["memory"],
                normalized["latency"],
                normalized["error_rate"],
                normalized["last_heartbeat"],
                normalized["last_seen"],
                json.dumps(normalized["metadata"]),
                normalized["anomaly_score"],
                normalized["predicted_failure"],
                normalized["desired_action"],
                normalized["updated_at"],
            ),
        )
        return self.get_service(service_name) or normalized

    def record_metric(self, service_name: str, payload: Dict[str, Any]) -> None:
        self._execute(
            """
            INSERT INTO metrics (service_name, cpu, memory, latency, error_rate, status, recorded_at, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                service_name,
                float(payload.get("cpu", 0) or 0),
                float(payload.get("memory", 0) or 0),
                float(payload.get("latency", 0) or 0),
                float(payload.get("error_rate", 0) or 0),
                payload.get("status"),
                payload.get("recorded_at", utc_iso()),
                json.dumps(payload),
            ),
        )
        self._execute(
            """
            DELETE FROM metrics
            WHERE id NOT IN (
                SELECT id FROM metrics WHERE service_name = ? ORDER BY id DESC LIMIT ?
            ) AND service_name = ?
            """,
            (service_name, METRIC_RETENTION_PER_SERVICE, service_name),
        )

    def add_alert(self, service_name: str, severity: str, category: str, message: str, details: Dict[str, Any]) -> None:
        self._execute(
            """
            INSERT INTO alerts (service_name, severity, category, message, details_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (service_name, severity, category, message, json.dumps(details), utc_iso()),
        )
        self._execute(
            "DELETE FROM alerts WHERE id NOT IN (SELECT id FROM alerts ORDER BY id DESC LIMIT ?)",
            (ALERT_RETENTION,),
        )

    def add_log(self, service_name: str, level: str, message: str, details: Dict[str, Any]) -> None:
        self._execute(
            """
            INSERT INTO logs (service_name, level, message, details_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (service_name, level, message, json.dumps(details), utc_iso()),
        )

    def add_healing_record(
        self,
        service_name: str,
        action: str,
        reason: str,
        status: str,
        details: Dict[str, Any],
    ) -> None:
        self._execute(
            """
            INSERT INTO healing_history (service_name, action, reason, status, details_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (service_name, action, reason, status, json.dumps(details), utc_iso()),
        )
        self._execute(
            "DELETE FROM healing_history WHERE id NOT IN (SELECT id FROM healing_history ORDER BY id DESC LIMIT ?)",
            (HEALING_RETENTION,),
        )

    def create_snapshot(self, snapshot_type: str, payload: Dict[str, Any]) -> None:
        self._execute(
            "INSERT INTO snapshots (snapshot_type, payload_json, created_at) VALUES (?, ?, ?)",
            (snapshot_type, json.dumps(payload), utc_iso()),
        )

    def get_service(self, service_name: str) -> Optional[Dict[str, Any]]:
        row = self._fetchone("SELECT * FROM services WHERE service_name = ?", (service_name,))
        return self._serialize_service(row) if row else None

    def get_all_services(self) -> Dict[str, Dict[str, Any]]:
        rows = self._fetchall("SELECT * FROM services ORDER BY service_name ASC")
        return {row["service_name"]: self._serialize_service(row) for row in rows}

    def get_metrics(self, service_name: Optional[str] = None, limit: int = 60) -> List[Dict[str, Any]]:
        if service_name:
            rows = self._fetchall(
                "SELECT * FROM metrics WHERE service_name = ? ORDER BY id DESC LIMIT ?",
                (service_name, limit),
            )
        else:
            rows = self._fetchall("SELECT * FROM metrics ORDER BY id DESC LIMIT ?", (limit,))
        return [self._serialize_metric(row) for row in rows]

    def get_recent_alerts(self, limit: int = 25) -> List[Dict[str, Any]]:
        rows = self._fetchall("SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,))
        return [self._serialize_alert(row) for row in rows]

    def get_recent_logs(self, limit: int = 25) -> List[Dict[str, Any]]:
        rows = self._fetchall("SELECT * FROM logs ORDER BY id DESC LIMIT ?", (limit,))
        return [self._serialize_log(row) for row in rows]

    def get_recent_healing(self, limit: int = 25) -> List[Dict[str, Any]]:
        rows = self._fetchall("SELECT * FROM healing_history ORDER BY id DESC LIMIT ?", (limit,))
        return [self._serialize_healing(row) for row in rows]

    def detect_offline_services(self) -> List[Dict[str, Any]]:
        threshold = utc_now() - timedelta(seconds=OFFLINE_AFTER_SECONDS)
        changed: list[Dict[str, Any]] = []
        for service in self.get_all_services().values():
            last_seen_raw = service.get("last_seen") or service.get("last_heartbeat")
            if not last_seen_raw:
                continue
            try:
                last_seen = datetime.fromisoformat(last_seen_raw)
            except ValueError:
                continue
            if last_seen.tzinfo is None:
                last_seen = last_seen.replace(tzinfo=UTC)
            if last_seen < threshold and service.get("status") != "offline":
                service["status"] = "offline"
                service["desired_action"] = "restart"
                updated = self.upsert_service(service["service"], service)
                self.add_alert(
                    service["service"],
                    "critical",
                    "heartbeat",
                    "Heartbeat timeout detected",
                    {"last_seen": last_seen_raw, "offline_after_seconds": OFFLINE_AFTER_SECONDS},
                )
                self.add_log(service["service"], "ERROR", "Service marked offline by monitor", updated)
                changed.append(updated)
        return changed

    def build_dashboard_snapshot(self) -> Dict[str, Any]:
        services = list(self.get_all_services().values())
        alerts = self.get_recent_alerts(limit=10)
        healing = self.get_recent_healing(limit=10)
        logs = self.get_recent_logs(limit=15)
        metrics = self.get_metrics(limit=60)

        counts = {"online": 0, "offline": 0, "degraded": 0, "healing": 0, "unknown": 0}
        cpu_total = 0.0
        memory_total = 0.0
        latency_total = 0.0

        for service in services:
            status = service.get("status", "unknown")
            counts[status] = counts.get(status, 0) + 1
            cpu_total += float(service.get("cpu", 0) or 0)
            memory_total += float(service.get("memory", 0) or 0)
            latency_total += float(service.get("latency", 0) or 0)

        total = max(len(services), 1)
        health_ratio = 0 if not services else round((counts.get("online", 0) / len(services)) * 100)
        avg_latency = round(latency_total / total, 2)

        snapshot = {
            "generated_at": utc_iso(),
            "services": services,
            "summary": {
                "total": len(services),
                "online": counts.get("online", 0),
                "offline": counts.get("offline", 0),
                "degraded": counts.get("degraded", 0),
                "healing": counts.get("healing", 0),
                "health_percentage": health_ratio,
                "avg_cpu": round(cpu_total / total, 2),
                "avg_memory": round(memory_total / total, 2),
                "avg_latency": avg_latency,
                "heals_today": len(healing),
            },
            "alerts": alerts,
            "healing": healing,
            "logs": logs,
            "metrics": metrics,
            "kafka": {
                "topics": [
                    {"name": "service.health", "partitions": 3, "throughput": len(metrics), "lag": 0},
                    {"name": "service.events", "partitions": 3, "throughput": len(alerts), "lag": 0},
                    {"name": "healing.commands", "partitions": 2, "throughput": len(healing), "lag": 0},
                    {"name": "healing.audit", "partitions": 2, "throughput": len(logs), "lag": 0},
                    {"name": "dead.letter", "partitions": 1, "throughput": 0, "lag": 0},
                ],
                "consumer_groups": [
                    {"group": "nexuscore-monitor", "topics": 2, "lag": 0, "status": "online"},
                    {"group": "nexuscore-healing", "topics": 2, "lag": 0, "status": "online"},
                ],
            },
        }
        self.create_snapshot("dashboard", snapshot)
        return snapshot

    def _serialize_service(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "service": row["service_name"],
            "status": row["status"],
            "host": row["host"],
            "version": row["version"],
            "instances": row["instances"],
            "cpu": row["cpu"],
            "memory": row["memory"],
            "latency": row["latency"],
            "error_rate": row["error_rate"],
            "last_heartbeat": row["last_heartbeat"],
            "last_seen": row["last_seen"],
            "metadata": json.loads(row["metadata_json"] or "{}"),
            "anomaly_score": row["anomaly_score"],
            "predicted_failure": bool(row["predicted_failure"]),
            "desired_action": row["desired_action"],
            "updated_at": row["updated_at"],
        }

    def _serialize_metric(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": row["id"],
            "service": row["service_name"],
            "cpu": row["cpu"],
            "memory": row["memory"],
            "latency": row["latency"],
            "error_rate": row["error_rate"],
            "status": row["status"],
            "recorded_at": row["recorded_at"],
            "payload": json.loads(row["payload_json"] or "{}"),
        }

    def _serialize_alert(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": row["id"],
            "service": row["service_name"],
            "severity": row["severity"],
            "category": row["category"],
            "message": row["message"],
            "details": json.loads(row["details_json"] or "{}"),
            "created_at": row["created_at"],
        }

    def _serialize_log(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": row["id"],
            "service": row["service_name"],
            "level": row["level"],
            "message": row["message"],
            "details": json.loads(row["details_json"] or "{}"),
            "created_at": row["created_at"],
        }

    def _serialize_healing(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": row["id"],
            "service": row["service_name"],
            "action": row["action"],
            "reason": row["reason"],
            "status": row["status"],
            "details": json.loads(row["details_json"] or "{}"),
            "created_at": row["created_at"],
        }


store = StateStore()

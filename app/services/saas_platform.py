from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List

from sqlalchemy import text

from app.services.state_store import store, utc_iso

UTC = timezone.utc


def _now() -> datetime:
    return datetime.now(tz=UTC)


def init_saas_schema() -> None:
    statements = [
        """
        CREATE TABLE IF NOT EXISTS tenants (
            tenant_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            plan TEXT NOT NULL,
            status TEXT NOT NULL,
            quota_services INTEGER DEFAULT 50,
            quota_events_per_day INTEGER DEFAULT 100000,
            key_ref TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS tenant_api_keys (
            api_key_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            key_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS connector_registry (
            connector_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            name TEXT NOT NULL,
            connector_type TEXT NOT NULL,
            config_json TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS healing_policies (
            policy_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            policy_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS billing_usage (
            id INTEGER PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            metric TEXT NOT NULL,
            amount REAL NOT NULL,
            metadata_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS transaction_healing_tasks (
            task_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            workflow_type TEXT NOT NULL,
            reference_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL,
            result_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS compliance_audit (
            id INTEGER PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            prev_hash TEXT NOT NULL,
            entry_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
    ]
    with store.engine.begin() as conn:
        for stmt in statements:
            normalized = stmt
            if store.dialect == "sqlite":
                normalized = normalized.replace("id INTEGER PRIMARY KEY,", "id INTEGER PRIMARY KEY AUTOINCREMENT,")
            elif store.dialect == "postgresql":
                normalized = normalized.replace("id INTEGER PRIMARY KEY,", "id BIGSERIAL PRIMARY KEY,")
            conn.execute(text(normalized))
    if not get_tenant("default"):
        create_tenant("default", "Default Tenant", "starter")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def append_audit(tenant_id: str, actor: str, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    payload_json = json.dumps(payload, sort_keys=True)
    row = store._fetchone(
        "SELECT entry_hash FROM compliance_audit WHERE tenant_id = :tenant_id ORDER BY id DESC LIMIT 1",
        {"tenant_id": tenant_id},
    )
    prev_hash = row["entry_hash"] if row else "genesis"
    entry_hash = _hash(f"{tenant_id}|{actor}|{action}|{payload_json}|{prev_hash}|{utc_iso()}")
    store._execute(
        """
        INSERT INTO compliance_audit (tenant_id, actor, action, payload_json, prev_hash, entry_hash, created_at)
        VALUES (:tenant_id, :actor, :action, :payload_json, :prev_hash, :entry_hash, :created_at)
        """,
        {
            "tenant_id": tenant_id,
            "actor": actor,
            "action": action,
            "payload_json": payload_json,
            "prev_hash": prev_hash,
            "entry_hash": entry_hash,
            "created_at": utc_iso(),
        },
    )
    return {"tenant_id": tenant_id, "action": action, "entry_hash": entry_hash}


def create_tenant(tenant_id: str, name: str, plan: str) -> Dict[str, Any]:
    key_ref = f"kms://tenant/{tenant_id}/{secrets.token_hex(8)}"
    store._execute(
        """
        INSERT INTO tenants (tenant_id, name, plan, status, quota_services, quota_events_per_day, key_ref, created_at)
        VALUES (:tenant_id, :name, :plan, 'active', :quota_services, :quota_events, :key_ref, :created_at)
        ON CONFLICT(tenant_id) DO UPDATE SET
            name = excluded.name,
            plan = excluded.plan,
            quota_services = excluded.quota_services,
            quota_events_per_day = excluded.quota_events_per_day
        """,
        {
            "tenant_id": tenant_id,
            "name": name,
            "plan": plan,
            "quota_services": 50 if plan == "starter" else 250,
            "quota_events": 100000 if plan == "starter" else 1000000,
            "key_ref": key_ref,
            "created_at": utc_iso(),
        },
    )
    raw_key = secrets.token_urlsafe(32)
    store._execute(
        """
        INSERT INTO tenant_api_keys (api_key_id, tenant_id, key_hash, created_at)
        VALUES (:api_key_id, :tenant_id, :key_hash, :created_at)
        """,
        {
            "api_key_id": secrets.token_hex(16),
            "tenant_id": tenant_id,
            "key_hash": _hash(raw_key),
            "created_at": utc_iso(),
        },
    )
    append_audit(tenant_id, "system", "tenant.created", {"name": name, "plan": plan})
    return {"tenant_id": tenant_id, "name": name, "plan": plan, "api_key": raw_key, "key_ref": key_ref}


def get_tenant(tenant_id: str) -> Dict[str, Any] | None:
    return store._fetchone("SELECT * FROM tenants WHERE tenant_id = :tenant_id", {"tenant_id": tenant_id})


def list_tenants() -> List[Dict[str, Any]]:
    return store._fetchall("SELECT * FROM tenants ORDER BY created_at DESC")


def register_connector(tenant_id: str, name: str, connector_type: str, config: Dict[str, Any]) -> Dict[str, Any]:
    row = get_tenant(tenant_id)
    if not row:
        raise ValueError("Unknown tenant")
    existing = store._fetchone(
        "SELECT COUNT(*) as count FROM connector_registry WHERE tenant_id = :tenant_id",
        {"tenant_id": tenant_id},
    )
    if int(existing["count"]) >= int(row["quota_services"]):
        raise ValueError("Connector quota exceeded for tenant plan")
    connector_id = secrets.token_hex(12)
    store._execute(
        """
        INSERT INTO connector_registry (connector_id, tenant_id, name, connector_type, config_json, status, created_at)
        VALUES (:connector_id, :tenant_id, :name, :connector_type, :config_json, 'active', :created_at)
        """,
        {
            "connector_id": connector_id,
            "tenant_id": tenant_id,
            "name": name,
            "connector_type": connector_type,
            "config_json": json.dumps(config),
            "created_at": utc_iso(),
        },
    )
    append_audit(tenant_id, "operator", "connector.registered", {"connector_id": connector_id, "type": connector_type})
    return {"connector_id": connector_id, "tenant_id": tenant_id, "status": "active"}


def upsert_policy(tenant_id: str, policy: Dict[str, Any]) -> Dict[str, Any]:
    policy_id = policy.get("policy_id") or f"{tenant_id}-default"
    store._execute(
        """
        INSERT INTO healing_policies (policy_id, tenant_id, policy_json, updated_at)
        VALUES (:policy_id, :tenant_id, :policy_json, :updated_at)
        ON CONFLICT(policy_id) DO UPDATE SET policy_json = excluded.policy_json, updated_at = excluded.updated_at
        """,
        {
            "policy_id": policy_id,
            "tenant_id": tenant_id,
            "policy_json": json.dumps(policy),
            "updated_at": utc_iso(),
        },
    )
    append_audit(tenant_id, "admin", "policy.updated", {"policy_id": policy_id})
    return {"policy_id": policy_id, "tenant_id": tenant_id}


def _tenant_policy(tenant_id: str) -> Dict[str, Any]:
    row = store._fetchone(
        "SELECT policy_json FROM healing_policies WHERE tenant_id = :tenant_id ORDER BY updated_at DESC LIMIT 1",
        {"tenant_id": tenant_id},
    )
    if not row:
        return {
            "autonomy_mode": "guarded",
            "deny_actions_for_severity": {"critical": ["reroute"]},
            "requires_approval_for": ["restart"],
            "max_auto_actions_per_minute": 20,
        }
    return json.loads(row["policy_json"])


def evaluate_guarded_action(tenant_id: str, action: str, severity: str, context: Dict[str, Any]) -> Dict[str, Any]:
    policy = _tenant_policy(tenant_id)
    denied = set(policy.get("deny_actions_for_severity", {}).get(severity, []))
    requires_approval = set(policy.get("requires_approval_for", []))
    approved = action not in denied
    mode = "auto"
    if action in requires_approval:
        mode = "approval"
    if not approved:
        mode = "blocked"
    decision = {"approved": approved, "mode": mode, "policy": policy, "action": action, "severity": severity}
    append_audit(tenant_id, "policy-engine", "policy.evaluated", {"decision": decision, "context": context})
    return decision


def record_usage(tenant_id: str, metric: str, amount: float, metadata: Dict[str, Any]) -> None:
    store._execute(
        """
        INSERT INTO billing_usage (tenant_id, metric, amount, metadata_json, created_at)
        VALUES (:tenant_id, :metric, :amount, :metadata_json, :created_at)
        """,
        {
            "tenant_id": tenant_id,
            "metric": metric,
            "amount": amount,
            "metadata_json": json.dumps(metadata),
            "created_at": utc_iso(),
        },
    )


def queue_transaction_workflow(tenant_id: str, workflow_type: str, reference_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    task_id = secrets.token_hex(12)
    store._execute(
        """
        INSERT INTO transaction_healing_tasks (
            task_id, tenant_id, workflow_type, reference_id, payload_json, status, result_json, created_at, updated_at
        ) VALUES (:task_id, :tenant_id, :workflow_type, :reference_id, :payload_json, 'queued', '{}', :created_at, :updated_at)
        """,
        {
            "task_id": task_id,
            "tenant_id": tenant_id,
            "workflow_type": workflow_type,
            "reference_id": reference_id,
            "payload_json": json.dumps(payload),
            "created_at": utc_iso(),
            "updated_at": utc_iso(),
        },
    )
    append_audit(tenant_id, "workflow", "transaction.task.queued", {"task_id": task_id, "workflow_type": workflow_type})
    return {"task_id": task_id, "status": "queued"}


def execute_transaction_task(task_id: str) -> Dict[str, Any]:
    row = store._fetchone("SELECT * FROM transaction_healing_tasks WHERE task_id = :task_id", {"task_id": task_id})
    if not row:
        raise ValueError("Task not found")
    payload = json.loads(row["payload_json"])
    workflow_type = row["workflow_type"]
    result: Dict[str, Any]
    if workflow_type == "failed_transaction_replay":
        result = {"action": "replay", "reference_id": row["reference_id"], "status": "replayed", "idempotency_key": payload.get("idempotency_key")}
    elif workflow_type == "stuck_settlement_reconcile":
        result = {"action": "reconcile", "reference_id": row["reference_id"], "status": "reconciled", "ledger_adjustment": payload.get("ledger_adjustment", 0)}
    elif workflow_type == "duplicate_charge_compensate":
        result = {"action": "compensate", "reference_id": row["reference_id"], "status": "compensated", "refund": payload.get("amount", 0)}
    else:
        result = {"action": "diagnose", "reference_id": row["reference_id"], "status": "diagnosed"}
    store._execute(
        """
        UPDATE transaction_healing_tasks
        SET status = 'completed', result_json = :result_json, updated_at = :updated_at
        WHERE task_id = :task_id
        """,
        {"result_json": json.dumps(result), "updated_at": utc_iso(), "task_id": task_id},
    )
    append_audit(row["tenant_id"], "workflow", "transaction.task.completed", {"task_id": task_id, "result": result})
    record_usage(row["tenant_id"], "transaction_healed", 1, {"task_id": task_id, "workflow_type": workflow_type})
    return {"task_id": task_id, "result": result}


def compliance_report(tenant_id: str) -> Dict[str, Any]:
    period_start = (_now().replace(hour=0, minute=0, second=0, microsecond=0)).isoformat()
    evidence_count = store._fetchone(
        "SELECT COUNT(*) as count FROM compliance_audit WHERE tenant_id = :tenant_id",
        {"tenant_id": tenant_id},
    )["count"]
    heals = store._fetchone(
        "SELECT COUNT(*) as count FROM transaction_healing_tasks WHERE tenant_id = :tenant_id AND status = 'completed'",
        {"tenant_id": tenant_id},
    )["count"]
    usage = store._fetchall(
        "SELECT metric, SUM(amount) as total FROM billing_usage WHERE tenant_id = :tenant_id GROUP BY metric",
        {"tenant_id": tenant_id},
    )
    return {
        "tenant_id": tenant_id,
        "period_start": period_start,
        "healing_workflows_completed": heals,
        "evidence_entries": evidence_count,
        "usage_summary": usage,
    }


def export_evidence(tenant_id: str, limit: int = 500) -> Dict[str, Any]:
    rows = store._fetchall(
        "SELECT * FROM compliance_audit WHERE tenant_id = :tenant_id ORDER BY id DESC LIMIT :limit",
        {"tenant_id": tenant_id, "limit": limit},
    )
    chain_valid = True
    prev = None
    for row in reversed(rows):
        if prev and row["entry_hash"] != prev["prev_hash"]:
            chain_valid = False
            break
        prev = row
    return {"tenant_id": tenant_id, "chain_valid": chain_valid, "items": rows}


def billing_overview(tenant_id: str) -> Dict[str, Any]:
    usage = store._fetchall(
        "SELECT metric, SUM(amount) as total FROM billing_usage WHERE tenant_id = :tenant_id GROUP BY metric",
        {"tenant_id": tenant_id},
    )
    tenant = get_tenant(tenant_id)
    return {"tenant": tenant, "usage": usage}


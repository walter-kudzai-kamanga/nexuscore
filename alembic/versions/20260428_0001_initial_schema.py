"""Initial production schema.

Revision ID: 20260428_0001
Revises:
Create Date: 2026-04-28 00:00:00
"""
from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260428_0001"
down_revision = None
branch_labels = None
depends_on = None


def _id_column_sql(dialect_name: str) -> str:
    if dialect_name == "postgresql":
        return "BIGSERIAL PRIMARY KEY"
    return "INTEGER PRIMARY KEY AUTOINCREMENT"


def upgrade() -> None:
    dialect_name = op.get_bind().dialect.name
    id_type = _id_column_sql(dialect_name)

    op.execute(
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
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS metrics (
            id {id_type},
            service_name TEXT NOT NULL,
            cpu REAL DEFAULT 0,
            memory REAL DEFAULT 0,
            latency REAL DEFAULT 0,
            error_rate REAL DEFAULT 0,
            status TEXT,
            recorded_at TEXT NOT NULL,
            payload_json TEXT DEFAULT '{{}}'
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS healing_history (
            id {id_type},
            service_name TEXT NOT NULL,
            action TEXT NOT NULL,
            reason TEXT NOT NULL,
            status TEXT NOT NULL,
            details_json TEXT DEFAULT '{{}}',
            created_at TEXT NOT NULL
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS alerts (
            id {id_type},
            service_name TEXT NOT NULL,
            severity TEXT NOT NULL,
            category TEXT NOT NULL,
            message TEXT NOT NULL,
            details_json TEXT DEFAULT '{{}}',
            created_at TEXT NOT NULL
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS logs (
            id {id_type},
            service_name TEXT NOT NULL,
            level TEXT NOT NULL,
            message TEXT NOT NULL,
            details_json TEXT DEFAULT '{{}}',
            created_at TEXT NOT NULL
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS snapshots (
            id {id_type},
            snapshot_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            created_at TEXT NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS refresh_tokens (
            jti TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            token_hash TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            revoked INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS refresh_tokens")
    op.execute("DROP TABLE IF EXISTS users")
    op.execute("DROP TABLE IF EXISTS snapshots")
    op.execute("DROP TABLE IF EXISTS logs")
    op.execute("DROP TABLE IF EXISTS alerts")
    op.execute("DROP TABLE IF EXISTS healing_history")
    op.execute("DROP TABLE IF EXISTS metrics")
    op.execute("DROP TABLE IF EXISTS services")


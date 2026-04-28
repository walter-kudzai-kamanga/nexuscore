from __future__ import annotations

import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text


def _alembic_config() -> Config:
    root = Path(__file__).resolve().parents[2]
    cfg = Config(str(root / "alembic.ini"))
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def run_startup_migrations() -> None:
    if os.getenv("NEXUS_AUTO_MIGRATE", "false").lower() != "true":
        return

    database_url = os.getenv("DATABASE_URL", "sqlite:///app/data/nexuscore.db")
    if database_url.startswith("sqlite") and os.getenv("NEXUS_AUTO_MIGRATE_ALLOW_SQLITE", "false").lower() != "true":
        return

    lock_enabled = os.getenv("NEXUS_AUTO_MIGRATE_WITH_LOCK", "true").lower() == "true"
    cfg = _alembic_config()

    if database_url.startswith("postgresql") and lock_enabled:
        lock_id = int(os.getenv("NEXUS_AUTO_MIGRATE_LOCK_ID", "682451"))
        engine = create_engine(database_url, pool_pre_ping=True, future=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT pg_advisory_lock(:lock_id)"), {"lock_id": lock_id})
            try:
                command.upgrade(cfg, "head")
            finally:
                conn.execute(text("SELECT pg_advisory_unlock(:lock_id)"), {"lock_id": lock_id})
    else:
        command.upgrade(cfg, "head")


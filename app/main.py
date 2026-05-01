from __future__ import annotations

import asyncio
import json
import os
from contextlib import suppress

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates


from app.api.routes import auth, platform, saas, status
from app.core.config import get_env_or_file
from app.core.kafka_consumer import start_kafka_consumer
from app.core.migrations import run_startup_migrations
from app.core.security import hash_password
from app.core.kafka_producer import ensure_kafka_topics, start_kafka_producer, stop_kafka_producer
from app.services.healing_engine import healing_scheduler, monitoring_scheduler
from app.services.registry import register_service
from app.services.saas_platform import init_saas_schema
from app.services.state_store import store
from app.services.transaction_worker import transaction_healing_worker

app = FastAPI(title="NexusCore")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")
app.include_router(status.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
app.include_router(platform.router, prefix="/api/platform")
app.include_router(saas.router, prefix="/api/saas")

BACKGROUND_TASKS: list[asyncio.Task] = []


def bootstrap_default_services() -> None:
    defaults = [
        {
            "service": "auth-service",
            "status": "online",
            "cpu": 18,
            "memory": 34,
            "latency": 22,
            "error_rate": 0.2,
            "version": "1.0.0",
            "instances": 2,
            "metadata": {"domain": "identity", "protocol": "self-healing-v1"},
        },
        {
            "service": "payments-service",
            "status": "online",
            "cpu": 42,
            "memory": 58,
            "latency": 120,
            "error_rate": 1.4,
            "version": "1.0.0",
            "instances": 2,
            "metadata": {"domain": "payments", "protocol": "self-healing-v1"},
        },
        {
            "service": "transactions-service",
            "status": "online",
            "cpu": 29,
            "memory": 40,
            "latency": 54,
            "error_rate": 0.3,
            "version": "1.0.0",
            "instances": 2,
            "metadata": {"domain": "ledger", "protocol": "self-healing-v1"},
        },
        {
            "service": "notification-service",
            "status": "online",
            "cpu": 14,
            "memory": 24,
            "latency": 18,
            "error_rate": 0.1,
            "version": "1.0.0",
            "instances": 1,
            "metadata": {"domain": "messaging", "protocol": "self-healing-v1"},
        },
        {
            "service": "user-service",
            "status": "online",
            "cpu": 20,
            "memory": 28,
            "latency": 30,
            "error_rate": 0.2,
            "version": "1.0.0",
            "instances": 2,
            "metadata": {"domain": "users", "protocol": "self-healing-v1"},
        },
    ]
    for service in defaults:
        register_service(service["service"], service)


def create_user_if_not_exists(username, password, roles):
    pass


def bootstrap_default_users():
    try:
        filepath = os.getenv("NEXUS_BOOTSTRAP_USERS_JSON_FILE")
        if not filepath:
            print("No bootstrap file configured")
            return

        with open(filepath, "r") as f:
            data = json.load(f)

        # The JSON structure is:
        # { "users": [ {username, password, roles}, ... ] }
        users = data.get("users", [])

        for u in users:
            username = u.get("username")
            password = u.get("password")
            roles = u.get("roles", [])

            print(f"Bootstrapping user: {username}")
            create_user_if_not_exists(username, password, roles)

        print("✔ Bootstrap users loaded")

    except Exception as e:
        print("Bootstrap error:", e)
@app.on_event("startup")
async def startup_event() -> None:
    run_startup_migrations()
    init_saas_schema()
    await ensure_kafka_topics()
    await start_kafka_producer()
    bootstrap_default_users()
    bootstrap_default_services()
    store.cleanup_expired_refresh_tokens()
    BACKGROUND_TASKS.extend(
        [
            asyncio.create_task(start_kafka_consumer()),
            asyncio.create_task(healing_scheduler()),
            asyncio.create_task(monitoring_scheduler()),
            asyncio.create_task(transaction_healing_worker()),
        ]
    )


@app.on_event("shutdown")
async def shutdown_event() -> None:
    for task in BACKGROUND_TASKS:
        task.cancel()
    for task in BACKGROUND_TASKS:
        with suppress(asyncio.CancelledError):
            await task
    BACKGROUND_TASKS.clear()
    await stop_kafka_producer()


@app.get("/")
async def dashboard(request: Request):
    return templates.TemplateResponse(
        request,
        "dashboard.html",
    )

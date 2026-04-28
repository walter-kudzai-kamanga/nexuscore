from __future__ import annotations

import asyncio
from contextlib import suppress

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api.routes import status
from app.core.kafka_consumer import start_kafka_consumer
from app.core.kafka_producer import ensure_kafka_topics, start_kafka_producer, stop_kafka_producer
from app.services.healing_engine import healing_scheduler, monitoring_scheduler
from app.services.registry import register_service

app = FastAPI(title="NexusCore")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates("app/templates")
app.include_router(status.router, prefix="/api")

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


@app.on_event("startup")
async def startup_event() -> None:
    await ensure_kafka_topics()
    await start_kafka_producer()
    bootstrap_default_services()
    BACKGROUND_TASKS.extend(
        [
            asyncio.create_task(start_kafka_consumer()),
            asyncio.create_task(healing_scheduler()),
            asyncio.create_task(monitoring_scheduler()),
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
    return templates.TemplateResponse("dashboard.html", {"request": request})

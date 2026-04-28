from __future__ import annotations

import asyncio
import json
import os
import socket
from typing import Any, Dict
from uuid import uuid4

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse

SERVICE_NAME = os.getenv("SERVICE_NAME", "sample-service")
SERVICE_PORT = int(os.getenv("SERVICE_PORT", "8100"))
NEXUSCORE_URL = os.getenv("NEXUSCORE_URL", "http://localhost:8000/api")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "service.health")
HEARTBEAT_INTERVAL_SECONDS = int(os.getenv("HEARTBEAT_INTERVAL_SECONDS", "10"))
INSTANCE_ID = os.getenv("INSTANCE_ID", str(uuid4()))
SERVICE_API_KEY = os.getenv("NEXUS_SERVICE_API_KEY", "nexus-service-key")

app = FastAPI(title=f"{SERVICE_NAME} Template")


def current_metrics() -> Dict[str, Any]:
    seed = sum(ord(char) for char in SERVICE_NAME) % 10
    return {
        "service": SERVICE_NAME,
        "host": socket.gethostname(),
        "version": "1.0.0",
        "status": "online",
        "instances": 1,
        "cpu": 15 + seed,
        "memory": 25 + seed,
        "latency": 20 + seed * 2,
        "error_rate": round(seed * 0.1, 2),
        "metadata": {
            "instance_id": INSTANCE_ID,
            "compliance_protocol": "self-healing-v1",
            "kafka_topic": KAFKA_TOPIC,
        },
    }


async def register_with_nexuscore() -> None:
    async with httpx.AsyncClient(timeout=5.0) as client:
        await client.post(
            f"{NEXUSCORE_URL}/services/register",
            json=current_metrics(),
            headers={"x-api-key": SERVICE_API_KEY},
        )


async def emit_heartbeat() -> None:
    while True:
        payload = current_metrics()
        payload["metadata"]["emitted_event"] = "service.heartbeat"
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"{NEXUSCORE_URL}/services/heartbeat",
                json=payload,
                headers={"x-api-key": SERVICE_API_KEY},
            )
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)


@app.on_event("startup")
async def startup_event() -> None:
    await register_with_nexuscore()
    asyncio.create_task(emit_heartbeat())


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"ok": True, **current_metrics()})


@app.get("/kafka-event")
async def kafka_event() -> JSONResponse:
    event = {
        "topic": KAFKA_TOPIC,
        "flow": "microservice-to-nexuscore",
        "payload": current_metrics(),
    }
    return JSONResponse({"ok": True, "event": event, "serialized": json.dumps(event)})

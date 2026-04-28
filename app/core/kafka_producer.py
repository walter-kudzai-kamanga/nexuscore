from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict

from aiokafka import AIOKafkaProducer

LOGGER = logging.getLogger(__name__)
BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
TOPICS = {
    "service_health": "service.health",
    "service_events": "service.events",
    "healing_commands": "healing.commands",
    "healing_audit": "healing.audit",
    "dead_letter": "dead.letter",
    "logs_stream": "logs.stream",
    "alerts": "alerts.notifications",
}

producer: AIOKafkaProducer | None = None


async def start_kafka_producer() -> None:
    global producer
    if producer is not None:
        return
    producer = AIOKafkaProducer(
        bootstrap_servers=BOOTSTRAP_SERVERS,
        value_serializer=lambda value: json.dumps(value).encode("utf-8"),
    )
    await producer.start()
    LOGGER.info("Kafka producer started for %s", BOOTSTRAP_SERVERS)


async def stop_kafka_producer() -> None:
    global producer
    if producer is None:
        return
    await producer.stop()
    producer = None


async def ensure_kafka_topics() -> Dict[str, Any]:
    topic_status = {
        "bootstrap_servers": BOOTSTRAP_SERVERS,
        "topics": [
            {"name": name, "topic": topic, "partitions": 1, "replication_factor": 1}
            for name, topic in TOPICS.items()
        ],
        "schema_registry": {"enabled": False, "mode": "placeholder-json-schema"},
        "dlq": TOPICS["dead_letter"],
    }
    LOGGER.info("Kafka topic plan prepared: %s", topic_status)
    return topic_status


async def send_event(topic: str, payload: Dict[str, Any]) -> None:
    if producer is None:
        LOGGER.warning("Kafka producer unavailable, dropping event for topic %s", topic)
        return
    await producer.send_and_wait(topic, payload)


async def send_command(command: Dict[str, Any]) -> None:
    await send_event(TOPICS["healing_commands"], command)


async def send_audit(audit: Dict[str, Any]) -> None:
    await send_event(TOPICS["healing_audit"], audit)


async def send_alert(alert: Dict[str, Any]) -> None:
    await send_event(TOPICS["alerts"], alert)


async def send_log(log_event: Dict[str, Any]) -> None:
    await send_event(TOPICS["logs_stream"], log_event)


async def send_dead_letter(payload: Dict[str, Any]) -> None:
    await send_event(TOPICS["dead_letter"], payload)

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Dict

from aiokafka import AIOKafkaConsumer

from app.core.kafka_producer import TOPICS, send_dead_letter, send_event
from app.services.healing_engine import evaluate_health
from app.services.state_store import utc_iso

LOGGER = logging.getLogger(__name__)
BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
CONSUMER_GROUP = os.getenv("KAFKA_CONSUMER_GROUP", "nexuscore-monitor")


async def process_message(topic: str, value: Dict[str, Any]) -> None:
    if topic == TOPICS["service_health"]:
        await evaluate_health(value)
    elif topic == TOPICS["service_events"]:
        await send_event(
            TOPICS["alerts"],
            {
                "type": "service.event.received",
                "payload": value,
                "timestamp": utc_iso(),
            },
        )
    else:
        await send_dead_letter({"topic": topic, "payload": value, "reason": "Unhandled topic"})


async def start_kafka_consumer() -> None:
    consumer = AIOKafkaConsumer(
        TOPICS["service_health"],
        TOPICS["service_events"],
        bootstrap_servers=BOOTSTRAP_SERVERS,
        value_deserializer=lambda value: json.loads(value.decode("utf-8")),
        group_id=CONSUMER_GROUP,
    )
    await consumer.start()
    LOGGER.info("Kafka consumer started for %s", BOOTSTRAP_SERVERS)
    try:
        async for message in consumer:
            try:
                await process_message(message.topic, message.value)
            except Exception as exc:  # pragma: no cover - defensive pathway
                LOGGER.exception("Kafka message processing failed: %s", exc)
                await send_dead_letter(
                    {
                        "topic": message.topic,
                        "payload": message.value,
                        "reason": str(exc),
                        "timestamp": utc_iso(),
                    }
                )
    except asyncio.CancelledError:
        LOGGER.info("Kafka consumer cancelled")
        raise
    finally:
        await consumer.stop()

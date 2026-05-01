from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import suppress
from typing import Any, Dict

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer, TopicPartition
from aiokafka.admin import AIOKafkaAdminClient, NewTopic

LOGGER = logging.getLogger(__name__)
BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
MONITORED_CONSUMER_GROUPS = os.getenv("KAFKA_MONITORED_GROUPS", "nexuscore-monitor,nexuscore-healing").split(",")
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
    admin = AIOKafkaAdminClient(bootstrap_servers=BOOTSTRAP_SERVERS)
    await admin.start()
    topic_specs = [
        NewTopic(name=topic, num_partitions=3 if topic != TOPICS["dead_letter"] else 1, replication_factor=1)
        for topic in TOPICS.values()
    ]
    created = []
    errors = []
    try:
        try:
            await admin.create_topics(topic_specs, validate_only=False)
            created = [spec.name for spec in topic_specs]
        except Exception as exc:  # pragma: no cover
            errors.append(str(exc))
    finally:
        await admin.close()

    topic_status = {
        "bootstrap_servers": BOOTSTRAP_SERVERS,
        "created": created,
        "errors": errors,
        "topics": [
            {"name": name, "topic": topic, "partitions": 3 if topic != TOPICS["dead_letter"] else 1, "replication_factor": 1}
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


async def kafka_observability_metrics() -> Dict[str, Any]:
    topics = list(TOPICS.values())
    consumer_groups = [group.strip() for group in MONITORED_CONSUMER_GROUPS if group.strip()]
    topic_metrics: list[Dict[str, Any]] = []
    group_metrics: list[Dict[str, Any]] = []

    inspector = AIOKafkaConsumer(bootstrap_servers=BOOTSTRAP_SERVERS, enable_auto_commit=False)
    await inspector.start()
    try:
        for topic in topics:
            partitions = inspector.partitions_for_topic(topic) or set()
            tps = [TopicPartition(topic, partition) for partition in partitions]
            end_offsets = await inspector.end_offsets(tps) if tps else {}
            topic_metrics.append(
                {
                    "name": topic,
                    "partitions": len(partitions),
                    "end_offsets": {f"{tp.topic}-{tp.partition}": value for tp, value in end_offsets.items()},
                }
            )

        for group in consumer_groups:
            group_consumer = AIOKafkaConsumer(
                *topics,
                bootstrap_servers=BOOTSTRAP_SERVERS,
                group_id=group,
                enable_auto_commit=False,
            )
            await group_consumer.start()
            try:
                lag_total = 0
                partition_lag: Dict[str, int] = {}
                for topic in topics:
                    partitions = group_consumer.partitions_for_topic(topic) or set()
                    for partition in partitions:
                        tp = TopicPartition(topic, partition)
                        end = (await group_consumer.end_offsets([tp])).get(tp, 0)
                        committed = await group_consumer.committed(tp) or 0
                        lag = max(end - committed, 0)
                        lag_total += lag
                        partition_lag[f"{topic}-{partition}"] = lag
                group_metrics.append({"group": group, "lag_total": lag_total, "partition_lag": partition_lag})
            finally:
                with suppress(asyncio.CancelledError):
                    await group_consumer.stop()
    finally:
        with suppress(asyncio.CancelledError):
            await inspector.stop()

    return {"bootstrap_servers": BOOTSTRAP_SERVERS, "topics": topic_metrics, "consumer_groups": group_metrics}

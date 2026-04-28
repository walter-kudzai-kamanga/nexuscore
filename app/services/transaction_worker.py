from __future__ import annotations

import asyncio
import os

from app.services.saas_platform import execute_transaction_task, list_queued_transaction_tasks, mark_transaction_task_running
from app.services.state_store import store, utc_iso

POLL_SECONDS = float(os.getenv("NEXUS_TRANSACTION_WORKER_POLL_SECONDS", "2"))
BATCH_SIZE = int(os.getenv("NEXUS_TRANSACTION_WORKER_BATCH_SIZE", "25"))


async def transaction_healing_worker() -> None:
    while True:
        tasks = list_queued_transaction_tasks(limit=BATCH_SIZE)
        for task in tasks:
            task_id = task["task_id"]
            mark_transaction_task_running(task_id)
            try:
                result = execute_transaction_task(task_id)
                await store.events.publish("transaction.healed", result)
            except Exception as exc:  # pragma: no cover
                store._execute(
                    "UPDATE transaction_healing_tasks SET status = 'failed', result_json = :result_json, updated_at = :updated_at WHERE task_id = :task_id",
                    {"task_id": task_id, "result_json": str({"error": str(exc)}), "updated_at": utc_iso()},
                )
                await store.events.publish("transaction.heal_failed", {"task_id": task_id, "error": str(exc)})
        await asyncio.sleep(POLL_SECONDS)


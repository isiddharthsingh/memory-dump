"""arq worker entry point.

Run with:
    arq app.workers.worker.WorkerSettings

Health: arq rewrites its health-check key every `health_check_interval`
seconds, and `arq --check app.workers.worker.WorkerSettings` exits non-zero
once it has gone stale (the compose healthcheck runs that).
"""

import logging
from typing import Any

from arq import cron, func
from arq.connections import RedisSettings
from prometheus_client import start_http_server

from app.config import get_settings
from app.logging_setup import adopt
from app.observability import init_sentry
from app.workers.compact import compact_topics, compact_user
from app.workers.digest import DIGEST_KEEP_RESULT_S, digest_user, weekly_digest
from app.workers.ingest import ingest_file, ingest_item
from app.workers.jobs import JOB_TIMEOUT_S, MAX_TRIES
from app.workers.reconcile import observe_queue, requeue_stuck
from app.workers.synthesize import forget_item, synthesize_topics

_settings = get_settings()
init_sentry("worker")


async def startup(ctx: dict[str, Any]) -> None:
    # arq set up its own log handler before importing this module.
    adopt(logging.getLogger("arq"))
    if _settings.worker_metrics_port:
        start_http_server(_settings.worker_metrics_port)


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(_settings.redis_url)
    functions = [
        ingest_item,
        ingest_file,
        synthesize_topics,
        forget_item,
        compact_topics,
        compact_user,
        weekly_digest,
        func(digest_user, keep_result=DIGEST_KEEP_RESULT_S),
    ]
    cron_jobs = [
        # Nightly at 03:14 UTC.
        cron(compact_topics, hour={3}, minute={14}, run_at_startup=False),
        # Sundays 16:00 UTC (around lunch on the US west coast).
        cron(
            weekly_digest,
            weekday={6},
            hour={16},
            minute={0},
            run_at_startup=False,
        ),
        # Every 5 minutes, and once as the worker starts.
        cron(requeue_stuck, minute=set(range(0, 60, 5)), run_at_startup=True),
        cron(observe_queue, run_at_startup=True),
    ]
    on_startup = startup
    keep_result_forever = False
    max_jobs = 4
    # Each job gives up by itself on its last try (app/workers/jobs.py).
    max_tries = MAX_TRIES
    job_timeout = JOB_TIMEOUT_S
    health_check_interval = 30

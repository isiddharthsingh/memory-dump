"""arq worker entry point.

Run with:
    arq app.workers.worker.WorkerSettings
"""

from arq import cron
from arq.connections import RedisSettings

from app.config import get_settings
from app.workers.compact import compact_topics
from app.workers.digest import weekly_digest
from app.workers.ingest import ingest_item
from app.workers.synthesize import synthesize_topics

_settings = get_settings()


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(_settings.redis_url)
    functions = [ingest_item, synthesize_topics, compact_topics, weekly_digest]
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
    ]
    keep_result_forever = False
    max_jobs = 4
    job_timeout = 600  # 10 minutes

"""arq worker entry point.

Exposes `WorkerSettings` per the arq convention. Run with:

    arq app.workers.worker.WorkerSettings
"""

from arq.connections import RedisSettings

from app.config import get_settings
from app.workers.ingest import ingest_item
from app.workers.synthesize import synthesize_topics

_settings = get_settings()


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(_settings.redis_url)
    functions = [ingest_item, synthesize_topics]
    keep_result_forever = False
    max_jobs = 4
    job_timeout = 600  # 10 minutes — yt-dlp + Claude can be slow

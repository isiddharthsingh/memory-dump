"""arq worker entry point.

Phase 1: skeleton only. Concrete tasks land in Phase 2 (ingest pipeline) and
Phase 5 (wiki synthesis).
"""

from app.config import get_settings

settings = get_settings()


async def noop(ctx: dict) -> str:
    return "ok"


class WorkerSettings:
    redis_settings = None  # populated from settings below
    functions = [noop]
    keep_result_forever = False
    max_jobs = 4


# Late import so we don't pull arq's dependency tree at module import in tests.
def _redis_settings():
    from arq.connections import RedisSettings

    return RedisSettings.from_dsn(settings.redis_url)


WorkerSettings.redis_settings = _redis_settings()

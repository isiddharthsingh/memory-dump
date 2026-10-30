from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # SuperTokens core: the docker-compose service, or a managed deployment's
    # connection URI. The API key must match the core's API_KEYS.
    supertokens_connection_uri: str = "http://supertokens:3567"
    supertokens_api_key: str = ""
    # Public address of this API. SuperTokens serves sign-in, sign-up and
    # refresh under {api_domain}/auth, and its user dashboard at
    # {api_domain}/auth/dashboard.
    api_domain: str = "http://localhost:8000"
    allow_signup: bool = True

    # Per-user rate limits on the endpoints that spend Claude and Voyage
    # tokens, and per-address limits on sign-in and sign-up. Token buckets in
    # Redis (app/auth/rate_limit.py); 0 turns a limit off.
    rate_limit_ingest_per_hour: int = 60
    rate_limit_ask_per_hour: int = 120
    rate_limit_search_per_hour: int = 600
    rate_limit_auth_per_hour: int = 30

    # Oldest app build (CFBundleVersion) the API still answers; older builds
    # get 426 and are asked to update. 0 lets every build in.
    min_app_build: int = 0

    postgres_user: str = "memory"
    postgres_password: str = "memory"
    postgres_db: str = "memory_dump"
    postgres_host: str = "postgres"
    postgres_port: int = 5432

    # Connections each process keeps to Postgres, how long a request waits for
    # one, and how long a statement or an idle transaction may run before
    # Postgres ends it.
    db_pool_size: int = 10
    db_max_overflow: int = 10
    db_pool_timeout_s: float = 10
    db_statement_timeout_ms: int = 30_000
    db_idle_in_transaction_timeout_ms: int = 60_000

    redis_url: str = "redis://redis:6379/0"

    anthropic_api_key: str = ""
    voyage_api_key: str = ""

    # How long a call to Claude may take: Ask has someone waiting on it, the
    # workers can wait longer. Voyage answers in seconds.
    anthropic_timeout_s: float = 300
    ask_timeout_s: float = 60
    voyage_timeout_s: float = 30
    # Calls to each provider in flight at once, across every API and worker
    # process; 0 = no cap.
    anthropic_max_concurrency: int = 16
    voyage_max_concurrency: int = 16

    # How long fetching and extracting a saved link may take, and the largest
    # page body read.
    extract_timeout_s: float = 90
    extract_max_bytes: int = 5 * 1024 * 1024

    claude_analysis_model: str = "claude-haiku-4-5-20251001"
    claude_synthesis_model: str = "claude-sonnet-4-6"
    claude_ask_model: str = "claude-opus-4-7"
    voyage_embed_model: str = "voyage-3-large"
    voyage_rerank_model: str = "rerank-2.5"

    # Multimodal backend for /ingest/file. Currently only "anthropic" is
    # implemented; see app/ai/providers.py for how to add another (e.g.
    # "openrouter") without changing call sites.
    ai_vision_provider: str = "anthropic"

    # Where uploaded files wait for the worker (app/storage.py). "local" keeps
    # them in uploads_dir, which must be the same volume in the api and worker
    # containers; "s3" keeps them in an S3-compatible bucket, so the two can
    # run on different hosts.
    storage_backend: str = "local"
    uploads_dir: str = "/uploads"
    s3_bucket: str = ""
    s3_endpoint_url: str = ""
    s3_region: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""

    # APNs token auth. Create the .p8 key under Certificates, IDs & Profiles →
    # Keys with "Apple Push Notifications service" enabled. Leave the path
    # empty to skip pushes.
    apns_key_path: str = ""
    apns_key_id: str = ""
    apns_team_id: str = ""
    apns_topic: str = "com.siddharthsingh.memorydump"

    log_level: str = "INFO"
    # "json" for log collectors, "console" for reading in a terminal.
    log_format: str = "json"
    # Errors go to Sentry when set.
    sentry_dsn: str = ""
    sentry_environment: str = "development"
    # /metrics answers only with `Authorization: Bearer <metrics_token>`, and
    # not at all while it's empty. The worker serves its metrics on
    # worker_metrics_port (0 = off).
    metrics_token: str = ""
    worker_metrics_port: int = 9100

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def sync_database_url(self) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()

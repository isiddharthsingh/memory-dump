from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Secret used to sign access JWTs. Generate with:
    #   openssl rand -hex 32
    jwt_secret: str = Field(alias="JWT_SECRET")
    jwt_access_ttl_min: int = 15
    jwt_refresh_ttl_days: int = 30
    allow_signup: bool = True

    # Per-user rate limits applied to the spend-heavy endpoints (Claude +
    # Voyage usage). Counted in a Redis fixed-window bucket per hour.
    rate_limit_ingest_per_hour: int = 60
    rate_limit_ask_per_hour: int = 120

    postgres_user: str = "memory"
    postgres_password: str = "memory"
    postgres_db: str = "memory_dump"
    postgres_host: str = "postgres"
    postgres_port: int = 5432

    redis_url: str = "redis://redis:6379/0"

    anthropic_api_key: str = ""
    voyage_api_key: str = ""

    claude_analysis_model: str = "claude-haiku-4-5-20251001"
    claude_synthesis_model: str = "claude-sonnet-4-6"
    claude_ask_model: str = "claude-opus-4-7"
    voyage_embed_model: str = "voyage-3-large"
    voyage_rerank_model: str = "rerank-2.5"

    # Multimodal backend for /ingest/file. Currently only "anthropic" is
    # implemented; see app/ai/providers.py for how to add another (e.g.
    # "openrouter") without changing call sites.
    ai_vision_provider: str = "anthropic"

    # Where the API drops uploaded files and the worker reads them. Must be
    # the same path inside both containers (docker-compose mounts a shared
    # volume).
    uploads_dir: str = "/uploads"

    # APNs token auth. Create the .p8 key under Certificates, IDs & Profiles →
    # Keys with "Apple Push Notifications service" enabled. Leave the path
    # empty to skip pushes.
    apns_key_path: str = ""
    apns_key_id: str = ""
    apns_team_id: str = ""
    apns_topic: str = "com.siddharthsingh.memorydump"

    log_level: str = "INFO"

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

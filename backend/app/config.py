from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    api_token: str = Field(alias="MEMORY_DUMP_API_TOKEN")

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

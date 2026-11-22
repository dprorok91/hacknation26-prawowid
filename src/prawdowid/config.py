"""Runtime settings; no side effects or secret printing at import time."""

from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, PositiveInt, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)
    app_env: str = "development"
    database_url: str = Field(
        default="postgresql+asyncpg://prawdowid:prawdowid_dev_password@127.0.0.1:5432/prawdowid",
        repr=False,
    )
    llm_provider: str = "ollama"
    public_llm_provider: Literal["ollama", "openrouter", "openrouter_all"] = "ollama"
    openrouter_model: str = Field(default="openai/gpt-4.1-mini", min_length=1, max_length=128)
    openrouter_api_key: SecretStr | None = Field(default=None, repr=False)
    llm_model: str = "qwen3:4b"
    ollama_base_url: str = "http://127.0.0.1:11434"
    embedding_model: str = "intfloat/multilingual-e5-small"
    embedding_revision: str | None = Field(default=None, pattern="^[0-9a-f]{40}$")
    factcheck_api_key: SecretStr | None = Field(default=None, repr=False)
    newsdata_api_key: SecretStr | None = Field(default=None, repr=False)
    enable_newsdata_fallback: bool = False
    newsdata_daily_credit_budget: int = Field(default=20, ge=1, le=200)
    discovery_cache_hours: int = Field(default=12, ge=1, le=24)
    embedding_dim: int = Field(default=384, ge=384, le=384)
    max_fetch_concurrency: PositiveInt = 4
    max_llm_concurrency: PositiveInt = 1
    analysis_deadline_seconds: PositiveInt = 180
    analysis_retention_hours: int = Field(default=24, ge=1, le=168)
    text_retention_minutes: int = Field(default=15, ge=1, le=120)
    log_raw_user_input: bool = False

    def worker_openrouter_key(self) -> SecretStr:
        key = self.openrouter_api_key
        if key is None or not key.get_secret_value().strip():
            raise ValueError("PUBLIC_LLM_PROVIDER=openrouter requires OPENROUTER_API_KEY.")
        return key

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        url = urlsplit(value)
        if (
            url.scheme != "postgresql+asyncpg"
            or not url.path.strip("/")
            or url.fragment
            or url.port == 0
        ):
            raise ValueError("a PostgreSQL asyncpg URL with a database name is required")
        return value

    @field_validator("ollama_base_url")
    @classmethod
    def validate_ollama_url(cls, value: str) -> str:
        url = urlsplit(value)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username is not None
            or url.port == 0
            or url.query
            or url.fragment
        ):
            raise ValueError("an HTTP(S) Ollama base URL is required")
        return value.rstrip("/")


def get_settings() -> Settings:
    return Settings()

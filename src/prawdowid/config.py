"""Runtime settings; no side effects or secret printing at import time."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_env: str = "development"
    database_url: str = (
        "postgresql+asyncpg://prawdowid:prawdowid_dev_password@127.0.0.1:5432/prawdowid"
    )
    llm_provider: str = "ollama"
    llm_model: str = "qwen3:4b"
    ollama_base_url: str = "http://127.0.0.1:11434"
    embedding_model: str = "intfloat/multilingual-e5-small"
    embedding_dim: int = 384
    max_fetch_concurrency: int = 4
    max_llm_concurrency: int = 1
    analysis_deadline_seconds: int = 28
    log_raw_user_input: bool = False


def get_settings() -> Settings:
    return Settings()

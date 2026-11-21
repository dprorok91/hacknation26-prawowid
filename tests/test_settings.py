import pytest
from pydantic import ValidationError

from prawdowid.config import Settings


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("DATABASE_URL", "sqlite+aiosqlite:///tmp/test.db"),
        ("MAX_FETCH_CONCURRENCY", "0"),
        ("MAX_LLM_CONCURRENCY", "-1"),
        ("ANALYSIS_DEADLINE_SECONDS", "0"),
        ("EMBEDDING_DIM", "768"),
        ("OLLAMA_BASE_URL", "file:///var/tmp/model"),
    ],
)
def test_invalid_settings_fail_at_startup(monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_database_credentials_are_not_in_settings_repr():
    settings = Settings(
        _env_file=None, database_url="postgresql+asyncpg://dev:private-demo-token@localhost/dev"
    )
    assert "private-demo-token" not in repr(settings)

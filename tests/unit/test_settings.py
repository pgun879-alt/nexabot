from __future__ import annotations

import pytest

from nexabot.config.settings import Environment, Settings
from nexabot.domain.common.errors import ConfigurationError


def test_local_settings_have_safe_defaults() -> None:
    settings = Settings.load({})

    assert settings.app.environment == Environment.LOCAL
    assert settings.telegram.enabled is False
    assert settings.llm.provider == "stub"
    assert settings.features.enable_api is True


def test_settings_safe_summary_redacts_secrets_and_credentials() -> None:
    settings = Settings.load(
        {
            "DATABASE_URL": "postgresql+asyncpg://user:password@db.example/nexabot",
            "REDIS_URL": "redis://:secret@redis.example:6379/0",
            "TELEGRAM_BOT_TOKEN": "123:telegram-token",
            "OPENAI_API_KEY": "sk-test",
            "NEXABOT_SECRET_KEY": "local-secret",
        }
    )

    summary = settings.safe_summary()

    assert summary["database"] == {"url": "postgresql+asyncpg://********@db.example/nexabot"}
    assert summary["redis"] == {
        "url": "redis://********@redis.example:6379/0",
        "queue_prefix": "nexabot",
    }
    assert summary["telegram"]["bot_token"] == "********"  # type: ignore[index]
    assert summary["llm"]["openai_api_key"] == "********"  # type: ignore[index]
    assert summary["security"]["secret_key"] == "********"  # type: ignore[index]


def test_production_settings_fail_fast_when_required_values_missing() -> None:
    with pytest.raises(ConfigurationError) as raised:
        Settings.load({"NEXABOT_ENVIRONMENT": "production"})

    error = raised.value
    assert error.code == "missing_required_configuration"
    assert set(error.details["missing"]) == {
        "DATABASE_URL",
        "REDIS_URL",
        "TELEGRAM_BOT_TOKEN",
        "NEXABOT_SECRET_KEY",
    }


def test_openai_provider_requires_api_key_in_production() -> None:
    with pytest.raises(ConfigurationError) as raised:
        Settings.load(
            {
                "NEXABOT_ENVIRONMENT": "production",
                "DATABASE_URL": "postgresql+asyncpg://nexabot:nexabot@localhost/nexabot",
                "REDIS_URL": "redis://localhost:6379/0",
                "TELEGRAM_BOT_TOKEN": "token",
                "NEXABOT_SECRET_KEY": "secret",
                "LLM_PROVIDER": "openai",
            }
        )

    assert raised.value.details["missing"] == ["OPENAI_API_KEY"]

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


def test_no_secret_value_survives_anywhere_in_the_safe_summary() -> None:
    """A blanket check: individual assertions miss fields added later."""
    secrets = {
        "DATABASE_URL": "postgresql+asyncpg://dbuser:db-pw-AAA@db.example/nexabot",
        "REDIS_URL": "redis://:redis-pw-BBB@redis.example:6379/0",
        "TELEGRAM_BOT_TOKEN": "123:tg-token-CCC",
        "OPENAI_API_KEY": "sk-openai-DDD",
        "NEXABOT_SECRET_KEY": "app-secret-EEE",
    }

    rendered = str(Settings.load(secrets).safe_summary())

    for marker in ("db-pw-AAA", "redis-pw-BBB", "tg-token-CCC", "sk-openai-DDD", "app-secret-EEE"):
        assert marker not in rendered, f"{marker} leaked into safe_summary()"


def test_production_settings_fail_fast_when_required_values_missing() -> None:
    with pytest.raises(ConfigurationError) as raised:
        Settings.load({"NEXABOT_ENVIRONMENT": "production"})

    error = raised.value
    assert error.code == "missing_required_configuration"
    # Only what every production process needs regardless of features.
    assert set(error.details["missing"]) == {"DATABASE_URL", "NEXABOT_SECRET_KEY"}


def test_production_requires_secrets_only_for_enabled_features() -> None:
    """Demanding secrets for disabled features trains operators to fake them."""
    base = {
        "NEXABOT_ENVIRONMENT": "production",
        "DATABASE_URL": "postgresql+asyncpg://nexabot:nexabot@db/nexabot",
        "NEXABOT_SECRET_KEY": "secret",
    }

    # Telegram and workers off: their secrets are not required.
    settings = Settings.load(base)
    assert settings.telegram.enabled is False

    with pytest.raises(ConfigurationError) as raised:
        Settings.load({**base, "NEXABOT_ENABLE_TELEGRAM": "true"})
    assert raised.value.details["missing"] == ["TELEGRAM_BOT_TOKEN"]

    with pytest.raises(ConfigurationError) as raised:
        Settings.load({**base, "NEXABOT_ENABLE_WORKERS": "true"})
    assert raised.value.details["missing"] == ["REDIS_URL"]


def test_production_rejects_a_provider_with_no_adapter() -> None:
    """Regression: 'openai' was accepted although no adapter exists."""
    with pytest.raises(ConfigurationError) as raised:
        Settings.load(
            {
                "NEXABOT_ENVIRONMENT": "production",
                "DATABASE_URL": "postgresql+asyncpg://nexabot:nexabot@db/nexabot",
                "NEXABOT_SECRET_KEY": "secret",
                "LLM_PROVIDER": "openai",
                "OPENAI_API_KEY": "sk-test",
            }
        )

    assert raised.value.code == "llm_provider_not_implemented"
    assert raised.value.details["implemented"] == ["stub"]


@pytest.mark.parametrize(
    ("environ", "expected_code", "expected_variable"),
    [
        ({"NEXABOT_LOG_LEVEL": "chatty"}, "invalid_log_level", "NEXABOT_LOG_LEVEL"),
        ({"NEXABOT_API_PORT": "eighty"}, "invalid_integer_setting", "NEXABOT_API_PORT"),
        (
            {"NEXABOT_ENABLE_API": "maybe"},
            "invalid_boolean_setting",
            "NEXABOT_ENABLE_API",
        ),
        ({"LLM_PROVIDER": "definitely-not-real"}, "invalid_enum_setting", "LLM_PROVIDER"),
        ({"NEXABOT_ENVIRONMENT": "staging"}, "invalid_enum_setting", "NEXABOT_ENVIRONMENT"),
        ({"NEXABOT_LOG_FORMAT": "xml"}, "invalid_enum_setting", "NEXABOT_LOG_FORMAT"),
    ],
)
def test_invalid_environment_values_raise_configuration_errors(
    environ: dict[str, str], expected_code: str, expected_variable: str
) -> None:
    """Regression: these used to raise a bare ValueError from a constructor."""
    with pytest.raises(ConfigurationError) as raised:
        Settings.load(environ)

    assert raised.value.code == expected_code
    assert raised.value.details["variable"] == expected_variable


def test_credentials_in_url_query_parameters_are_redacted() -> None:
    """Regression: only `user:pass@` was redacted, leaving `?password=` bare."""
    settings = Settings.load(
        {"DATABASE_URL": "postgresql+asyncpg://db.example/nexabot?password=hunter2&sslmode=require"}
    )

    summary_url = settings.safe_summary()["database"]["url"]  # type: ignore[index]

    assert "hunter2" not in str(summary_url)
    assert "sslmode=require" in str(summary_url)


def test_log_level_is_normalized_and_usable() -> None:
    settings = Settings.load({"NEXABOT_LOG_LEVEL": "debug"})

    assert settings.app.log_level == "DEBUG"
    assert settings.app.log_level_number == 10


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

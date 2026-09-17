"""Validated runtime settings.

Settings are loaded from environment variables. Production mode fails fast when
critical settings are missing.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from nexabot.domain.common.errors import ConfigurationError


class Environment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    PRODUCTION = "production"


class LogFormat(StrEnum):
    CONSOLE = "console"
    JSON = "json"


class AppSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = "NexaBot"
    environment: Environment = Environment.LOCAL
    log_level: str = "INFO"
    log_format: LogFormat = LogFormat.CONSOLE

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        return value.upper()


class ApiSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)


class DatabaseSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    url: str = "postgresql+asyncpg://nexabot:nexabot@localhost:5432/nexabot"
    pool_size: int = 5
    max_overflow: int = 10


class RedisSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    url: str = "redis://localhost:6379/0"
    queue_prefix: str = "nexabot"


class TelegramSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    bot_token: SecretStr | None = None
    polling_enabled: bool = False

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.bot_token.get_secret_value())


class LLMSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str = "stub"
    default_model: str | None = None
    openai_api_key: SecretStr | None = None


class SecuritySettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    secret_key: SecretStr | None = None
    max_requests_per_minute: int = Field(default=60, ge=1)
    max_file_size_mb: int = Field(default=25, ge=1)


class FeatureFlags(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    enable_telegram: bool = False
    enable_api: bool = True
    enable_workers: bool = False


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    app: AppSettings = Field(default_factory=AppSettings)
    api: ApiSettings = Field(default_factory=ApiSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    features: FeatureFlags = Field(default_factory=FeatureFlags)

    @classmethod
    def load(cls, environ: Mapping[str, str] | None = None) -> Settings:
        source = dict(os.environ if environ is None else environ)
        environment = Environment(source.get("NEXABOT_ENVIRONMENT", Environment.LOCAL.value))

        cls._validate_required_environment(source, environment)

        return cls(
            app=AppSettings(
                name=source.get("NEXABOT_APP_NAME", "NexaBot"),
                environment=environment,
                log_level=source.get("NEXABOT_LOG_LEVEL", "INFO"),
                log_format=LogFormat(source.get("NEXABOT_LOG_FORMAT", LogFormat.CONSOLE.value)),
            ),
            api=ApiSettings(
                host=source.get("NEXABOT_API_HOST", ApiSettings().host),
                port=int(source.get("NEXABOT_API_PORT", "8000")),
            ),
            database=DatabaseSettings(
                url=source.get("DATABASE_URL", DatabaseSettings().url),
                pool_size=int(source.get("DATABASE_POOL_SIZE", "5")),
                max_overflow=int(source.get("DATABASE_MAX_OVERFLOW", "10")),
            ),
            redis=RedisSettings(
                url=source.get("REDIS_URL", RedisSettings().url),
                queue_prefix=source.get("REDIS_QUEUE_PREFIX", "nexabot"),
            ),
            telegram=TelegramSettings(
                bot_token=cls._optional_secret(source.get("TELEGRAM_BOT_TOKEN")),
                polling_enabled=cls._as_bool(source.get("TELEGRAM_POLLING_ENABLED", "false")),
            ),
            llm=LLMSettings(
                provider=source.get("LLM_PROVIDER", "stub"),
                default_model=source.get("LLM_DEFAULT_MODEL") or None,
                openai_api_key=cls._optional_secret(source.get("OPENAI_API_KEY")),
            ),
            security=SecuritySettings(
                secret_key=cls._optional_secret(source.get("NEXABOT_SECRET_KEY")),
                max_requests_per_minute=int(source.get("NEXABOT_MAX_REQUESTS_PER_MINUTE", "60")),
                max_file_size_mb=int(source.get("NEXABOT_MAX_FILE_SIZE_MB", "25")),
            ),
            features=FeatureFlags(
                enable_telegram=cls._as_bool(source.get("NEXABOT_ENABLE_TELEGRAM", "false")),
                enable_api=cls._as_bool(source.get("NEXABOT_ENABLE_API", "true")),
                enable_workers=cls._as_bool(source.get("NEXABOT_ENABLE_WORKERS", "false")),
            ),
        )

    def safe_summary(self) -> dict[str, object]:
        return {
            "app": self.app.model_dump(),
            "api": self.api.model_dump(),
            "database": {"url": self._redact_url(self.database.url)},
            "redis": {
                "url": self._redact_url(self.redis.url),
                "queue_prefix": self.redis.queue_prefix,
            },
            "telegram": {
                "bot_token": self._redact_secret(self.telegram.bot_token),
                "polling_enabled": self.telegram.polling_enabled,
                "enabled": self.telegram.enabled,
            },
            "llm": {
                "provider": self.llm.provider,
                "default_model": self.llm.default_model,
                "openai_api_key": self._redact_secret(self.llm.openai_api_key),
            },
            "security": {
                "secret_key": self._redact_secret(self.security.secret_key),
                "max_requests_per_minute": self.security.max_requests_per_minute,
                "max_file_size_mb": self.security.max_file_size_mb,
            },
            "features": self.features.model_dump(),
        }

    @staticmethod
    def _validate_required_environment(source: Mapping[str, str], environment: Environment) -> None:
        if environment != Environment.PRODUCTION:
            return

        required = ["DATABASE_URL", "REDIS_URL", "TELEGRAM_BOT_TOKEN", "NEXABOT_SECRET_KEY"]
        missing = [key for key in required if not source.get(key)]

        provider = source.get("LLM_PROVIDER", "stub")
        if provider == "openai" and not source.get("OPENAI_API_KEY"):
            missing.append("OPENAI_API_KEY")

        if missing:
            raise ConfigurationError(
                "Missing required production configuration.",
                code="missing_required_configuration",
                missing=sorted(set(missing)),
            )

    @staticmethod
    def _optional_secret(value: str | None) -> SecretStr | None:
        if value is None or value == "":
            return None
        return SecretStr(value)

    @staticmethod
    def _as_bool(value: str) -> bool:
        return value.strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _redact_secret(value: SecretStr | None) -> str | None:
        return "********" if value and value.get_secret_value() else None

    @staticmethod
    def _redact_url(value: str) -> str:
        if "@" not in value or "://" not in value:
            return value
        scheme, rest = value.split("://", 1)
        _, host = rest.rsplit("@", 1)
        return f"{scheme}://********@{host}"

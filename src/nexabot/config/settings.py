"""Validated runtime settings.

Settings are loaded from environment variables. Production mode fails fast when
critical settings are missing.
"""

from __future__ import annotations

import logging
import os
import re
import urllib.parse
from collections.abc import Mapping
from enum import StrEnum
from typing import TypeVar

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from nexabot.domain.common.errors import ConfigurationError


class Environment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    PRODUCTION = "production"


class LogFormat(StrEnum):
    CONSOLE = "console"
    JSON = "json"


class LLMProvider(StrEnum):
    STUB = "stub"
    """Deterministic in-process gateway. The only provider with an adapter."""

    OPENAI = "openai"
    """Recognised but NOT implemented: no adapter exists in infrastructure.llm.

    Kept as a value so configuration validation can reject it with an accurate
    message instead of accepting it and failing later at wiring time.
    """


IMPLEMENTED_LLM_PROVIDERS = frozenset({LLMProvider.STUB})

VALID_LOG_LEVELS = frozenset({"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"})

SENSITIVE_QUERY_PARAMETERS = re.compile(
    r"(password|passwd|pwd|token|secret|api[_-]?key|sslkey|auth)", re.IGNORECASE
)
"""Connection-URL query parameters that carry credentials.

Drivers accept credentials as query parameters as readily as in the userinfo
section, so redacting only ``user:pass@`` leaves the other half in the clear.
"""

_EnumT = TypeVar("_EnumT", bound=StrEnum)


class AppSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = "NexaBot"
    environment: Environment = Environment.LOCAL
    log_level: str = "INFO"
    log_format: LogFormat = LogFormat.CONSOLE

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        """Reject unknown levels here rather than at the first log call.

        ``logging.setLevel`` raises on an unrecognised name, so an invalid
        value would otherwise take the process down during startup with a
        stdlib traceback instead of a configuration error.
        """
        normalized = value.strip().upper()
        if normalized not in VALID_LOG_LEVELS:
            raise ConfigurationError(
                "Unknown log level.",
                code="invalid_log_level",
                variable="NEXABOT_LOG_LEVEL",
                allowed=sorted(VALID_LOG_LEVELS),
            )
        return normalized

    @property
    def log_level_number(self) -> int:
        return int(logging.getLevelNamesMapping()[self.log_level])


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

    provider: LLMProvider = LLMProvider.STUB
    default_model: str | None = None
    openai_api_key: SecretStr | None = None

    @property
    def implemented(self) -> bool:
        return self.provider in IMPLEMENTED_LLM_PROVIDERS


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
        """Build settings from the environment, failing with a domain error.

        Every value is parsed through a helper that raises ``ConfigurationError``
        rather than letting ``int()`` or an enum constructor raise. A malformed
        variable is an operator mistake and deserves a message naming the
        variable, not a ``ValueError`` from the middle of a constructor.
        """
        source = dict(os.environ if environ is None else environ)
        environment = cls._as_enum(
            Environment, source.get("NEXABOT_ENVIRONMENT"), "NEXABOT_ENVIRONMENT", Environment.LOCAL
        )
        features = FeatureFlags(
            enable_telegram=cls._as_bool(source, "NEXABOT_ENABLE_TELEGRAM", default=False),
            enable_api=cls._as_bool(source, "NEXABOT_ENABLE_API", default=True),
            enable_workers=cls._as_bool(source, "NEXABOT_ENABLE_WORKERS", default=False),
        )
        provider = cls._as_enum(
            LLMProvider, source.get("LLM_PROVIDER"), "LLM_PROVIDER", LLMProvider.STUB
        )

        cls._validate_required_environment(source, environment, features, provider)

        return cls(
            app=AppSettings(
                name=source.get("NEXABOT_APP_NAME", "NexaBot"),
                environment=environment,
                log_level=source.get("NEXABOT_LOG_LEVEL", "INFO"),
                log_format=cls._as_enum(
                    LogFormat,
                    source.get("NEXABOT_LOG_FORMAT"),
                    "NEXABOT_LOG_FORMAT",
                    LogFormat.CONSOLE,
                ),
            ),
            api=ApiSettings(
                host=source.get("NEXABOT_API_HOST", ApiSettings().host),
                port=cls._as_int(source, "NEXABOT_API_PORT", default=8000),
            ),
            database=DatabaseSettings(
                url=source.get("DATABASE_URL", DatabaseSettings().url),
                pool_size=cls._as_int(source, "DATABASE_POOL_SIZE", default=5),
                max_overflow=cls._as_int(source, "DATABASE_MAX_OVERFLOW", default=10),
            ),
            redis=RedisSettings(
                url=source.get("REDIS_URL", RedisSettings().url),
                queue_prefix=source.get("REDIS_QUEUE_PREFIX", "nexabot"),
            ),
            telegram=TelegramSettings(
                bot_token=cls._optional_secret(source.get("TELEGRAM_BOT_TOKEN")),
                polling_enabled=cls._as_bool(
                    source, "TELEGRAM_POLLING_ENABLED", default=False
                ),
            ),
            llm=LLMSettings(
                provider=provider,
                default_model=source.get("LLM_DEFAULT_MODEL") or None,
                openai_api_key=cls._optional_secret(source.get("OPENAI_API_KEY")),
            ),
            security=SecuritySettings(
                secret_key=cls._optional_secret(source.get("NEXABOT_SECRET_KEY")),
                max_requests_per_minute=cls._as_int(
                    source, "NEXABOT_MAX_REQUESTS_PER_MINUTE", default=60
                ),
                max_file_size_mb=cls._as_int(source, "NEXABOT_MAX_FILE_SIZE_MB", default=25),
            ),
            features=features,
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
                "provider": self.llm.provider.value,
                "implemented": self.llm.implemented,
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
    def _validate_required_environment(
        source: Mapping[str, str],
        environment: Environment,
        features: FeatureFlags,
        provider: LLMProvider,
    ) -> None:
        """Refuse to start production on incomplete configuration.

        Requirements are scoped to the features actually turned on. Demanding a
        Telegram token from a deployment running with Telegram disabled teaches
        operators to fill required secrets with throwaway values, which is
        worse than not asking: a real secret and a placeholder then look
        identical.
        """
        if environment != Environment.PRODUCTION:
            return

        required = ["DATABASE_URL", "NEXABOT_SECRET_KEY"]
        if features.enable_telegram:
            required.append("TELEGRAM_BOT_TOKEN")
        if features.enable_workers:
            # Redis is only read by the queue adapter, which only workers run.
            required.append("REDIS_URL")
        if provider == LLMProvider.OPENAI:
            required.append("OPENAI_API_KEY")

        missing = [key for key in required if not source.get(key)]
        if missing:
            raise ConfigurationError(
                "Missing required production configuration.",
                code="missing_required_configuration",
                missing=sorted(set(missing)),
            )

        if provider not in IMPLEMENTED_LLM_PROVIDERS:
            raise ConfigurationError(
                "The configured LLM provider has no adapter.",
                code="llm_provider_not_implemented",
                provider=provider.value,
                implemented=sorted(p.value for p in IMPLEMENTED_LLM_PROVIDERS),
            )

    @staticmethod
    def _optional_secret(value: str | None) -> SecretStr | None:
        if value is None or value == "":
            return None
        return SecretStr(value)

    @staticmethod
    def _as_bool(source: Mapping[str, str], variable: str, *, default: bool) -> bool:
        raw = source.get(variable)
        if raw is None or raw.strip() == "":
            return default
        normalized = raw.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        # Silently reading an unrecognised value as False is how a feature flag
        # meant to be on ends up off for a whole deploy.
        raise ConfigurationError(
            "Expected a boolean value.", code="invalid_boolean_setting", variable=variable
        )

    @staticmethod
    def _as_int(source: Mapping[str, str], variable: str, *, default: int) -> int:
        raw = source.get(variable)
        if raw is None or raw.strip() == "":
            return default
        try:
            return int(raw.strip())
        except ValueError:
            raise ConfigurationError(
                "Expected an integer value.", code="invalid_integer_setting", variable=variable
            ) from None

    @staticmethod
    def _as_enum(
        enum_type: type[_EnumT], raw: str | None, variable: str, default: _EnumT
    ) -> _EnumT:
        if raw is None or raw.strip() == "":
            return default
        try:
            return enum_type(raw.strip().lower())
        except ValueError:
            raise ConfigurationError(
                "Unsupported value.",
                code="invalid_enum_setting",
                variable=variable,
                allowed=sorted(member.value for member in enum_type),
            ) from None

    @staticmethod
    def _redact_secret(value: SecretStr | None) -> str | None:
        return "********" if value and value.get_secret_value() else None

    @staticmethod
    def _redact_url(value: str) -> str:
        """Strip credentials from a connection URL before it can be logged.

        Both carriers are handled: userinfo before the ``@``, and query
        parameters such as ``?password=`` that several drivers accept as an
        alternative to userinfo. Redacting only the first leaves the second
        fully readable in whatever the summary is written to.
        """
        if "://" not in value:
            return value
        try:
            parsed = urllib.parse.urlsplit(value)
        except ValueError:
            # Unparseable: nothing here can be trusted to be credential-free.
            return "********"

        netloc = parsed.netloc
        if "@" in netloc:
            netloc = f"********@{netloc.rsplit('@', 1)[1]}"

        query = parsed.query
        if query:
            pairs = urllib.parse.parse_qsl(query, keep_blank_values=True)
            query = urllib.parse.urlencode(
                [
                    (key, "********" if SENSITIVE_QUERY_PARAMETERS.search(key) else val)
                    for key, val in pairs
                ]
            )

        return urllib.parse.urlunsplit(
            (parsed.scheme, netloc, parsed.path, query, parsed.fragment)
        )

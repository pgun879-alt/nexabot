"""Identity entities and value objects."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from nexabot.domain.common.errors import ConflictError, ValidationError
from nexabot.domain.common.ids import UserId
from nexabot.domain.common.time import utc_now


class UserStatus(StrEnum):
    ACTIVE = "active"
    BANNED = "banned"
    DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class Permission:
    namespace: str
    action: str

    def __post_init__(self) -> None:
        if not self.namespace or not self.action:
            raise ValidationError("Permission namespace and action are required.")

    @property
    def key(self) -> str:
        return f"{self.namespace}:{self.action}"


@dataclass(frozen=True, slots=True)
class Role:
    name: str
    permissions: frozenset[Permission] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValidationError("Role name is required.")


@dataclass(frozen=True, slots=True)
class TelegramIdentity:
    telegram_user_id: int
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None

    def __post_init__(self) -> None:
        if self.telegram_user_id <= 0:
            raise ValidationError("Telegram user id must be positive.")


@dataclass(slots=True)
class User:
    id: UserId
    status: UserStatus = UserStatus.ACTIVE
    roles: frozenset[Role] = field(default_factory=frozenset)
    telegram_identity: TelegramIdentity | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def has_permission(self, permission: Permission) -> bool:
        return any(permission in role.permissions for role in self.roles)

    def assign_roles(self, roles: Iterable[Role]) -> None:
        self.roles = frozenset(roles)
        self.updated_at = utc_now()

    def attach_telegram_identity(self, identity: TelegramIdentity) -> None:
        self.telegram_identity = identity
        self.updated_at = utc_now()

    def ban(self) -> None:
        if self.status == UserStatus.BANNED:
            raise ConflictError("User is already banned.", code="user_already_banned")
        self.status = UserStatus.BANNED
        self.updated_at = utc_now()

    def unban(self) -> None:
        if self.status != UserStatus.BANNED:
            raise ConflictError("Only banned users can be unbanned.", code="user_not_banned")
        self.status = UserStatus.ACTIVE
        self.updated_at = utc_now()

    def ensure_active(self) -> None:
        if self.status != UserStatus.ACTIVE:
            raise ConflictError(
                "User is not active.", code="user_not_active", status=self.status.value
            )

"""Identity repository ports."""

from __future__ import annotations

from typing import Protocol

from nexabot.domain.common.ids import UserId
from nexabot.domain.identity.entities import TelegramIdentity, User


class UserRepository(Protocol):
    async def get(self, user_id: UserId) -> User | None:
        """Return a user by id."""

    async def get_by_telegram_identity(self, telegram_user_id: int) -> User | None:
        """Return a user mapped to a Telegram account."""

    async def add(self, user: User) -> None:
        """Persist a new user."""

    async def save(self, user: User) -> None:
        """Persist changes to an existing user."""

    async def attach_telegram_identity(self, user_id: UserId, identity: TelegramIdentity) -> None:
        """Create or update Telegram identity mapping."""

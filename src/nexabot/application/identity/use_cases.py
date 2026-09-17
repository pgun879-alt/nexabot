"""Identity application use cases.

These orchestrate domain entities and repository ports only. No
infrastructure, Telegram, or FastAPI details may appear here.
"""

from __future__ import annotations

from dataclasses import dataclass

from nexabot.domain.common.errors import AuthorizationError, NotFoundError
from nexabot.domain.common.ids import UserId, new_user_id
from nexabot.domain.identity.entities import TelegramIdentity, User
from nexabot.ports.repositories.identity import UserRepository


@dataclass(slots=True)
class RegisterOrUpdateTelegramUser:
    """Create a user for a first-seen Telegram account, or refresh identity details."""

    users: UserRepository

    async def execute(self, identity: TelegramIdentity) -> User:
        existing = await self.users.get_by_telegram_identity(identity.telegram_user_id)
        if existing is not None:
            existing.attach_telegram_identity(identity)
            await self.users.save(existing)
            return existing

        user = User(id=new_user_id())
        user.attach_telegram_identity(identity)
        await self.users.add(user)
        return user


@dataclass(slots=True)
class ResolveCurrentActor:
    """Resolve the acting user for an incoming Telegram update, rejecting inactive users."""

    users: UserRepository

    async def execute(self, telegram_user_id: int) -> User:
        user = await self.users.get_by_telegram_identity(telegram_user_id)
        if user is None:
            raise AuthorizationError("Unknown Telegram user.", code="unknown_actor")
        user.ensure_active()
        return user


@dataclass(slots=True)
class BanUser:
    users: UserRepository

    async def execute(self, user_id: UserId) -> User:
        user = await self.users.get(user_id)
        if user is None:
            raise NotFoundError("User not found.", code="user_not_found")
        user.ban()
        await self.users.save(user)
        return user


@dataclass(slots=True)
class UnbanUser:
    users: UserRepository

    async def execute(self, user_id: UserId) -> User:
        user = await self.users.get(user_id)
        if user is None:
            raise NotFoundError("User not found.", code="user_not_found")
        user.unban()
        await self.users.save(user)
        return user

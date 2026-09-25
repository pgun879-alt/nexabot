from __future__ import annotations

import pytest

from nexabot.application.identity.use_cases import (
    BanUser,
    RegisterOrUpdateTelegramUser,
    ResolveCurrentActor,
    UnbanUser,
)
from nexabot.domain.common.errors import AuthorizationError, ConflictError, NotFoundError
from nexabot.domain.common.ids import new_user_id
from nexabot.domain.identity.entities import TelegramIdentity, User
from nexabot.infrastructure.database.repositories.identity import SqlAlchemyUserRepository
from tests.support import EngineRegistry, run_scenario


def test_register_telegram_user_creates_then_updates_on_repeat_calls() -> None:
    async def scenario(engines: EngineRegistry) -> None:
        sessionmaker = await engines.sessionmaker()
        identity = TelegramIdentity(telegram_user_id=42, username="first-name")

        async with sessionmaker() as session:
            use_case = RegisterOrUpdateTelegramUser(users=SqlAlchemyUserRepository(session))
            created = await use_case.execute(identity)
            await session.commit()

        updated_identity = TelegramIdentity(telegram_user_id=42, username="renamed")
        async with sessionmaker() as session:
            use_case = RegisterOrUpdateTelegramUser(users=SqlAlchemyUserRepository(session))
            updated = await use_case.execute(updated_identity)
            await session.commit()

        assert updated.id == created.id
        assert updated.telegram_identity is not None
        assert updated.telegram_identity.username == "renamed"

    run_scenario(scenario)


def test_resolve_current_actor_rejects_unknown_and_banned_users() -> None:
    async def scenario(engines: EngineRegistry) -> None:
        sessionmaker = await engines.sessionmaker()
        identity = TelegramIdentity(telegram_user_id=7)

        async with sessionmaker() as session:
            repo = SqlAlchemyUserRepository(session)
            await RegisterOrUpdateTelegramUser(users=repo).execute(identity)
            await session.commit()

        async with sessionmaker() as session:
            actor = await ResolveCurrentActor(users=SqlAlchemyUserRepository(session)).execute(7)
            assert actor.telegram_identity is not None
            assert actor.telegram_identity.telegram_user_id == 7

        async with sessionmaker() as session:
            with pytest.raises(AuthorizationError):
                await ResolveCurrentActor(users=SqlAlchemyUserRepository(session)).execute(999)

        async with sessionmaker() as session:
            repo = SqlAlchemyUserRepository(session)
            actor = await ResolveCurrentActor(users=repo).execute(7)
            await BanUser(users=repo).execute(actor.id)
            await session.commit()

        async with sessionmaker() as session:
            with pytest.raises(ConflictError):
                await ResolveCurrentActor(users=SqlAlchemyUserRepository(session)).execute(7)

    run_scenario(scenario)


def test_ban_and_unban_user_round_trip() -> None:
    async def scenario(engines: EngineRegistry) -> None:
        sessionmaker = await engines.sessionmaker()
        user_id = new_user_id()

        async with sessionmaker() as session:
            repo = SqlAlchemyUserRepository(session)
            await repo.add(User(id=user_id))
            await session.commit()

        async with sessionmaker() as session:
            repo = SqlAlchemyUserRepository(session)
            banned = await BanUser(users=repo).execute(user_id)
            await session.commit()
            assert banned.status.value == "banned"

        async with sessionmaker() as session:
            repo = SqlAlchemyUserRepository(session)
            unbanned = await UnbanUser(users=repo).execute(user_id)
            await session.commit()
            assert unbanned.status.value == "active"

    run_scenario(scenario)


def test_ban_unknown_user_raises_not_found() -> None:
    async def scenario(engines: EngineRegistry) -> None:
        sessionmaker = await engines.sessionmaker()
        async with sessionmaker() as session:
            with pytest.raises(NotFoundError):
                await BanUser(users=SqlAlchemyUserRepository(session)).execute(new_user_id())

    run_scenario(scenario)

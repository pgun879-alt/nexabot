"""Persistence failures must reach application code as domain errors.

A raw SQLAlchemy exception escaping the unit of work bypasses the whole error
taxonomy and surfaces to users as an opaque 500.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from nexabot.domain.common.errors import ConflictError, ExternalServiceError
from nexabot.domain.common.ids import new_conversation_id, new_user_id
from nexabot.domain.conversations.entities import Conversation
from nexabot.domain.identity.entities import TelegramIdentity, User
from nexabot.infrastructure.database import models as _models  # noqa: F401
from nexabot.infrastructure.database.base import Base
from nexabot.infrastructure.database.errors import translate_database_errors
from nexabot.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWorkFactory


async def _make_sessionmaker() -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


def test_constraint_violation_surfaces_as_a_domain_conflict() -> None:
    async def scenario() -> None:
        sessionmaker = await _make_sessionmaker()
        uow_factory = SqlAlchemyUnitOfWorkFactory(sessionmaker)

        user = User(id=new_user_id())
        user.attach_telegram_identity(TelegramIdentity(telegram_user_id=900))
        async with uow_factory() as uow:
            await uow.users.add(user)

        # A second user claiming the same Telegram account violates the
        # unique constraint on telegram_identities.telegram_user_id.
        duplicate = User(id=new_user_id())
        duplicate.attach_telegram_identity(TelegramIdentity(telegram_user_id=900))

        with pytest.raises(ConflictError) as raised:
            async with uow_factory() as uow:
                await uow.users.add(duplicate)

        assert raised.value.code == "database_conflict"
        assert raised.value.http_status == 409
        assert isinstance(raised.value.__cause__, SQLAlchemyError)

    asyncio.run(scenario())


def test_duplicate_primary_key_does_not_leak_sqlalchemy() -> None:
    async def scenario() -> None:
        sessionmaker = await _make_sessionmaker()
        uow_factory = SqlAlchemyUnitOfWorkFactory(sessionmaker)

        owner = User(id=new_user_id())
        owner.attach_telegram_identity(TelegramIdentity(telegram_user_id=901))
        conversation = Conversation(id=new_conversation_id(), owner_id=owner.id)

        async with uow_factory() as uow:
            await uow.users.add(owner)
            await uow.conversations.add(conversation)

        with pytest.raises(ConflictError):
            async with uow_factory() as uow:
                await uow.conversations.add(conversation)

    asyncio.run(scenario())


def test_translator_maps_connection_failures_to_a_retryable_error() -> None:
    from sqlalchemy.exc import OperationalError

    with (
        pytest.raises(ExternalServiceError) as raised,
        translate_database_errors(operation="commit"),
    ):
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    assert raised.value.code == "database_unavailable"
    assert raised.value.retryable is True
    assert "connection refused" not in raised.value.safe_message


def test_translator_does_not_swallow_unrelated_errors() -> None:
    with (
        pytest.raises(ValueError),
        translate_database_errors(operation="commit"),
    ):
        raise ValueError("not a database problem")

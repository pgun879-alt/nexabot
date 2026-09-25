"""Repository adapters are verified against a real SQLAlchemy engine.

Production uses PostgreSQL over asyncpg (see infrastructure.database.session).
These tests substitute an in-memory SQLite engine so the mapping and query
logic can be verified without a running PostgreSQL server; the repositories
themselves are written against standard SQLAlchemy Core/ORM and contain no
PostgreSQL-specific behavior.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexabot.domain.agent.entities import AgentLimits, AgentRun, AgentRunStatus, AgentStepType
from nexabot.domain.common.errors import ConflictError, NotFoundError, ValidationError
from nexabot.domain.common.ids import (
    new_agent_run_id,
    new_conversation_id,
    new_message_id,
    new_user_id,
)
from nexabot.domain.common.time import utc_now
from nexabot.domain.conversations.entities import Conversation, Message, MessageRole
from nexabot.domain.identity.entities import Permission, Role, TelegramIdentity, User
from nexabot.infrastructure.database.repositories.agent import SqlAlchemyAgentRunRepository
from nexabot.infrastructure.database.repositories.conversations import (
    SqlAlchemyConversationRepository,
)
from nexabot.infrastructure.database.repositories.idempotency import SqlAlchemyIdempotencyRepository
from nexabot.infrastructure.database.repositories.identity import SqlAlchemyUserRepository
from nexabot.ports.repositories.conversations import MAX_MESSAGE_WINDOW
from nexabot.ports.repositories.idempotency import IdempotencyState
from tests.support import EngineRegistry, run_scenario


def test_user_repository_round_trips_roles_and_telegram_identity() -> None:
    async def scenario(engines: EngineRegistry) -> None:
        sessionmaker = await engines.sessionmaker()
        permission = Permission(namespace="tool", action="calculator")
        role = Role(name="member", permissions=frozenset({permission}))
        user = User(id=new_user_id(), roles=frozenset({role}))
        user.attach_telegram_identity(TelegramIdentity(telegram_user_id=555, username="ilya"))

        async with sessionmaker() as session:
            await SqlAlchemyUserRepository(session).add(user)
            await session.commit()

        async with sessionmaker() as session:
            repo = SqlAlchemyUserRepository(session)
            loaded = await repo.get(user.id)
            assert loaded is not None
            assert loaded.telegram_identity == TelegramIdentity(
                telegram_user_id=555, username="ilya"
            )
            assert loaded.has_permission(permission)

            by_telegram = await repo.get_by_telegram_identity(555)
            assert by_telegram is not None
            assert by_telegram.id == user.id

    run_scenario(scenario)


def test_user_repository_save_updates_status() -> None:
    async def scenario(engines: EngineRegistry) -> None:
        sessionmaker = await engines.sessionmaker()
        user = User(id=new_user_id())

        async with sessionmaker() as session:
            await SqlAlchemyUserRepository(session).add(user)
            await session.commit()

        user.ban()
        async with sessionmaker() as session:
            await SqlAlchemyUserRepository(session).save(user)
            await session.commit()

        async with sessionmaker() as session:
            loaded = await SqlAlchemyUserRepository(session).get(user.id)
            assert loaded is not None
            assert loaded.status.value == "banned"

    run_scenario(scenario)


def test_conversation_repository_persists_messages_and_bounds_recent_window() -> None:
    async def scenario(engines: EngineRegistry) -> None:
        sessionmaker = await engines.sessionmaker()
        owner = User(id=new_user_id())
        conversation = Conversation(id=new_conversation_id(), owner_id=owner.id)

        async with sessionmaker() as session:
            await SqlAlchemyUserRepository(session).add(owner)
            await SqlAlchemyConversationRepository(session).add(conversation)
            await session.commit()

        async with sessionmaker() as session:
            conv_repo = SqlAlchemyConversationRepository(session)
            loaded = await conv_repo.get(conversation.id)
            assert loaded is not None
            first = loaded.append_message(role=MessageRole.USER, content="hello")
            await conv_repo.append_message(first)
            second = loaded.append_message(role=MessageRole.ASSISTANT, content="hi there")
            await conv_repo.append_message(second)
            await session.commit()

        async with sessionmaker() as session:
            conv_repo = SqlAlchemyConversationRepository(session)
            recent = await conv_repo.recent_messages(conversation.id, limit=1)
            assert [message.content for message in recent] == ["hi there"]

            active = await conv_repo.get_active_for_user(owner.id)
            assert active is not None
            assert len(active.messages) == 2

    run_scenario(scenario)


async def _conversation_with_messages(
    sessionmaker: async_sessionmaker[AsyncSession], count: int
) -> Conversation:
    owner = User(id=new_user_id())
    conversation = Conversation(id=new_conversation_id(), owner_id=owner.id)

    async with sessionmaker() as session:
        await SqlAlchemyUserRepository(session).add(owner)
        repo = SqlAlchemyConversationRepository(session)
        await repo.add(conversation)
        for index in range(count):
            await repo.append_message(
                conversation.append_message(role=MessageRole.USER, content=f"message-{index}")
            )
        await session.commit()

    return conversation


def test_loaded_conversation_reports_that_its_history_is_partial() -> None:
    """Regression: a bounded load was indistinguishable from a complete one.

    recent_messages(100) used to return 50 messages and present them as the
    whole story, so a caller asking for more context than was loaded silently
    got a truncated conversation instead of an error.
    """

    async def scenario(engines: EngineRegistry) -> None:
        sessionmaker = await engines.sessionmaker()
        conversation = await _conversation_with_messages(sessionmaker, 12)

        async with sessionmaker() as session:
            repo = SqlAlchemyConversationRepository(session)
            loaded = await repo.get(conversation.id, message_limit=5)
            assert loaded is not None

            assert loaded.history_window == 5
            assert len(loaded.messages) == 5
            assert loaded.recent_messages(5)[-1].content == "message-11"

            with pytest.raises(ValidationError) as raised:
                loaded.recent_messages(6)
            assert raised.value.code == "conversation_history_not_loaded"

    run_scenario(scenario)


def test_a_fully_loaded_conversation_is_not_marked_partial() -> None:
    """A window nobody filled left nothing behind, so nothing is restricted."""

    async def scenario(engines: EngineRegistry) -> None:
        sessionmaker = await engines.sessionmaker()
        conversation = await _conversation_with_messages(sessionmaker, 3)

        async with sessionmaker() as session:
            loaded = await SqlAlchemyConversationRepository(session).get(
                conversation.id, message_limit=10
            )
            assert loaded is not None
            assert loaded.history_window is None
            assert len(loaded.recent_messages(500)) == 3

    run_scenario(scenario)


def test_appending_to_a_partially_loaded_conversation_widens_its_window() -> None:
    """The trailing run of known messages grew, so the claim must grow too."""

    async def scenario(engines: EngineRegistry) -> None:
        sessionmaker = await engines.sessionmaker()
        conversation = await _conversation_with_messages(sessionmaker, 8)

        async with sessionmaker() as session:
            loaded = await SqlAlchemyConversationRepository(session).get(
                conversation.id, message_limit=4
            )
            assert loaded is not None
            loaded.append_message(role=MessageRole.ASSISTANT, content="reply")

            assert loaded.history_window == 5
            assert len(loaded.recent_messages(5)) == 5

    run_scenario(scenario)


def test_message_windows_are_bounded_to_a_maximum_page_size() -> None:
    """A caller-supplied window is untrusted input, not just a hint."""

    async def scenario(engines: EngineRegistry) -> None:
        sessionmaker = await engines.sessionmaker()
        conversation = await _conversation_with_messages(sessionmaker, 2)

        async with sessionmaker() as session:
            repo = SqlAlchemyConversationRepository(session)

            with pytest.raises(ValidationError) as raised:
                await repo.recent_messages(conversation.id, MAX_MESSAGE_WINDOW + 1)
            assert raised.value.code == "message_window_too_large"

            with pytest.raises(ValidationError):
                await repo.get(conversation.id, message_limit=0)

    run_scenario(scenario)


def test_message_order_survives_identical_timestamps() -> None:
    """Ordering must be a stable total order, not backend-dependent.

    Known limitation, documented on the repository: with identical timestamps
    the tie-break is the random UUID, so the order is deterministic but not
    guaranteed to match append order.
    """

    async def scenario(engines: EngineRegistry) -> None:
        sessionmaker = await engines.sessionmaker()
        owner = User(id=new_user_id())
        conversation = Conversation(id=new_conversation_id(), owner_id=owner.id)
        stamp = utc_now()

        async with sessionmaker() as session:
            await SqlAlchemyUserRepository(session).add(owner)
            repo = SqlAlchemyConversationRepository(session)
            await repo.add(conversation)
            for index in range(6):
                await repo.append_message(
                    Message(
                        id=new_message_id(),
                        conversation_id=conversation.id,
                        role=MessageRole.USER,
                        content=f"same-instant-{index}",
                        created_at=stamp,
                    )
                )
            await session.commit()

        async def read() -> list[str]:
            async with sessionmaker() as session:
                messages = await SqlAlchemyConversationRepository(session).recent_messages(
                    conversation.id, 6
                )
                return [message.content for message in messages]

        assert await read() == await read()

    run_scenario(scenario)


def test_message_timestamps_round_trip_from_the_domain() -> None:
    """Regression: the column default overwrote the domain's append instant.

    The entity handed back to the caller and the row written to the database
    carried different timestamps, and ordering is derived from that column.
    """

    async def scenario(engines: EngineRegistry) -> None:
        sessionmaker = await engines.sessionmaker()
        owner = User(id=new_user_id())
        conversation = Conversation(id=new_conversation_id(), owner_id=owner.id)

        async with sessionmaker() as session:
            await SqlAlchemyUserRepository(session).add(owner)
            repo = SqlAlchemyConversationRepository(session)
            await repo.add(conversation)
            appended = conversation.append_message(role=MessageRole.USER, content="stamped")
            await repo.append_message(appended)
            await session.commit()

        async with sessionmaker() as session:
            loaded = await SqlAlchemyConversationRepository(session).recent_messages(
                conversation.id, 1
            )
            assert loaded[0].created_at == appended.created_at

    run_scenario(scenario)


def test_agent_run_repository_round_trips_limits_and_steps() -> None:
    async def scenario(engines: EngineRegistry) -> None:
        sessionmaker = await engines.sessionmaker()
        owner = User(id=new_user_id())
        conversation = Conversation(id=new_conversation_id(), owner_id=owner.id)
        run = AgentRun(
            id=new_agent_run_id(),
            user_id=owner.id,
            conversation_id=conversation.id,
            provider="stub",
            model="test-model",
            limits=AgentLimits(
                max_steps=3,
                max_tool_calls=1,
                max_retries=1,
                max_execution_seconds=5,
                max_context_tokens=500,
            ),
        )
        run.start()
        run.record_step(AgentStepType.PLANNING)

        async with sessionmaker() as session:
            await SqlAlchemyUserRepository(session).add(owner)
            await SqlAlchemyConversationRepository(session).add(conversation)
            await SqlAlchemyAgentRunRepository(session).add(run)
            await session.commit()

        run.record_step(AgentStepType.MODEL_CALL)
        run.finish("done", usage_metadata={"total_tokens": 10})

        async with sessionmaker() as session:
            await SqlAlchemyAgentRunRepository(session).save(run)
            await session.commit()

        async with sessionmaker() as session:
            loaded = await SqlAlchemyAgentRunRepository(session).get(run.id)
            assert loaded is not None
            assert loaded.status == AgentRunStatus.SUCCEEDED
            assert loaded.limits.max_steps == 3
            assert loaded.final_response == "done"
            assert [step.type for step in loaded.steps] == [
                AgentStepType.PLANNING,
                AgentStepType.MODEL_CALL,
                AgentStepType.FINAL_RESPONSE,
            ]

    run_scenario(scenario)


def test_idempotency_repository_start_is_idempotent_and_completes() -> None:
    async def scenario(engines: EngineRegistry) -> None:
        sessionmaker = await engines.sessionmaker()
        async with sessionmaker() as session:
            repo = SqlAlchemyIdempotencyRepository(session)
            first = await repo.start("agent-run", "key-1")
            second = await repo.start("agent-run", "key-1")
            assert first.state == IdempotencyState.STARTED
            assert second.state == IdempotencyState.STARTED

            # Only the first caller may do the work, and only it gets a token.
            assert first.owned is True
            assert first.claim_token is not None
            assert second.owned is False
            assert second.claim_token is None

            await repo.complete("agent-run", "key-1", {"ok": True}, claim_token=first.claim_token)
            await session.commit()

        async with sessionmaker() as session:
            record = await SqlAlchemyIdempotencyRepository(session).start("agent-run", "key-1")
            assert record.state == IdempotencyState.COMPLETED
            assert record.result == {"ok": True}

    run_scenario(scenario)


def test_expired_idempotency_claim_can_be_reclaimed_exactly_once() -> None:
    """A crashed attempt must not poison its key forever, nor free it twice."""

    async def scenario(engines: EngineRegistry) -> None:
        sessionmaker = await engines.sessionmaker()
        async with sessionmaker() as session:
            repo = SqlAlchemyIdempotencyRepository(session)
            original = await repo.start("agent-run", "abandoned")
            assert original.owned is True
            await session.commit()

        async with sessionmaker() as session:
            repo = SqlAlchemyIdempotencyRepository(session)

            # A claim still within its TTL stays with its owner.
            assert (await repo.start("agent-run", "abandoned")).owned is False

            # Once expired, exactly one caller takes it over.
            takeover = await repo.start("agent-run", "abandoned", claim_ttl_seconds=0)
            assert takeover.owned is True
            assert takeover.state == IdempotencyState.STARTED

            # Taking over issues a *different* token, which is what makes the
            # previous owner identifiable as stale.
            assert takeover.claim_token != original.claim_token

            # The new claim is fresh, so it is not immediately re-stealable.
            assert (await repo.start("agent-run", "abandoned")).owned is False

    run_scenario(scenario)


def test_stale_owner_cannot_settle_a_claim_that_was_taken_over() -> None:
    """Regression: complete()/fail() overwrote the newer owner's result.

    A worker that stalled past the TTL, had its claim reclaimed, then woke up
    and finished would replace the outcome the replacement worker had already
    recorded and handed to a caller.
    """

    async def scenario(engines: EngineRegistry) -> None:
        sessionmaker = await engines.sessionmaker()
        async with sessionmaker() as session:
            repo = SqlAlchemyIdempotencyRepository(session)
            stale = await repo.start("agent-run", "contended")
            assert stale.claim_token is not None
            await session.commit()

        async with sessionmaker() as session:
            repo = SqlAlchemyIdempotencyRepository(session)
            fresh = await repo.start("agent-run", "contended", claim_ttl_seconds=0)
            assert fresh.claim_token is not None
            await repo.complete(
                "agent-run", "contended", {"winner": "fresh"}, claim_token=fresh.claim_token
            )
            await session.commit()

        async with sessionmaker() as session:
            repo = SqlAlchemyIdempotencyRepository(session)

            with pytest.raises(ConflictError) as raised:
                await repo.complete(
                    "agent-run", "contended", {"winner": "stale"}, claim_token=stale.claim_token
                )
            assert raised.value.code == "idempotency_claim_lost"

            with pytest.raises(ConflictError):
                await repo.fail(
                    "agent-run", "contended", "stale failure", claim_token=stale.claim_token
                )

        async with sessionmaker() as session:
            record = await SqlAlchemyIdempotencyRepository(session).start("agent-run", "contended")
            assert record.state == IdempotencyState.COMPLETED
            assert record.result == {"winner": "fresh"}

    run_scenario(scenario)


def test_a_settled_claim_cannot_be_settled_again_by_its_own_owner() -> None:
    """The first recorded outcome is the one callers were handed; it stands."""

    async def scenario(engines: EngineRegistry) -> None:
        sessionmaker = await engines.sessionmaker()
        async with sessionmaker() as session:
            repo = SqlAlchemyIdempotencyRepository(session)
            claim = await repo.start("agent-run", "settled-twice")
            assert claim.claim_token is not None

            await repo.complete(
                "agent-run", "settled-twice", {"attempt": 1}, claim_token=claim.claim_token
            )
            await session.commit()

            with pytest.raises(ConflictError):
                await repo.fail(
                    "agent-run", "settled-twice", "changed my mind", claim_token=claim.claim_token
                )

    run_scenario(scenario)


def test_settling_an_unknown_key_is_not_found_rather_than_conflict() -> None:
    async def scenario(engines: EngineRegistry) -> None:
        sessionmaker = await engines.sessionmaker()
        async with sessionmaker() as session:
            repo = SqlAlchemyIdempotencyRepository(session)

            with pytest.raises(NotFoundError):
                await repo.complete("agent-run", "never-started", {}, claim_token="whatever")

    run_scenario(scenario)


def test_concurrent_claimers_of_one_key_produce_exactly_one_owner() -> None:
    """Two workers racing on the same key: only one may do the work."""

    async def scenario(engines: EngineRegistry) -> None:
        sessionmaker = await engines.sessionmaker()

        async def claim() -> bool:
            async with sessionmaker() as session:
                record = await SqlAlchemyIdempotencyRepository(session).start("agent-run", "race")
                await session.commit()
                return record.owned

        # SQLite serialises writers, so this exercises the contended code path
        # (existing-row detection and the IntegrityError fallback) rather than
        # true parallelism; the invariant under test is the same either way.
        outcomes = await asyncio.gather(*(claim() for _ in range(5)), return_exceptions=True)
        owners = [outcome for outcome in outcomes if outcome is True]

        assert len(owners) == 1, f"expected exactly one owner, got {outcomes}"

    run_scenario(scenario)

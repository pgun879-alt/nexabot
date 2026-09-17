"""End-to-end tests for RunAgentTurn against an in-memory SQLite substitute.

Same approach as tests/integration/test_sqlalchemy_repositories.py: a real
SQLAlchemy engine backed by aiosqlite verifies the actual persistence and
transaction behavior, without requiring a running PostgreSQL server.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from nexabot.application.agent.use_cases import RunAgentTurn, RunAgentTurnCommand
from nexabot.domain.agent.entities import AgentLimits, AgentRunStatus
from nexabot.domain.common.errors import (
    AgentExecutionError,
    AuthorizationError,
    ConflictError,
    ExternalServiceError,
    NotFoundError,
    ValidationError,
)
from nexabot.domain.common.ids import AgentRunId, new_conversation_id, new_user_id
from nexabot.domain.conversations.entities import Conversation, MessageRole
from nexabot.domain.identity.entities import TelegramIdentity, User
from nexabot.infrastructure.database import models as _models  # noqa: F401
from nexabot.infrastructure.database.base import Base
from nexabot.infrastructure.database.models import AgentRunModel
from nexabot.infrastructure.database.repositories.agent import SqlAlchemyAgentRunRepository
from nexabot.infrastructure.database.repositories.conversations import (
    SqlAlchemyConversationRepository,
)
from nexabot.infrastructure.database.repositories.idempotency import (
    SqlAlchemyIdempotencyRepository,
)
from nexabot.infrastructure.database.repositories.identity import SqlAlchemyUserRepository
from nexabot.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWorkFactory
from nexabot.infrastructure.llm.stub_gateway import StubLLMGateway
from nexabot.ports.llm.gateway import LLMResponse, LLMToolCall, LLMUsage


async def _make_sessionmaker() -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


async def _seed_user_with_conversation(
    sessionmaker: async_sessionmaker[AsyncSession], *, telegram_user_id: int
) -> tuple[User, Conversation]:
    owner = User(id=new_user_id())
    owner.attach_telegram_identity(TelegramIdentity(telegram_user_id=telegram_user_id))
    conversation = Conversation(id=new_conversation_id(), owner_id=owner.id)

    async with sessionmaker() as session:
        await SqlAlchemyUserRepository(session).add(owner)
        await SqlAlchemyConversationRepository(session).add(conversation)
        await session.commit()

    return owner, conversation


def test_successful_agent_turn_persists_messages_and_completes_run() -> None:
    async def scenario() -> None:
        sessionmaker = await _make_sessionmaker()
        owner, conversation = await _seed_user_with_conversation(sessionmaker, telegram_user_id=101)
        gateway = StubLLMGateway()
        use_case = RunAgentTurn(
            uow_factory=SqlAlchemyUnitOfWorkFactory(sessionmaker), llm_gateway=gateway
        )

        result = await use_case.execute(
            RunAgentTurnCommand(
                telegram_user_id=101,
                conversation_id=conversation.id,
                text="hello there",
                provider="stub",
                model="test-model",
            )
        )

        assert result.status == AgentRunStatus.SUCCEEDED
        assert result.response == "Echo: hello there"
        assert len(gateway.calls) == 1

        async with sessionmaker() as session:
            loaded_conversation = await SqlAlchemyConversationRepository(session).get(
                conversation.id
            )
            assert loaded_conversation is not None
            assert [m.role for m in loaded_conversation.messages] == [
                MessageRole.USER,
                MessageRole.ASSISTANT,
            ]
            assert loaded_conversation.messages[-1].id == result.assistant_message_id
            assert loaded_conversation.messages[-1].content == "Echo: hello there"

            loaded_run = await SqlAlchemyAgentRunRepository(session).get(result.agent_run_id)
            assert loaded_run is not None
            assert loaded_run.status == AgentRunStatus.SUCCEEDED
            assert loaded_run.user_id == owner.id
            assert loaded_run.final_response == "Echo: hello there"
            assert loaded_run.usage_metadata["total_tokens"] > 0
            assert [step.type.value for step in loaded_run.steps] == [
                "planning",
                "model_call",
                "validation",
                "final_response",
            ]

    asyncio.run(scenario())


def test_invalid_input_fails_before_any_side_effect() -> None:
    async def scenario() -> None:
        sessionmaker = await _make_sessionmaker()
        _owner, conversation = await _seed_user_with_conversation(
            sessionmaker, telegram_user_id=102
        )
        use_case = RunAgentTurn(
            uow_factory=SqlAlchemyUnitOfWorkFactory(sessionmaker), llm_gateway=StubLLMGateway()
        )

        with pytest.raises(ValidationError):
            await use_case.execute(
                RunAgentTurnCommand(
                    telegram_user_id=102,
                    conversation_id=conversation.id,
                    text="   ",
                    provider="stub",
                    model="test-model",
                )
            )

        async with sessionmaker() as session:
            loaded = await SqlAlchemyConversationRepository(session).get(conversation.id)
            assert loaded is not None
            assert loaded.messages == ()

    asyncio.run(scenario())


def test_missing_conversation_raises_not_found() -> None:
    async def scenario() -> None:
        sessionmaker = await _make_sessionmaker()
        await _seed_user_with_conversation(sessionmaker, telegram_user_id=103)
        use_case = RunAgentTurn(
            uow_factory=SqlAlchemyUnitOfWorkFactory(sessionmaker), llm_gateway=StubLLMGateway()
        )

        with pytest.raises(NotFoundError):
            await use_case.execute(
                RunAgentTurnCommand(
                    telegram_user_id=103,
                    conversation_id=new_conversation_id(),
                    text="hello",
                    provider="stub",
                    model="test-model",
                )
            )

    asyncio.run(scenario())


def test_user_cannot_run_a_turn_against_another_users_conversation() -> None:
    async def scenario() -> None:
        sessionmaker = await _make_sessionmaker()
        _owner_a, conversation_a = await _seed_user_with_conversation(
            sessionmaker, telegram_user_id=201
        )
        await _seed_user_with_conversation(sessionmaker, telegram_user_id=202)

        use_case = RunAgentTurn(
            uow_factory=SqlAlchemyUnitOfWorkFactory(sessionmaker), llm_gateway=StubLLMGateway()
        )

        with pytest.raises(AuthorizationError):
            await use_case.execute(
                RunAgentTurnCommand(
                    telegram_user_id=202,
                    conversation_id=conversation_a.id,
                    text="not yours",
                    provider="stub",
                    model="test-model",
                )
            )

        async with sessionmaker() as session:
            loaded = await SqlAlchemyConversationRepository(session).get(conversation_a.id)
            assert loaded is not None
            assert loaded.messages == ()

    asyncio.run(scenario())


def test_llm_failure_marks_agent_run_failed_and_keeps_state_consistent() -> None:
    async def scenario() -> None:
        sessionmaker = await _make_sessionmaker()
        await _seed_user_with_conversation(sessionmaker, telegram_user_id=301)
        _owner, conversation = await _seed_user_with_conversation(
            sessionmaker, telegram_user_id=302
        )
        gateway = StubLLMGateway(fail_with=ExternalServiceError("provider is down"))
        use_case = RunAgentTurn(
            uow_factory=SqlAlchemyUnitOfWorkFactory(sessionmaker), llm_gateway=gateway
        )

        with pytest.raises(ExternalServiceError):
            await use_case.execute(
                RunAgentTurnCommand(
                    telegram_user_id=302,
                    conversation_id=conversation.id,
                    text="hello",
                    provider="stub",
                    model="test-model",
                )
            )

        async with sessionmaker() as session:
            loaded_conversation = await SqlAlchemyConversationRepository(session).get(
                conversation.id
            )
            assert loaded_conversation is not None
            assert [m.role for m in loaded_conversation.messages] == [MessageRole.USER]

            row = (
                await session.execute(
                    sa.select(AgentRunModel).where(
                        AgentRunModel.conversation_id == str(conversation.id)
                    )
                )
            ).scalar_one()
            loaded_run = await SqlAlchemyAgentRunRepository(session).get(AgentRunId(UUID(row.id)))
            assert loaded_run is not None
            assert loaded_run.status == AgentRunStatus.FAILED
            assert loaded_run.failure_reason == "provider is down"
            assert loaded_run.final_response is None

    asyncio.run(scenario())


def test_tool_call_response_fails_cleanly_as_extension_point() -> None:
    async def scenario() -> None:
        sessionmaker = await _make_sessionmaker()
        _owner, conversation = await _seed_user_with_conversation(
            sessionmaker, telegram_user_id=401
        )
        gateway = StubLLMGateway(
            default_response=LLMResponse(
                content=None,
                tool_calls=(LLMToolCall(id="call-1", name="lookup", arguments={"q": "weather"}),),
                usage=LLMUsage(),
            )
        )
        use_case = RunAgentTurn(
            uow_factory=SqlAlchemyUnitOfWorkFactory(sessionmaker), llm_gateway=gateway
        )

        with pytest.raises(AgentExecutionError) as raised:
            await use_case.execute(
                RunAgentTurnCommand(
                    telegram_user_id=401,
                    conversation_id=conversation.id,
                    text="what's the weather",
                    provider="stub",
                    model="test-model",
                )
            )

        assert raised.value.code == "agent_tool_execution_unsupported"

        async with sessionmaker() as session:
            loaded_conversation = await SqlAlchemyConversationRepository(session).get(
                conversation.id
            )
            assert loaded_conversation is not None
            assert [m.role for m in loaded_conversation.messages] == [MessageRole.USER]

    asyncio.run(scenario())


class _FailOnNthUnitOfWork:
    """Unit-of-work factory that blows up on one specific transaction.

    Used to simulate a database failure during final persistence, which is the
    window where a run could previously be stranded in RUNNING forever.
    """

    def __init__(self, inner: SqlAlchemyUnitOfWorkFactory, *, fail_on_call: int) -> None:
        self._inner = inner
        self._fail_on_call = fail_on_call
        self.calls = 0

    def __call__(self) -> object:
        self.calls += 1
        if self.calls == self._fail_on_call:
            raise ExternalServiceError("database unavailable")
        return self._inner()


def test_tight_step_budget_does_not_discard_a_successful_turn() -> None:
    """Regression: max_steps <= 3 used to fail the turn after the model replied."""

    async def scenario() -> None:
        sessionmaker = await _make_sessionmaker()
        _owner, conversation = await _seed_user_with_conversation(
            sessionmaker, telegram_user_id=601
        )
        gateway = StubLLMGateway()
        use_case = RunAgentTurn(
            uow_factory=SqlAlchemyUnitOfWorkFactory(sessionmaker), llm_gateway=gateway
        )

        result = await use_case.execute(
            RunAgentTurnCommand(
                telegram_user_id=601,
                conversation_id=conversation.id,
                text="tight budget",
                provider="stub",
                model="test-model",
                limits=AgentLimits(max_steps=3),
            )
        )

        assert result.status == AgentRunStatus.SUCCEEDED

        async with sessionmaker() as session:
            loaded_conversation = await SqlAlchemyConversationRepository(session).get(
                conversation.id
            )
            assert loaded_conversation is not None
            assert [m.role for m in loaded_conversation.messages] == [
                MessageRole.USER,
                MessageRole.ASSISTANT,
            ]

            loaded_run = await SqlAlchemyAgentRunRepository(session).get(result.agent_run_id)
            assert loaded_run is not None
            assert loaded_run.status == AgentRunStatus.SUCCEEDED

    asyncio.run(scenario())


def test_failure_while_persisting_the_result_still_settles_the_run() -> None:
    """Regression: a run must never be left stranded in RUNNING."""

    async def scenario() -> None:
        sessionmaker = await _make_sessionmaker()
        _owner, conversation = await _seed_user_with_conversation(
            sessionmaker, telegram_user_id=602
        )
        # Call 1 starts the turn, call 2 persists the result, call 3 settles.
        factory = _FailOnNthUnitOfWork(
            SqlAlchemyUnitOfWorkFactory(sessionmaker), fail_on_call=2
        )
        use_case = RunAgentTurn(
            uow_factory=factory,  # type: ignore[arg-type]
            llm_gateway=StubLLMGateway(),
        )

        with pytest.raises(ExternalServiceError):
            await use_case.execute(
                RunAgentTurnCommand(
                    telegram_user_id=602,
                    conversation_id=conversation.id,
                    text="persistence will fail",
                    provider="stub",
                    model="test-model",
                )
            )

        async with sessionmaker() as session:
            row = (
                await session.execute(
                    sa.select(AgentRunModel).where(
                        AgentRunModel.conversation_id == str(conversation.id)
                    )
                )
            ).scalar_one()
            loaded_run = await SqlAlchemyAgentRunRepository(session).get(AgentRunId(UUID(row.id)))
            assert loaded_run is not None
            assert loaded_run.status == AgentRunStatus.FAILED
            assert loaded_run.failure_reason == "database unavailable"
            assert loaded_run.final_response is None

            loaded_conversation = await SqlAlchemyConversationRepository(session).get(
                conversation.id
            )
            assert loaded_conversation is not None
            assert [m.role for m in loaded_conversation.messages] == [MessageRole.USER]

    asyncio.run(scenario())


def test_turn_refuses_to_run_while_another_attempt_holds_the_claim() -> None:
    """Regression: a STARTED claim used to be read as 'go ahead'.

    A retried Telegram update or a second concurrent worker would then make a
    second paid model call and persist a duplicate assistant message.
    """

    async def scenario() -> None:
        sessionmaker = await _make_sessionmaker()
        _owner, conversation = await _seed_user_with_conversation(
            sessionmaker, telegram_user_id=603
        )

        # Stand in for an attempt that claimed the key and is still in flight.
        async with sessionmaker() as session:
            await SqlAlchemyIdempotencyRepository(session).start("agent_run", "tg-update-42")
            await session.commit()

        gateway = StubLLMGateway()
        use_case = RunAgentTurn(
            uow_factory=SqlAlchemyUnitOfWorkFactory(sessionmaker), llm_gateway=gateway
        )

        with pytest.raises(ConflictError) as raised:
            await use_case.execute(
                RunAgentTurnCommand(
                    telegram_user_id=603,
                    conversation_id=conversation.id,
                    text="duplicate delivery",
                    provider="stub",
                    model="test-model",
                    idempotency_key="tg-update-42",
                )
            )

        assert raised.value.code == "agent_run_in_progress"
        assert gateway.calls == []

        async with sessionmaker() as session:
            loaded = await SqlAlchemyConversationRepository(session).get(conversation.id)
            assert loaded is not None
            assert loaded.messages == ()

            runs = (
                await session.execute(sa.select(sa.func.count()).select_from(AgentRunModel))
            ).scalar_one()
            assert runs == 0

    asyncio.run(scenario())


def test_idempotent_replay_does_not_duplicate_agent_runs_or_messages() -> None:
    async def scenario() -> None:
        sessionmaker = await _make_sessionmaker()
        _owner, conversation = await _seed_user_with_conversation(
            sessionmaker, telegram_user_id=501
        )
        gateway = StubLLMGateway()
        use_case = RunAgentTurn(
            uow_factory=SqlAlchemyUnitOfWorkFactory(sessionmaker), llm_gateway=gateway
        )
        command = RunAgentTurnCommand(
            telegram_user_id=501,
            conversation_id=conversation.id,
            text="idempotent hello",
            provider="stub",
            model="test-model",
            idempotency_key="turn-1",
        )

        first = await use_case.execute(command)
        second = await use_case.execute(command)

        assert first == second
        assert len(gateway.calls) == 1

        async with sessionmaker() as session:
            loaded_conversation = await SqlAlchemyConversationRepository(session).get(
                conversation.id
            )
            assert loaded_conversation is not None
            assert len(loaded_conversation.messages) == 2

    asyncio.run(scenario())

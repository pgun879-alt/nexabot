"""End-to-end tests for RunAgentTurn against an in-memory SQLite substitute.

Same approach as tests/integration/test_sqlalchemy_repositories.py: a real
SQLAlchemy engine backed by aiosqlite verifies the actual persistence and
transaction behavior, without requiring a running PostgreSQL server.
"""

from __future__ import annotations

import asyncio
import time
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexabot.application.agent.use_cases import (
    MAX_INPUT_CHARACTERS,
    RunAgentTurn,
    RunAgentTurnCommand,
)
from nexabot.domain.agent.context import estimate_tokens
from nexabot.domain.agent.entities import AgentLimits, AgentRun, AgentRunStatus
from nexabot.domain.common.errors import (
    AgentExecutionError,
    AuthorizationError,
    ConflictError,
    ExternalServiceError,
    NotFoundError,
    ValidationError,
)
from nexabot.domain.common.ids import (
    AgentRunId,
    new_agent_run_id,
    new_conversation_id,
    new_message_id,
    new_user_id,
)
from nexabot.domain.conversations.entities import Conversation, MessageRole
from nexabot.domain.identity.entities import TelegramIdentity, User
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
from nexabot.ports.llm.gateway import LLMRequest, LLMResponse, LLMToolCall, LLMUsage
from tests.support import EngineRegistry, run_scenario


async def _only_run(session: AsyncSession) -> AgentRun:
    """The single agent run these single-turn scenarios produce."""
    row = (await session.execute(sa.select(AgentRunModel))).scalar_one()
    run = await SqlAlchemyAgentRunRepository(session).get(AgentRunId(UUID(row.id)))
    assert run is not None
    return run


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
    async def scenario(engines: EngineRegistry) -> None:
        sessionmaker = await engines.sessionmaker()
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

    run_scenario(scenario)


def test_invalid_input_fails_before_any_side_effect() -> None:
    async def scenario(engines: EngineRegistry) -> None:
        sessionmaker = await engines.sessionmaker()
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

    run_scenario(scenario)


def test_missing_conversation_raises_not_found() -> None:
    async def scenario(engines: EngineRegistry) -> None:
        sessionmaker = await engines.sessionmaker()
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

    run_scenario(scenario)


def test_user_cannot_run_a_turn_against_another_users_conversation() -> None:
    async def scenario(engines: EngineRegistry) -> None:
        sessionmaker = await engines.sessionmaker()
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

    run_scenario(scenario)


def test_llm_failure_marks_agent_run_failed_and_keeps_state_consistent() -> None:
    async def scenario(engines: EngineRegistry) -> None:
        sessionmaker = await engines.sessionmaker()
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

    run_scenario(scenario)


def test_tool_call_response_fails_cleanly_as_extension_point() -> None:
    async def scenario(engines: EngineRegistry) -> None:
        sessionmaker = await engines.sessionmaker()
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

    run_scenario(scenario)


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

    async def scenario(engines: EngineRegistry) -> None:
        sessionmaker = await engines.sessionmaker()
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

    run_scenario(scenario)


def test_failure_while_persisting_the_result_still_settles_the_run() -> None:
    """Regression: a run must never be left stranded in RUNNING."""

    async def scenario(engines: EngineRegistry) -> None:
        sessionmaker = await engines.sessionmaker()
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

    run_scenario(scenario)


def test_turn_refuses_to_run_while_another_attempt_holds_the_claim() -> None:
    """Regression: a STARTED claim used to be read as 'go ahead'.

    A retried Telegram update or a second concurrent worker would then make a
    second paid model call and persist a duplicate assistant message.
    """

    async def scenario(engines: EngineRegistry) -> None:
        sessionmaker = await engines.sessionmaker()
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

    run_scenario(scenario)


def test_idempotent_replay_does_not_duplicate_agent_runs_or_messages() -> None:
    async def scenario(engines: EngineRegistry) -> None:
        sessionmaker = await engines.sessionmaker()
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

    run_scenario(scenario)


def test_a_hanging_provider_is_cut_off_by_the_execution_budget() -> None:
    """Regression: the model call was unbounded.

    A provider that accepts the connection and then stalls held the turn, its
    worker, and its idempotency claim open indefinitely.
    """

    class _HangingGateway:
        def __init__(self) -> None:
            self.cancelled = False

        async def complete(self, request: LLMRequest) -> LLMResponse:
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                self.cancelled = True
                raise
            raise AssertionError("unreachable")

    async def scenario(engines: EngineRegistry) -> None:
        sessionmaker = await engines.sessionmaker()
        _owner, conversation = await _seed_user_with_conversation(
            sessionmaker, telegram_user_id=701
        )
        gateway = _HangingGateway()
        use_case = RunAgentTurn(
            uow_factory=SqlAlchemyUnitOfWorkFactory(sessionmaker),
            llm_gateway=gateway,  # type: ignore[arg-type]
        )

        started = time.monotonic()
        with pytest.raises(ExternalServiceError) as raised:
            await use_case.execute(
                RunAgentTurnCommand(
                    telegram_user_id=701,
                    conversation_id=conversation.id,
                    text="please hang",
                    provider="stub",
                    model="test-model",
                    limits=AgentLimits(max_execution_seconds=1),
                )
            )
        elapsed = time.monotonic() - started

        assert raised.value.code == "llm_timeout"
        assert raised.value.retryable is True
        assert elapsed < 10, "the deadline did not actually cut the call off"
        assert gateway.cancelled is True

        async with sessionmaker() as session:
            loaded_run = await _only_run(session)
            assert loaded_run.status == AgentRunStatus.FAILED
            assert loaded_run.failure_reason == "The model provider did not respond in time."

            loaded_conversation = await SqlAlchemyConversationRepository(session).get(
                conversation.id
            )
            assert loaded_conversation is not None
            assert [m.role for m in loaded_conversation.messages] == [MessageRole.USER]

    run_scenario(scenario)


def test_provider_exception_text_is_never_persisted() -> None:
    """Regression: str(exc) became the run's failure_reason.

    Provider SDK exceptions routinely quote the request URL, headers, or an
    echoed API key, all of which went straight into the database.
    """

    async def scenario(engines: EngineRegistry) -> None:
        sessionmaker = await engines.sessionmaker()
        _owner, conversation = await _seed_user_with_conversation(
            sessionmaker, telegram_user_id=702
        )
        leaky = RuntimeError("401 from https://api.example/v1 (Authorization: Bearer sk-live-XYZ)")
        use_case = RunAgentTurn(
            uow_factory=SqlAlchemyUnitOfWorkFactory(sessionmaker),
            llm_gateway=StubLLMGateway(fail_with=leaky),
        )

        with pytest.raises(ExternalServiceError) as raised:
            await use_case.execute(
                RunAgentTurnCommand(
                    telegram_user_id=702,
                    conversation_id=conversation.id,
                    text="leak please",
                    provider="stub",
                    model="test-model",
                )
            )

        # Normalized for the caller: the class name survives, the text does not.
        assert raised.value.code == "llm_provider_error"
        assert raised.value.retryable is False
        assert raised.value.details["error_type"] == "RuntimeError"
        assert "sk-live-XYZ" not in str(raised.value.to_safe_dict())
        assert raised.value.__cause__ is leaky

        async with sessionmaker() as session:
            loaded_run = await _only_run(session)
            assert loaded_run.status == AgentRunStatus.FAILED
            assert loaded_run.failure_reason is not None
            assert "sk-live-XYZ" not in loaded_run.failure_reason
            assert "api.example" not in loaded_run.failure_reason

    run_scenario(scenario)


def test_context_is_trimmed_to_the_token_budget_not_the_message_count() -> None:
    """Regression: max_context_tokens was stored but never enforced.

    Context was bounded by message *count*, so a handful of very long messages
    blew straight through a budget the run recorded and believed it had.
    """

    async def scenario(engines: EngineRegistry) -> None:
        sessionmaker = await engines.sessionmaker()
        _owner, conversation = await _seed_user_with_conversation(
            sessionmaker, telegram_user_id=703
        )

        # Ten long messages: well inside the message-count window, far outside
        # any small token budget.
        async with sessionmaker() as session:
            repo = SqlAlchemyConversationRepository(session)
            loaded = await repo.get(conversation.id)
            assert loaded is not None
            for index in range(10):
                await repo.append_message(
                    loaded.append_message(role=MessageRole.USER, content=f"{index} " + "x" * 600)
                )
            await session.commit()

        gateway = StubLLMGateway()
        use_case = RunAgentTurn(
            uow_factory=SqlAlchemyUnitOfWorkFactory(sessionmaker), llm_gateway=gateway
        )

        await use_case.execute(
            RunAgentTurnCommand(
                telegram_user_id=703,
                conversation_id=conversation.id,
                text="short question",
                provider="stub",
                model="test-model",
                limits=AgentLimits(max_context_tokens=900),
                context_window=20,
            )
        )

        (request,) = gateway.calls
        sent = sum(estimate_tokens(message.content) for message in request.messages)
        assert sent <= 900, f"context budget blown: {sent} tokens"

        # Trimming keeps the newest messages, so the turn's own input survives.
        assert request.messages[-1].content == "short question"
        # ...and it really did drop history rather than send all eleven.
        assert len(request.messages) < 12

    run_scenario(scenario)


def test_input_that_cannot_fit_the_budget_is_rejected_before_any_side_effect() -> None:
    async def scenario(engines: EngineRegistry) -> None:
        sessionmaker = await engines.sessionmaker()
        _owner, conversation = await _seed_user_with_conversation(
            sessionmaker, telegram_user_id=704
        )
        gateway = StubLLMGateway()
        use_case = RunAgentTurn(
            uow_factory=SqlAlchemyUnitOfWorkFactory(sessionmaker), llm_gateway=gateway
        )

        with pytest.raises(ValidationError) as raised:
            await use_case.execute(
                RunAgentTurnCommand(
                    telegram_user_id=704,
                    conversation_id=conversation.id,
                    text="y" * 5_000,
                    provider="stub",
                    model="test-model",
                    limits=AgentLimits(max_context_tokens=100),
                )
            )

        assert raised.value.code == "agent_turn_input_exceeds_context_budget"
        assert gateway.calls == []

        async with sessionmaker() as session:
            loaded = await SqlAlchemyConversationRepository(session).get(conversation.id)
            assert loaded is not None
            assert loaded.messages == ()

    run_scenario(scenario)


def test_oversized_input_is_rejected_before_it_is_stored() -> None:
    async def scenario(engines: EngineRegistry) -> None:
        sessionmaker = await engines.sessionmaker()
        _owner, conversation = await _seed_user_with_conversation(
            sessionmaker, telegram_user_id=705
        )
        use_case = RunAgentTurn(
            uow_factory=SqlAlchemyUnitOfWorkFactory(sessionmaker), llm_gateway=StubLLMGateway()
        )

        with pytest.raises(ValidationError) as raised:
            await use_case.execute(
                RunAgentTurnCommand(
                    telegram_user_id=705,
                    conversation_id=conversation.id,
                    text="z" * (MAX_INPUT_CHARACTERS + 1),
                    provider="stub",
                    model="test-model",
                    limits=AgentLimits(max_context_tokens=10_000_000),
                )
            )

        assert raised.value.code == "agent_turn_input_too_long"

    run_scenario(scenario)


def test_a_stale_worker_cannot_overwrite_the_newer_attempts_result() -> None:
    """Regression: settlement ignored who owned the claim.

    A worker that stalled past the claim TTL, had its claim taken over, then
    woke up and finished would persist a *second* assistant message and
    overwrite the result the replacement had already handed to a caller.
    """

    async def scenario(engines: EngineRegistry) -> None:
        sessionmaker = await engines.sessionmaker()
        _owner, conversation = await _seed_user_with_conversation(
            sessionmaker, telegram_user_id=706
        )
        uow_factory = SqlAlchemyUnitOfWorkFactory(sessionmaker)

        # The stale worker claims the key, then stalls.
        async with sessionmaker() as session:
            stale_claim = await SqlAlchemyIdempotencyRepository(session).start(
                "agent_run", "tg-update-99"
            )
            assert stale_claim.claim_token is not None
            await session.commit()

        # A replacement reclaims the expired claim and completes the turn.
        async with sessionmaker() as session:
            fresh_claim = await SqlAlchemyIdempotencyRepository(session).start(
                "agent_run", "tg-update-99", claim_ttl_seconds=0
            )
            assert fresh_claim.claim_token is not None
            await SqlAlchemyIdempotencyRepository(session).complete(
                "agent_run",
                "tg-update-99",
                {
                    "agent_run_id": str(new_agent_run_id()),
                    "conversation_id": str(conversation.id),
                    "assistant_message_id": str(new_message_id()),
                    "response": "answer from the replacement worker",
                    "status": "succeeded",
                    "usage_metadata": {},
                },
                claim_token=fresh_claim.claim_token,
            )
            await session.commit()

        # The stale worker wakes up and retries the same update.
        gateway = StubLLMGateway()
        result = await RunAgentTurn(uow_factory=uow_factory, llm_gateway=gateway).execute(
            RunAgentTurnCommand(
                telegram_user_id=706,
                conversation_id=conversation.id,
                text="duplicate delivery",
                provider="stub",
                model="test-model",
                idempotency_key="tg-update-99",
            )
        )

        # It is handed the completed result instead of redoing the work.
        assert result.response == "answer from the replacement worker"
        assert gateway.calls == []

        async with sessionmaker() as session:
            loaded = await SqlAlchemyConversationRepository(session).get(conversation.id)
            assert loaded is not None
            assert loaded.messages == ()

    run_scenario(scenario)


def test_losing_the_claim_mid_turn_prevents_a_duplicate_assistant_message() -> None:
    """The claim is taken over *while* this attempt is calling the model.

    Persisting the assistant message anyway would produce exactly the duplicate
    the idempotency key exists to prevent, so the whole settlement is refused.
    """

    async def scenario(engines: EngineRegistry) -> None:
        sessionmaker = await engines.sessionmaker()
        _owner, conversation = await _seed_user_with_conversation(
            sessionmaker, telegram_user_id=707
        )

        stolen = False

        class _StealTheClaimMidCall:
            async def complete(self, request: LLMRequest) -> LLMResponse:
                nonlocal stolen
                async with sessionmaker() as session:
                    await SqlAlchemyIdempotencyRepository(session).start(
                        "agent_run", "tg-update-100", claim_ttl_seconds=0
                    )
                    await session.commit()
                stolen = True
                return LLMResponse(content="an answer nobody should see", usage=LLMUsage())

        use_case = RunAgentTurn(
            uow_factory=SqlAlchemyUnitOfWorkFactory(sessionmaker),
            llm_gateway=_StealTheClaimMidCall(),  # type: ignore[arg-type]
        )

        with pytest.raises(ConflictError) as raised:
            await use_case.execute(
                RunAgentTurnCommand(
                    telegram_user_id=707,
                    conversation_id=conversation.id,
                    text="racing",
                    provider="stub",
                    model="test-model",
                    idempotency_key="tg-update-100",
                )
            )

        assert stolen is True
        assert raised.value.code == "idempotency_claim_lost"

        async with sessionmaker() as session:
            loaded = await SqlAlchemyConversationRepository(session).get(conversation.id)
            assert loaded is not None
            # The user message stands; the assistant message was rolled back.
            assert [m.role for m in loaded.messages] == [MessageRole.USER]

            loaded_run = await _only_run(session)
            assert loaded_run.status == AgentRunStatus.FAILED
            assert loaded_run.final_response is None

    run_scenario(scenario)


def test_a_failed_turn_always_leaves_a_settled_run() -> None:
    """A started run may never be left stranded in RUNNING."""

    async def scenario(engines: EngineRegistry) -> None:
        sessionmaker = await engines.sessionmaker()
        _owner, conversation = await _seed_user_with_conversation(
            sessionmaker, telegram_user_id=708
        )
        use_case = RunAgentTurn(
            uow_factory=SqlAlchemyUnitOfWorkFactory(sessionmaker),
            llm_gateway=StubLLMGateway(default_response=LLMResponse(content="   ")),
        )

        with pytest.raises(AgentExecutionError) as raised:
            await use_case.execute(
                RunAgentTurnCommand(
                    telegram_user_id=708,
                    conversation_id=conversation.id,
                    text="empty reply please",
                    provider="stub",
                    model="test-model",
                )
            )

        assert raised.value.code == "agent_run_empty_response"

        async with sessionmaker() as session:
            loaded_run = await _only_run(session)
            assert loaded_run.status == AgentRunStatus.FAILED
            assert loaded_run.finished_at is not None
            assert [step.type.value for step in loaded_run.steps][-1] == "error"

    run_scenario(scenario)

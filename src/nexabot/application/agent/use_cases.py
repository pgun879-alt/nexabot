"""Agent orchestration application use case.

RunAgentTurn is the first end-to-end path connecting an incoming user message
to a persisted assistant response:

    resolve actor -> resolve conversation -> normalize input -> build context
    -> start AgentRun -> call LLMGateway -> (tool loop: extension point)
    -> persist assistant response -> finalize AgentRun

Only domain entities and ports are used here. No SQLAlchemy, Telegram, or
provider SDK details may appear in this module.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from uuid import UUID

from nexabot.application.identity.use_cases import ResolveCurrentActor
from nexabot.domain.agent.context import estimate_tokens, select_within_token_budget
from nexabot.domain.agent.entities import AgentLimits, AgentRun, AgentRunStatus, AgentStepType
from nexabot.domain.common.errors import (
    AgentExecutionError,
    AuthorizationError,
    ConflictError,
    ExternalServiceError,
    NexaBotError,
    NotFoundError,
    ValidationError,
)
from nexabot.domain.common.ids import (
    AgentRunId,
    ConversationId,
    MessageId,
    new_agent_run_id,
)
from nexabot.domain.common.time import utc_now
from nexabot.domain.conversations.entities import Conversation, MessageRole
from nexabot.ports.llm.gateway import (
    LLMGateway,
    LLMMessage,
    LLMMessageRole,
    LLMRequest,
    LLMResponse,
)
from nexabot.ports.repositories.conversations import MAX_MESSAGE_WINDOW
from nexabot.ports.repositories.idempotency import IdempotencyState
from nexabot.ports.unit_of_work import UnitOfWorkFactory

logger = logging.getLogger(__name__)

IDEMPOTENCY_NAMESPACE = "agent_run"

MAX_INPUT_CHARACTERS = 32_000
"""Hard ceiling on one turn's input, independent of the token budget.

The token budget decides how much *history* travels with a request; this
rejects a single oversized message before it is written to the database or
estimated at all, so the size of an incoming update cannot be used to drive
memory or storage growth.
"""

_TERMINAL_RUN_STATUSES = frozenset(
    {AgentRunStatus.SUCCEEDED, AgentRunStatus.FAILED, AgentRunStatus.CANCELLED}
)

_DOMAIN_TO_LLM_ROLE: Mapping[MessageRole, LLMMessageRole] = {
    MessageRole.SYSTEM: LLMMessageRole.SYSTEM,
    MessageRole.USER: LLMMessageRole.USER,
    MessageRole.ASSISTANT: LLMMessageRole.ASSISTANT,
    MessageRole.TOOL: LLMMessageRole.TOOL,
}

DEFAULT_SYSTEM_PROMPT = "You are NexaBot, a helpful and safe assistant."


@dataclass(frozen=True, slots=True)
class _Claim:
    """The idempotency claim this attempt holds and may settle."""

    key: str
    token: str


@dataclass(frozen=True, slots=True)
class RunAgentTurnCommand:
    telegram_user_id: int
    conversation_id: ConversationId
    text: str
    provider: str
    model: str
    limits: AgentLimits = field(default_factory=AgentLimits)
    idempotency_key: str | None = None
    context_window: int = 20
    """Upper bound on how many past messages are even considered.

    This is a load bound, not the context limit: ``limits.max_context_tokens``
    decides how many of them actually travel with the request.
    """


@dataclass(frozen=True, slots=True)
class AgentTurnResult:
    agent_run_id: AgentRunId
    conversation_id: ConversationId
    assistant_message_id: MessageId
    response: str
    status: AgentRunStatus
    usage_metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class RunAgentTurn:
    """Runs one agent turn: user message in, assistant message out.

    Transaction boundaries are deliberately short: one unit of work persists
    the user message and the started AgentRun, the LLM call happens with no
    database transaction open, and a second unit of work persists the
    outcome (success or failure). This keeps a slow/external model call from
    holding a database transaction open.
    """

    uow_factory: UnitOfWorkFactory
    llm_gateway: LLMGateway
    system_prompt: str = DEFAULT_SYSTEM_PROMPT

    async def execute(self, command: RunAgentTurnCommand) -> AgentTurnResult:
        text = self._validate_input(command)

        claim: _Claim | None = None
        if command.idempotency_key is not None:
            cached, claim = await self._check_idempotency(command.idempotency_key)
            if cached is not None:
                return cached

        conversation, run = await self._start_turn(command, text)

        try:
            llm_request = self._build_request(command, conversation)
            llm_response = await self._call_model(run, llm_request)
            content = self._validate_response(llm_response)
            return await self._finish_turn(run, conversation, llm_response, content, claim)
        except Exception as exc:
            # No exit from a started run may leave it stranded in RUNNING, and
            # no settlement problem may replace the error the caller needs.
            failure = _normalize_failure(exc)
            await self._fail_turn(run, claim, reason=failure.safe_message)
            if failure is exc:
                raise
            raise failure from exc

    def _validate_input(self, command: RunAgentTurnCommand) -> str:
        text = command.text.strip()
        if not text:
            raise ValidationError("Agent turn input is required.", code="agent_turn_input_required")
        if len(text) > MAX_INPUT_CHARACTERS:
            raise ValidationError(
                "Agent turn input is too long.",
                code="agent_turn_input_too_long",
                maximum_characters=MAX_INPUT_CHARACTERS,
            )
        if command.context_window <= 0 or command.context_window > MAX_MESSAGE_WINDOW:
            raise ValidationError(
                "Context window is out of range.",
                code="agent_context_window_invalid",
                maximum=MAX_MESSAGE_WINDOW,
            )
        # A message that cannot fit the budget on its own can never be answered:
        # every context built for it would drop the very text being replied to.
        if estimate_tokens(text) > command.limits.max_context_tokens:
            raise ValidationError(
                "Agent turn input exceeds the context token budget.",
                code="agent_turn_input_exceeds_context_budget",
                max_context_tokens=command.limits.max_context_tokens,
            )
        return text

    async def _check_idempotency(self, key: str) -> tuple[AgentTurnResult | None, _Claim | None]:
        async with self.uow_factory() as uow:
            record = await uow.idempotency.start(IDEMPOTENCY_NAMESPACE, key)

        if record.state == IdempotencyState.COMPLETED:
            return _result_from_stored(record.result), None
        if record.state == IdempotencyState.FAILED:
            raise ConflictError(
                "A previous attempt for this request failed and was not retried.",
                code="agent_run_idempotency_failed",
            )
        if not record.owned or record.claim_token is None:
            # Another attempt holds the claim. Proceeding would duplicate the
            # model call and the assistant message this key exists to prevent.
            raise ConflictError(
                "This request is already being processed.",
                code="agent_run_in_progress",
            )
        return None, _Claim(key=key, token=record.claim_token)

    async def _call_model(self, run: AgentRun, request: LLMRequest) -> LLMResponse:
        """Call the provider under the run's execution deadline.

        ``max_execution_seconds`` is a property of the whole turn, so the
        budget handed to the provider is what is left of it, not the full
        allowance. Without this the call is unbounded: a provider that accepts
        a connection and then stalls holds the turn, its worker, and its
        idempotency claim open until something else times out.
        """
        remaining = self._remaining_seconds(run)
        if remaining <= 0:
            raise ExternalServiceError(
                "The agent run exceeded its execution budget.",
                code="agent_run_deadline_exceeded",
                retryable=True,
            )
        try:
            async with asyncio.timeout(remaining):
                return await self.llm_gateway.complete(request)
        except TimeoutError as exc:
            raise ExternalServiceError(
                "The model provider did not respond in time.",
                code="llm_timeout",
                retryable=True,
            ) from exc

    @staticmethod
    def _remaining_seconds(run: AgentRun) -> float:
        budget = float(run.limits.max_execution_seconds)
        if run.started_at is None:  # pragma: no cover - start() always sets it
            return budget
        return budget - (utc_now() - run.started_at).total_seconds()

    @staticmethod
    def _validate_response(response: LLMResponse) -> str:
        if response.tool_calls:
            raise AgentExecutionError(
                "The model requested a tool call, but no ToolExecutor port is wired yet.",
                code="agent_tool_execution_unsupported",
            )
        content = (response.content or "").strip()
        if not content:
            raise AgentExecutionError(
                "The model returned an empty response.", code="agent_run_empty_response"
            )
        return content

    async def _start_turn(
        self, command: RunAgentTurnCommand, text: str
    ) -> tuple[Conversation, AgentRun]:
        async with self.uow_factory() as uow:
            actor = await ResolveCurrentActor(users=uow.users).execute(command.telegram_user_id)

            # Load exactly the window this turn may use: anything more is paid
            # for and discarded, anything less makes recent_messages below
            # refuse to answer.
            conversation = await uow.conversations.get(
                command.conversation_id, message_limit=command.context_window
            )
            if conversation is None:
                raise NotFoundError("Conversation not found.", code="conversation_not_found")
            if conversation.owner_id != actor.id:
                raise AuthorizationError(
                    "You are not allowed to use this conversation.",
                    code="conversation_not_owned",
                )

            user_message = conversation.append_message(role=MessageRole.USER, content=text)
            await uow.conversations.append_message(user_message)

            run = AgentRun(
                id=new_agent_run_id(),
                user_id=actor.id,
                conversation_id=conversation.id,
                provider=command.provider,
                model=command.model,
                limits=command.limits,
            )
            run.start()
            run.record_step(AgentStepType.PLANNING)
            run.record_step(AgentStepType.MODEL_CALL)
            await uow.agent_runs.add(run)

        return conversation, run

    def _build_request(
        self, command: RunAgentTurnCommand, conversation: Conversation
    ) -> LLMRequest:
        """Assemble a request that fits inside the run's token budget.

        The system prompt is reserved out of the budget first because it is not
        optional; whatever is left decides how much history travels. The newest
        user message is guaranteed to survive because ``_validate_input``
        already refused any input that could not fit on its own.
        """
        budget = command.limits.max_context_tokens - estimate_tokens(self.system_prompt)
        history = select_within_token_budget(
            conversation.recent_messages(command.context_window), budget=budget
        )
        if not history:
            raise AgentExecutionError(
                "The system prompt leaves no room for conversation context.",
                code="agent_context_budget_exhausted",
                max_context_tokens=command.limits.max_context_tokens,
            )

        messages = tuple(
            LLMMessage(role=_DOMAIN_TO_LLM_ROLE[message.role], content=message.content)
            for message in history
        )
        system_message = LLMMessage(role=LLMMessageRole.SYSTEM, content=self.system_prompt)
        return LLMRequest(
            provider=command.provider,
            model=command.model,
            messages=(system_message, *messages),
        )

    async def _fail_turn(self, run: AgentRun, claim: _Claim | None, *, reason: str) -> None:
        """Settle a failed turn without ever masking the error that caused it.

        The run and the idempotency claim are settled in separate transactions
        on purpose. They are independent facts, and sharing one transaction
        means a refused claim settlement — which is the *expected* outcome for
        a worker whose claim was taken over — would roll back the run
        settlement too and strand the run in RUNNING. Both are logged and
        neither raises, so the caller always sees the original failure.
        """
        await self._settle_failed_run(run, reason)
        if claim is not None:
            await self._settle_failed_claim(claim, reason)

    async def _settle_failed_run(self, run: AgentRun, reason: str) -> None:
        """Record the failure against the *persisted* run.

        A ``finish()`` that never reached the database did not really happen,
        so the persisted state wins over the in-memory entity.
        """
        try:
            async with self.uow_factory() as uow:
                persisted = await uow.agent_runs.get(run.id)
                target = run if persisted is None else persisted
                if target.status in _TERMINAL_RUN_STATUSES:
                    return
                target.fail(reason)
                await uow.agent_runs.save(target)
        except Exception:
            logger.exception("agent_run.settlement_failed agent_run_id=%s", run.id)

    async def _settle_failed_claim(self, claim: _Claim, reason: str) -> None:
        try:
            async with self.uow_factory() as uow:
                await uow.idempotency.fail(
                    IDEMPOTENCY_NAMESPACE, claim.key, reason, claim_token=claim.token
                )
        except ConflictError:
            # The claim expired and another attempt owns it now. That attempt's
            # outcome is authoritative; overwriting it is exactly what the
            # claim token exists to prevent.
            logger.warning("agent_run.claim_lost_on_failure agent_run_key=%s", claim.key)
        except Exception:
            logger.exception("agent_run.claim_settlement_failed agent_run_key=%s", claim.key)

    async def _finish_turn(
        self,
        run: AgentRun,
        conversation: Conversation,
        llm_response: LLMResponse,
        content: str,
        claim: _Claim | None,
    ) -> AgentTurnResult:
        metadata = dict(llm_response.provider_metadata) if llm_response.provider_metadata else None
        assistant_message = conversation.append_message(
            role=MessageRole.ASSISTANT, content=content, metadata=metadata
        )

        usage_metadata: dict[str, object] = {
            "input_tokens": llm_response.usage.input_tokens,
            "output_tokens": llm_response.usage.output_tokens,
            "total_tokens": llm_response.usage.total_tokens,
        }
        run.record_step(AgentStepType.VALIDATION)
        run.finish(content, usage_metadata=usage_metadata)

        result = AgentTurnResult(
            agent_run_id=run.id,
            conversation_id=conversation.id,
            assistant_message_id=assistant_message.id,
            response=content,
            status=run.status,
            usage_metadata=usage_metadata,
        )

        # One transaction: the assistant message, the finished run, and the
        # settled claim are the same fact. A claim that was taken over rejects
        # the settlement and rolls the whole thing back, which is the point —
        # a stale attempt must not append a second assistant message.
        async with self.uow_factory() as uow:
            await uow.conversations.append_message(assistant_message)
            await uow.agent_runs.save(run)
            if claim is not None:
                await uow.idempotency.complete(
                    IDEMPOTENCY_NAMESPACE,
                    claim.key,
                    _result_to_stored(result),
                    claim_token=claim.token,
                )

        return result


def _normalize_failure(exc: Exception) -> NexaBotError:
    """Map any failure onto the domain taxonomy, safely.

    ``str(exc)`` on a provider SDK exception routinely carries the request URL,
    headers, or an echoed API key. That string was previously used as the run's
    ``failure_reason`` and persisted verbatim. Only errors that already carry a
    curated ``safe_message`` are passed through; anything else is replaced with
    a generic message, keeping just the exception's *type* name — which names
    the failure without quoting any of its contents.

    Unknown provider failures are classified non-retryable: retrying a failure
    nobody has characterised risks paying for the same failure repeatedly, so
    the default is to stop and let an operator look.
    """
    if isinstance(exc, NexaBotError):
        return exc
    logger.exception("agent_run.unclassified_failure error_type=%s", type(exc).__name__)
    return ExternalServiceError(
        "The model provider failed.",
        code="llm_provider_error",
        retryable=False,
        error_type=type(exc).__name__,
    )


def _result_to_stored(result: AgentTurnResult) -> dict[str, object]:
    return {
        "agent_run_id": str(result.agent_run_id),
        "conversation_id": str(result.conversation_id),
        "assistant_message_id": str(result.assistant_message_id),
        "response": result.response,
        "status": result.status.value,
        "usage_metadata": dict(result.usage_metadata),
    }


def _result_from_stored(data: Mapping[str, object]) -> AgentTurnResult:
    usage_metadata = data.get("usage_metadata")
    return AgentTurnResult(
        agent_run_id=AgentRunId(UUID(str(data["agent_run_id"]))),
        conversation_id=ConversationId(UUID(str(data["conversation_id"]))),
        assistant_message_id=MessageId(UUID(str(data["assistant_message_id"]))),
        response=str(data["response"]),
        status=AgentRunStatus(str(data["status"])),
        usage_metadata=dict(usage_metadata) if isinstance(usage_metadata, Mapping) else {},
    )

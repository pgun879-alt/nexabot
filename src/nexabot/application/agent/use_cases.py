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

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from uuid import UUID

from nexabot.application.identity.use_cases import ResolveCurrentActor
from nexabot.domain.agent.entities import AgentLimits, AgentRun, AgentRunStatus, AgentStepType
from nexabot.domain.common.errors import (
    AgentExecutionError,
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from nexabot.domain.common.ids import (
    AgentRunId,
    ConversationId,
    MessageId,
    new_agent_run_id,
)
from nexabot.domain.conversations.entities import Conversation, MessageRole
from nexabot.ports.llm.gateway import (
    LLMGateway,
    LLMMessage,
    LLMMessageRole,
    LLMRequest,
    LLMResponse,
)
from nexabot.ports.repositories.idempotency import IdempotencyState
from nexabot.ports.unit_of_work import UnitOfWorkFactory

logger = logging.getLogger(__name__)

IDEMPOTENCY_NAMESPACE = "agent_run"

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
class RunAgentTurnCommand:
    telegram_user_id: int
    conversation_id: ConversationId
    text: str
    provider: str
    model: str
    limits: AgentLimits = field(default_factory=AgentLimits)
    idempotency_key: str | None = None
    context_window: int = 20


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
        text = command.text.strip()
        if not text:
            raise ValidationError("Agent turn input is required.", code="agent_turn_input_required")

        if command.idempotency_key is not None:
            cached = await self._check_idempotency(command.idempotency_key)
            if cached is not None:
                return cached

        conversation, run = await self._start_turn(command, text)
        llm_request = self._build_request(command, conversation)

        try:
            llm_response = await self.llm_gateway.complete(llm_request)
        except Exception as exc:
            await self._fail_turn(run, command.idempotency_key, reason=str(exc) or repr(exc))
            raise

        if llm_response.tool_calls:
            reason = "The model requested a tool call, but no ToolExecutor port is wired yet."
            await self._fail_turn(run, command.idempotency_key, reason=reason)
            raise AgentExecutionError(reason, code="agent_tool_execution_unsupported")

        content = (llm_response.content or "").strip()
        if not content:
            reason = "The model returned an empty response."
            await self._fail_turn(run, command.idempotency_key, reason=reason)
            raise AgentExecutionError(reason, code="agent_run_empty_response")

        try:
            return await self._finish_turn(run, conversation, llm_response, content, command)
        except Exception as exc:
            # No exit from a started run may leave it stranded in RUNNING.
            await self._fail_turn(run, command.idempotency_key, reason=str(exc) or repr(exc))
            raise

    async def _check_idempotency(self, key: str) -> AgentTurnResult | None:
        async with self.uow_factory() as uow:
            record = await uow.idempotency.start(IDEMPOTENCY_NAMESPACE, key)

        if record.state == IdempotencyState.COMPLETED:
            return _result_from_stored(record.result)
        if record.state == IdempotencyState.FAILED:
            raise ConflictError(
                "A previous attempt for this request failed and was not retried.",
                code="agent_run_idempotency_failed",
            )
        if not record.owned:
            # Another attempt holds the claim. Proceeding would duplicate the
            # model call and the assistant message this key exists to prevent.
            raise ConflictError(
                "This request is already being processed.",
                code="agent_run_in_progress",
            )
        return None

    async def _start_turn(
        self, command: RunAgentTurnCommand, text: str
    ) -> tuple[Conversation, AgentRun]:
        async with self.uow_factory() as uow:
            actor = await ResolveCurrentActor(users=uow.users).execute(command.telegram_user_id)

            conversation = await uow.conversations.get(command.conversation_id)
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
        history = conversation.recent_messages(command.context_window)
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

    async def _fail_turn(self, run: AgentRun, idempotency_key: str | None, *, reason: str) -> None:
        """Settle a failed turn without ever masking the error that caused it.

        Settlement works from the *persisted* run rather than the in-memory
        one: a ``finish()`` that never reached the database did not really
        happen, and must not stop the run from being recorded as failed.
        Settlement problems are logged, never raised, so the caller always sees
        the original failure.
        """
        try:
            async with self.uow_factory() as uow:
                persisted = await uow.agent_runs.get(run.id)
                target = run if persisted is None else persisted
                if target.status not in _TERMINAL_RUN_STATUSES:
                    target.fail(reason)
                    await uow.agent_runs.save(target)
                if idempotency_key is not None:
                    await uow.idempotency.fail(IDEMPOTENCY_NAMESPACE, idempotency_key, reason)
        except Exception:
            logger.exception("agent_run.settlement_failed agent_run_id=%s", run.id)

    async def _finish_turn(
        self,
        run: AgentRun,
        conversation: Conversation,
        llm_response: LLMResponse,
        content: str,
        command: RunAgentTurnCommand,
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

        async with self.uow_factory() as uow:
            await uow.conversations.append_message(assistant_message)
            await uow.agent_runs.save(run)
            if command.idempotency_key is not None:
                await uow.idempotency.complete(
                    IDEMPOTENCY_NAMESPACE, command.idempotency_key, _result_to_stored(result)
                )

        return result


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

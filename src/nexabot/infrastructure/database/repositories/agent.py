"""SQLAlchemy implementation of the agent run repository port."""

from __future__ import annotations

from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from nexabot.domain.agent.entities import (
    AgentLimits,
    AgentRun,
    AgentRunStatus,
    AgentStep,
    AgentStepType,
)
from nexabot.domain.common.errors import NotFoundError
from nexabot.domain.common.ids import AgentRunId, AgentStepId, ConversationId, UserId
from nexabot.infrastructure.database.base import ensure_utc, ensure_utc_optional
from nexabot.infrastructure.database.models import AgentRunModel, AgentStepModel


class SqlAlchemyAgentRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, run_id: AgentRunId) -> AgentRun | None:
        row = await self._session.get(AgentRunModel, str(run_id))
        if row is None:
            return None
        return await self._to_domain(row)

    async def add(self, run: AgentRun) -> None:
        self._session.add(
            AgentRunModel(
                id=str(run.id),
                user_id=str(run.user_id),
                conversation_id=str(run.conversation_id),
                status=run.status.value,
                provider=run.provider,
                model=run.model,
                started_at=run.started_at,
                finished_at=run.finished_at,
                failure_reason=run.failure_reason,
                final_response=run.final_response,
                usage_metadata=dict(run.usage_metadata),
                max_steps=run.limits.max_steps,
                max_tool_calls=run.limits.max_tool_calls,
                max_retries=run.limits.max_retries,
                max_execution_seconds=run.limits.max_execution_seconds,
                max_context_tokens=run.limits.max_context_tokens,
            )
        )
        await self._session.flush()
        for step in run.steps:
            await self.append_step(step)

    async def save(self, run: AgentRun) -> None:
        row = await self._session.get(AgentRunModel, str(run.id))
        if row is None:
            raise NotFoundError("Agent run not found.", code="agent_run_not_found")
        row.status = run.status.value
        row.started_at = run.started_at
        row.finished_at = run.finished_at
        row.failure_reason = run.failure_reason
        row.final_response = run.final_response
        row.usage_metadata = dict(run.usage_metadata)

        # Append by index rather than by count. Steps are numbered from 1 and
        # never renumbered, so "index greater than the highest stored" is the
        # set that is genuinely new; counting instead assumes the stored rows
        # are a gapless prefix, and silently writes duplicates if they are not.
        highest_persisted = (
            await self._session.execute(
                sa.select(sa.func.coalesce(sa.func.max(AgentStepModel.index), 0)).where(
                    AgentStepModel.run_id == str(run.id)
                )
            )
        ).scalar_one()
        for step in run.steps:
            if step.index > highest_persisted:
                await self.append_step(step)

    async def append_step(self, step: AgentStep) -> None:
        self._session.add(
            AgentStepModel(
                id=str(step.id),
                run_id=str(step.run_id),
                index=step.index,
                type=step.type.value,
                meta=dict(step.metadata),
                # The step records when the run took it, not when the row was
                # written; those differ whenever settlement is deferred.
                created_at=step.created_at,
                updated_at=step.created_at,
            )
        )
        await self._session.flush()

    async def _to_domain(self, row: AgentRunModel) -> AgentRun:
        step_rows = (
            await self._session.execute(
                sa.select(AgentStepModel)
                .where(AgentStepModel.run_id == row.id)
                .order_by(AgentStepModel.index.asc())
            )
        ).scalars()
        steps = tuple(
            AgentStep(
                id=AgentStepId(UUID(step_row.id)),
                run_id=AgentRunId(UUID(step_row.run_id)),
                index=step_row.index,
                type=AgentStepType(step_row.type),
                metadata=step_row.meta,
                created_at=ensure_utc(step_row.created_at),
            )
            for step_row in step_rows
        )
        return AgentRun(
            id=AgentRunId(UUID(row.id)),
            user_id=UserId(UUID(row.user_id)),
            conversation_id=ConversationId(UUID(row.conversation_id)),
            provider=row.provider,
            model=row.model,
            limits=AgentLimits(
                max_steps=row.max_steps,
                max_tool_calls=row.max_tool_calls,
                max_retries=row.max_retries,
                max_execution_seconds=row.max_execution_seconds,
                max_context_tokens=row.max_context_tokens,
            ),
            status=AgentRunStatus(row.status),
            steps=steps,
            started_at=ensure_utc_optional(row.started_at),
            finished_at=ensure_utc_optional(row.finished_at),
            failure_reason=row.failure_reason,
            final_response=row.final_response,
            usage_metadata=row.usage_metadata,
        )

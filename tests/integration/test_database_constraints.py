"""Domain invariants must hold at the database, not only in Python.

An application-only invariant holds exactly as long as every writer goes
through the application. These tests assert the database itself refuses rows
the domain entities consider impossible to construct, so a bad backfill, a
manual fix, or a future writer cannot create them either.

SQLite enforces CHECK constraints, so the same assertions hold here as on
PostgreSQL. Not verified against PostgreSQL in this environment — no server is
available (see README).
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from nexabot.domain.common.ids import new_conversation_id, new_user_id
from nexabot.domain.conversations.entities import Conversation
from nexabot.domain.identity.entities import User
from nexabot.infrastructure.database.repositories.conversations import (
    SqlAlchemyConversationRepository,
)
from nexabot.infrastructure.database.repositories.identity import SqlAlchemyUserRepository
from tests.support import EngineRegistry, run_scenario


def _insert(table: str, **values: object) -> sa.TextClause:
    columns = ", ".join(f'"{name}"' for name in values)
    placeholders = ", ".join(f":{name}" for name in values)
    return sa.text(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})").bindparams(**values)


def _rejects(table: str, **values: object) -> None:
    async def scenario(engines: EngineRegistry) -> None:
        sessionmaker = await engines.sessionmaker()
        async with sessionmaker() as session:
            with pytest.raises(IntegrityError):
                await session.execute(_insert(table, **values))
                await session.flush()

    run_scenario(scenario)


def _timestamps() -> dict[str, object]:
    return {"created_at": "2026-09-24 00:00:00", "updated_at": "2026-09-24 00:00:00"}


def test_users_reject_an_unknown_status() -> None:
    """A status outside UserStatus makes UserStatus(row.status) raise ValueError."""
    _rejects("users", id=str(new_user_id()), status="probationary", **_timestamps())


def test_conversations_reject_an_unknown_status() -> None:
    _rejects(
        "conversations",
        id=str(new_conversation_id()),
        owner_id=str(new_user_id()),
        status="paused",
        meta="{}",
        **_timestamps(),
    )


def test_messages_reject_blank_content() -> None:
    """Message.__post_init__ refuses it, so the row must not exist either."""

    async def scenario(engines: EngineRegistry) -> None:
        sessionmaker = await engines.sessionmaker()
        owner = User(id=new_user_id())
        conversation = Conversation(id=new_conversation_id(), owner_id=owner.id)

        async with sessionmaker() as session:
            await SqlAlchemyUserRepository(session).add(owner)
            await SqlAlchemyConversationRepository(session).add(conversation)
            await session.commit()

        async with sessionmaker() as session:
            with pytest.raises(IntegrityError):
                await session.execute(
                    _insert(
                        "messages",
                        id=str(new_conversation_id()),
                        conversation_id=str(conversation.id),
                        role="user",
                        content="",
                        meta="{}",
                        **_timestamps(),
                    )
                )
                await session.flush()

    run_scenario(scenario)


def test_messages_reject_an_unknown_role() -> None:
    _rejects(
        "messages",
        id=str(new_conversation_id()),
        conversation_id=str(new_conversation_id()),
        role="moderator",
        content="hello",
        meta="{}",
        **_timestamps(),
    )


def _agent_run_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": str(new_conversation_id()),
        "user_id": str(new_user_id()),
        "conversation_id": str(new_conversation_id()),
        "status": "pending",
        "provider": "stub",
        "model": "test-model",
        "usage_metadata": "{}",
        "max_steps": 12,
        "max_tool_calls": 6,
        "max_retries": 2,
        "max_execution_seconds": 60,
        "max_context_tokens": 12_000,
        **_timestamps(),
    }
    row.update(overrides)
    return row


@pytest.mark.parametrize(
    "limit",
    [
        "max_steps",
        "max_tool_calls",
        "max_retries",
        "max_execution_seconds",
        "max_context_tokens",
    ],
)
def test_agent_run_limits_must_be_positive(limit: str) -> None:
    """Mirrors AgentLimits.__post_init__, which rejects non-positive budgets."""
    _rejects("agent_runs", **_agent_run_row(**{limit: 0}))


def test_agent_runs_reject_an_unknown_status() -> None:
    _rejects("agent_runs", **_agent_run_row(status="thinking"))


def test_a_succeeded_run_must_carry_a_final_response() -> None:
    _rejects(
        "agent_runs",
        **_agent_run_row(status="succeeded", finished_at="2026-09-24 00:00:01"),
    )


def test_a_failed_run_must_carry_a_failure_reason() -> None:
    _rejects(
        "agent_runs",
        **_agent_run_row(status="failed", finished_at="2026-09-24 00:00:01"),
    )


def test_a_terminal_run_must_record_when_it_finished() -> None:
    _rejects(
        "agent_runs",
        **_agent_run_row(status="succeeded", final_response="an answer"),
    )


def test_agent_step_indexes_start_at_one() -> None:
    _rejects(
        "agent_steps",
        id=str(new_conversation_id()),
        run_id=str(new_conversation_id()),
        index=0,
        type="planning",
        meta="{}",
        **_timestamps(),
    )


def test_idempotency_records_reject_an_unknown_state() -> None:
    _rejects(
        "idempotency_records",
        id=str(new_conversation_id()),
        namespace="agent_run",
        key="k",
        state="halfway",
        result="{}",
        claimed_at="2026-09-24 00:00:00",
        claim_token="token",
        **_timestamps(),
    )

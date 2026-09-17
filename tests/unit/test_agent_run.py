from __future__ import annotations

import pytest

from nexabot.domain.agent.entities import AgentLimits, AgentRun, AgentRunStatus, AgentStepType
from nexabot.domain.common.errors import AgentExecutionError, ConflictError
from nexabot.domain.common.ids import new_agent_run_id, new_conversation_id, new_user_id


def make_run(*, limits: AgentLimits | None = None) -> AgentRun:
    return AgentRun(
        id=new_agent_run_id(),
        user_id=new_user_id(),
        conversation_id=new_conversation_id(),
        provider="stub",
        model="test-model",
        limits=limits or AgentLimits(),
    )


def test_agent_run_requires_start_before_steps() -> None:
    run = make_run()

    with pytest.raises(ConflictError):
        run.record_step(AgentStepType.PLANNING)


def test_agent_run_records_ordered_steps_and_finishes() -> None:
    run = make_run(
        limits=AgentLimits(
            max_steps=4,
            max_tool_calls=1,
            max_retries=1,
            max_execution_seconds=10,
            max_context_tokens=1000,
        )
    )

    run.start()
    run.record_step(AgentStepType.PLANNING)
    run.record_step(AgentStepType.MODEL_CALL)
    run.record_step(AgentStepType.VALIDATION)
    run.finish("Final answer.", usage_metadata={"total_tokens": 42})

    assert run.status == AgentRunStatus.SUCCEEDED
    assert [step.index for step in run.steps] == [1, 2, 3, 4]
    assert run.final_response == "Final answer."
    assert run.usage_metadata == {"total_tokens": 42}


def test_agent_run_enforces_step_limit() -> None:
    run = make_run(
        limits=AgentLimits(
            max_steps=1,
            max_tool_calls=1,
            max_retries=1,
            max_execution_seconds=10,
            max_context_tokens=1000,
        )
    )
    run.start()
    run.record_step(AgentStepType.PLANNING)

    with pytest.raises(AgentExecutionError) as raised:
        run.record_step(AgentStepType.MODEL_CALL)

    assert raised.value.code == "agent_step_limit_exceeded"


def test_agent_run_enforces_tool_call_limit() -> None:
    run = make_run(
        limits=AgentLimits(
            max_steps=3,
            max_tool_calls=1,
            max_retries=1,
            max_execution_seconds=10,
            max_context_tokens=1000,
        )
    )
    run.start()
    run.record_step(AgentStepType.TOOL_CALL)

    with pytest.raises(AgentExecutionError) as raised:
        run.record_step(AgentStepType.TOOL_CALL)

    assert raised.value.code == "agent_tool_limit_exceeded"


def test_exhausted_step_budget_still_allows_the_run_to_finish() -> None:
    """Regression: the budget used to reject FINAL_RESPONSE.

    That stranded the run in RUNNING and discarded a model answer that had
    already been paid for.
    """
    run = make_run(limits=AgentLimits(max_steps=2))
    run.start()
    run.record_step(AgentStepType.PLANNING)
    run.record_step(AgentStepType.MODEL_CALL)

    run.finish("Final answer.")

    assert run.status == AgentRunStatus.SUCCEEDED
    assert run.final_response == "Final answer."
    assert [step.type for step in run.steps][-1] == AgentStepType.FINAL_RESPONSE


def test_exhausted_step_budget_still_allows_the_run_to_fail() -> None:
    run = make_run(limits=AgentLimits(max_steps=2))
    run.start()
    run.record_step(AgentStepType.PLANNING)
    run.record_step(AgentStepType.MODEL_CALL)

    run.fail("provider timeout")

    assert run.status == AgentRunStatus.FAILED
    assert [step.type for step in run.steps][-1] == AgentStepType.ERROR


def test_budget_still_rejects_non_terminal_work_after_the_exemption() -> None:
    run = make_run(limits=AgentLimits(max_steps=2))
    run.start()
    run.record_step(AgentStepType.PLANNING)
    run.record_step(AgentStepType.MODEL_CALL)

    for step_type in (AgentStepType.TOOL_CALL, AgentStepType.VALIDATION):
        with pytest.raises(AgentExecutionError) as raised:
            run.record_step(step_type)
        assert raised.value.code == "agent_step_limit_exceeded"


def test_terminal_agent_run_cannot_transition_again() -> None:
    run = make_run()
    run.start()
    run.fail("provider timeout")

    assert run.status == AgentRunStatus.FAILED
    with pytest.raises(ConflictError):
        run.fail("second failure")

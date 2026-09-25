"""Token-budgeted context selection.

Regression cover for max_context_tokens, which the agent run stored and
reported but never enforced: context was bounded by message *count*, a
different quantity entirely.
"""

from __future__ import annotations

from nexabot.domain.agent.context import (
    MESSAGE_TOKEN_OVERHEAD,
    estimate_tokens,
    select_within_token_budget,
)
from nexabot.domain.common.ids import new_conversation_id, new_message_id
from nexabot.domain.conversations.entities import Message, MessageRole


def _message(content: str) -> Message:
    return Message(
        id=new_message_id(),
        conversation_id=new_conversation_id(),
        role=MessageRole.USER,
        content=content,
    )


def test_estimate_is_conservative_and_counts_structural_overhead() -> None:
    assert estimate_tokens("") == MESSAGE_TOKEN_OVERHEAD

    # Over-estimating is the safe direction: a low estimate would let a request
    # through that the provider then rejects.
    text = "a normal sentence of english prose"
    assert estimate_tokens(text) > len(text) / 4


def test_selection_keeps_the_newest_messages_that_fit() -> None:
    messages = [_message(f"message-{index}") for index in range(10)]
    budget = estimate_tokens(messages[0].content) * 3

    selected = select_within_token_budget(messages, budget=budget)

    assert [m.content for m in selected] == ["message-7", "message-8", "message-9"]


def test_selection_never_exceeds_the_budget() -> None:
    messages = [_message("x" * size) for size in (10, 4000, 20, 30)]
    budget = 200

    selected = select_within_token_budget(messages, budget=budget)

    assert sum(estimate_tokens(m.content) for m in selected) <= budget


def test_selection_stays_contiguous_rather_than_cherry_picking() -> None:
    """Skipping a big message to fit older ones would rewrite the conversation."""
    messages = [_message("old"), _message("x" * 9_000), _message("new")]

    selected = select_within_token_budget(messages, budget=100)

    assert [m.content for m in selected] == ["new"]


def test_an_empty_or_negative_budget_selects_nothing() -> None:
    messages = [_message("anything")]

    assert select_within_token_budget(messages, budget=0) == ()
    assert select_within_token_budget(messages, budget=-5) == ()


def test_everything_fits_when_the_budget_is_generous() -> None:
    messages = [_message(f"message-{index}") for index in range(5)]

    assert select_within_token_budget(messages, budget=100_000) == tuple(messages)

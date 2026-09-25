"""Token-budgeted context selection.

``AgentLimits.max_context_tokens`` is a *token* budget. Bounding context by
message count instead is not an approximation of it: fifty one-word messages
and fifty thousand-word messages are the same count and wildly different costs,
so a message-count bound either wastes most of the budget or blows through it.
This module turns the budget back into what it says it is.

ASSUMPTION: token counts are estimated, not measured. A real count is
tokenizer-specific and therefore provider-specific, and no provider adapter
exists yet. ``estimate_tokens`` is deliberately conservative (it over-estimates)
so the estimate failing means sending less context than allowed rather than
being rejected by the provider. When a real provider adapter lands, it should
supply its own tokenizer through the gateway port and this heuristic becomes
the fallback for providers that expose none.
"""

from __future__ import annotations

from collections.abc import Sequence

from nexabot.domain.conversations.entities import Message

CHARS_PER_TOKEN = 3
"""Characters per token assumed by the estimate.

Real tokenizers average roughly four characters per token on English prose and
fewer on code, punctuation, and non-Latin scripts. Three is used so the
estimate errs high across all of them.
"""

MESSAGE_TOKEN_OVERHEAD = 4
"""Per-message cost of role markers and separators in a chat encoding.

Providers wrap each message in structural tokens that no per-character estimate
of the content would account for.
"""


def estimate_tokens(text: str) -> int:
    """Conservatively estimate how many tokens ``text`` will occupy."""
    return -(-len(text) // CHARS_PER_TOKEN) + MESSAGE_TOKEN_OVERHEAD


def select_within_token_budget(
    messages: Sequence[Message], *, budget: int
) -> tuple[Message, ...]:
    """Keep the newest messages whose estimated cost fits ``budget``.

    Selection runs newest-first because recency is what the model needs most,
    and stops at the first message that does not fit rather than skipping it to
    squeeze in an older one: preserving contiguity keeps the conversation
    readable as a conversation instead of an arbitrary excerpt of it.
    """
    if budget <= 0:
        return ()

    selected: list[Message] = []
    remaining = budget
    for message in reversed(messages):
        cost = estimate_tokens(message.content)
        if cost > remaining:
            break
        remaining -= cost
        selected.append(message)
    return tuple(reversed(selected))

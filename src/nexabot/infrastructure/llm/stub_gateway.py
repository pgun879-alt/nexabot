"""Deterministic, no-network LLMGateway adapter.

This stub exists so the agent orchestration path (RunAgentTurn) can be built
and tested end-to-end before a real provider adapter is wired in. It performs
no network I/O and never leaks provider-specific concepts: it only speaks the
``LLMGateway`` port's own types.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from nexabot.ports.llm.gateway import LLMMessageRole, LLMRequest, LLMResponse, LLMUsage

ResponseFactory = Callable[[LLMRequest], LLMResponse]


@dataclass(slots=True)
class StubLLMGateway:
    """Configurable deterministic stand-in for a real LLM provider adapter.

    Resolution order per call to ``complete``:

    1. If ``scripted_responses`` is non-empty, pop and return/raise its next
       entry (lets a test script an exact multi-call sequence, including
       simulated provider failures).
    2. Else if ``fail_with`` is set, raise it (simulates a model failure for
       every call).
    3. Else if ``response_factory`` is set, call it with the request.
    4. Else if ``default_response`` is set, return it.
    5. Else deterministically echo the last user message.

    Every request passed in is recorded in ``calls`` so tests can assert on
    exactly what the orchestration layer sent.
    """

    default_response: LLMResponse | None = None
    response_factory: ResponseFactory | None = None
    scripted_responses: list[LLMResponse | Exception] = field(default_factory=list)
    fail_with: Exception | None = None
    calls: list[LLMRequest] = field(default_factory=list, init=False)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls.append(request)

        if self.scripted_responses:
            next_item = self.scripted_responses.pop(0)
            if isinstance(next_item, Exception):
                raise next_item
            return next_item

        if self.fail_with is not None:
            raise self.fail_with

        if self.response_factory is not None:
            return self.response_factory(request)

        if self.default_response is not None:
            return self.default_response

        return _echo_response(request)


def _echo_response(request: LLMRequest) -> LLMResponse:
    last_user_message = next(
        (
            message.content
            for message in reversed(request.messages)
            if message.role == LLMMessageRole.USER
        ),
        "",
    )
    content = f"Echo: {last_user_message}" if last_user_message else "Acknowledged."
    input_tokens = len(last_user_message.split())
    output_tokens = len(content.split())
    return LLMResponse(
        content=content,
        usage=LLMUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
        ),
        provider_metadata={"stub": True},
    )

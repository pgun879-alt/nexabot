from __future__ import annotations

import asyncio

import pytest

from nexabot.domain.common.errors import ExternalServiceError
from nexabot.infrastructure.llm.stub_gateway import StubLLMGateway
from nexabot.ports.llm.gateway import LLMMessage, LLMMessageRole, LLMRequest, LLMResponse, LLMUsage


def _request(text: str = "hello") -> LLMRequest:
    return LLMRequest(
        provider="stub",
        model="test-model",
        messages=(LLMMessage(role=LLMMessageRole.USER, content=text),),
    )


def test_stub_gateway_echoes_deterministically_by_default() -> None:
    async def scenario() -> None:
        gateway = StubLLMGateway()
        first = await gateway.complete(_request("hi there"))
        second = await gateway.complete(_request("hi there"))

        assert first == second
        assert first.content == "Echo: hi there"
        assert len(gateway.calls) == 2

    asyncio.run(scenario())


def test_stub_gateway_returns_configured_default_response() -> None:
    async def scenario() -> None:
        fixed = LLMResponse(content="fixed answer", usage=LLMUsage(total_tokens=3))
        gateway = StubLLMGateway(default_response=fixed)

        response = await gateway.complete(_request())

        assert response == fixed

    asyncio.run(scenario())


def test_stub_gateway_uses_response_factory() -> None:
    async def scenario() -> None:
        gateway = StubLLMGateway(
            response_factory=lambda request: LLMResponse(
                content=f"model={request.model}", usage=LLMUsage()
            )
        )

        response = await gateway.complete(_request())

        assert response.content == "model=test-model"

    asyncio.run(scenario())


def test_stub_gateway_simulates_configured_failure() -> None:
    async def scenario() -> None:
        gateway = StubLLMGateway(fail_with=ExternalServiceError("provider timeout"))

        with pytest.raises(ExternalServiceError):
            await gateway.complete(_request())

    asyncio.run(scenario())


def test_stub_gateway_scripted_responses_play_back_in_order() -> None:
    async def scenario() -> None:
        gateway = StubLLMGateway(
            scripted_responses=[
                ExternalServiceError("first call fails"),
                LLMResponse(content="second call succeeds", usage=LLMUsage()),
            ]
        )

        with pytest.raises(ExternalServiceError):
            await gateway.complete(_request())

        response = await gateway.complete(_request())
        assert response.content == "second call succeeds"

    asyncio.run(scenario())

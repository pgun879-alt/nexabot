"""LLM gateway port.

The agent/application layer talks to this protocol, never directly to a vendor
SDK.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol


class LLMMessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass(frozen=True, slots=True)
class LLMMessage:
    role: LLMMessageRole
    content: str
    name: str | None = None


@dataclass(frozen=True, slots=True)
class LLMToolSpec:
    name: str
    description: str
    input_schema: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class LLMRequest:
    provider: str
    model: str
    messages: tuple[LLMMessage, ...]
    tools: tuple[LLMToolSpec, ...] = ()
    temperature: float = 0.2
    max_output_tokens: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LLMToolCall:
    id: str
    name: str
    arguments: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class LLMUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True, slots=True)
class LLMResponse:
    content: str | None
    tool_calls: tuple[LLMToolCall, ...] = ()
    usage: LLMUsage = field(default_factory=LLMUsage)
    provider_metadata: Mapping[str, Any] = field(default_factory=dict)


class LLMGateway(Protocol):
    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Produce a model response for a normalized LLM request."""

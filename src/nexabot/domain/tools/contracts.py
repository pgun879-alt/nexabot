"""Tool contracts used by the agent and application services."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel

from nexabot.domain.identity.entities import User


class ToolSensitivity(StrEnum):
    SAFE = "safe"
    SENSITIVE = "sensitive"
    ADMIN = "admin"


@dataclass(frozen=True, slots=True)
class ToolIdentity:
    name: str
    version: str = "1.0"

    @property
    def key(self) -> str:
        return f"{self.name}@{self.version}"


@dataclass(frozen=True, slots=True)
class ToolTimeoutPolicy:
    seconds: float = 10.0


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    identity: ToolIdentity
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    sensitivity: ToolSensitivity = ToolSensitivity.SAFE
    timeout: ToolTimeoutPolicy = field(default_factory=ToolTimeoutPolicy)


@dataclass(frozen=True, slots=True)
class ToolResult:
    output: BaseModel
    metadata: Mapping[str, Any] = field(default_factory=dict)


class Tool(Protocol):
    definition: ToolDefinition

    async def execute(self, user: User, input_data: BaseModel) -> ToolResult:
        """Execute the tool after centralized authorization has already passed."""

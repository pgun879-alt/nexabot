"""Idempotency repository port."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

DEFAULT_CLAIM_TTL_SECONDS = 300
"""How long a ``STARTED`` claim is trusted before another caller may take over.

Without a ceiling, a process that dies mid-operation would poison its key
forever. It must comfortably exceed the longest legitimate operation.
"""


class IdempotencyState(StrEnum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    namespace: str
    key: str
    state: IdempotencyState
    result: Mapping[str, Any] = field(default_factory=dict)
    owned: bool = False
    """Whether *this* caller holds the claim and may perform the work.

    True only when the caller created the record or reclaimed an expired one.
    A caller that sees ``STARTED`` without owning it must not proceed: another
    attempt is in flight, and duplicating the work is exactly what the
    idempotency key exists to prevent.
    """
    claim_token: str | None = None
    """Proof of the claim this caller holds; ``None`` unless ``owned``.

    Ownership is not permanent: a claim that outlives its TTL can be taken over
    by another attempt. The token identifies *which* claim the caller holds, so
    settlement can be refused when the caller is no longer the current owner.
    """


class IdempotencyRepository(Protocol):
    async def start(
        self,
        namespace: str,
        key: str,
        *,
        claim_ttl_seconds: int = DEFAULT_CLAIM_TTL_SECONDS,
    ) -> IdempotencyRecord:
        """Claim an operation, or report the existing record for this key."""

    async def complete(
        self, namespace: str, key: str, result: Mapping[str, Any], *, claim_token: str
    ) -> None:
        """Mark an operation completed, if ``claim_token`` still holds the claim.

        Raises ``ConflictError`` when it does not: a worker whose claim expired
        and was taken over must not overwrite the newer owner's outcome.
        """

    async def fail(self, namespace: str, key: str, reason: str, *, claim_token: str) -> None:
        """Mark an operation failed, if ``claim_token`` still holds the claim."""

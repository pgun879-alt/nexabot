"""SQLAlchemy implementation of the idempotency repository port."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any, cast
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nexabot.domain.common.errors import ConflictError, NotFoundError
from nexabot.domain.common.time import utc_now
from nexabot.infrastructure.database.base import ensure_utc
from nexabot.infrastructure.database.models import IdempotencyRecordModel
from nexabot.ports.repositories.idempotency import (
    DEFAULT_CLAIM_TTL_SECONDS,
    IdempotencyRecord,
    IdempotencyState,
)


def _new_claim_token() -> str:
    return str(uuid4())


class SqlAlchemyIdempotencyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def start(
        self,
        namespace: str,
        key: str,
        *,
        claim_ttl_seconds: int = DEFAULT_CLAIM_TTL_SECONDS,
    ) -> IdempotencyRecord:
        """Claim this key, or report who already holds it.

        ``owned`` is the caller's authority to do the work and ``claim_token``
        is the proof it will present when settling. Exactly one caller can
        obtain them per claim: either by inserting the row, or by winning the
        conditional update that reclaims an expired claim.
        """
        existing = await self._get_row(namespace, key)
        if existing is not None:
            return await self._claim_existing(existing, claim_ttl_seconds)

        token = _new_claim_token()
        try:
            async with self._session.begin_nested():
                self._session.add(
                    IdempotencyRecordModel(
                        id=str(uuid4()),
                        namespace=namespace,
                        key=key,
                        state=IdempotencyState.STARTED.value,
                        result={},
                        claimed_at=utc_now(),
                        claim_token=token,
                    )
                )
        except IntegrityError:
            # A concurrent caller won the race to create this key.
            raced = await self._get_row(namespace, key)
            if raced is None:
                raise ConflictError(
                    "The idempotency key is contended.",
                    code="idempotency_key_contended",
                    namespace=namespace,
                ) from None
            return await self._claim_existing(raced, claim_ttl_seconds)

        return IdempotencyRecord(
            namespace=namespace,
            key=key,
            state=IdempotencyState.STARTED,
            owned=True,
            claim_token=token,
        )

    async def complete(
        self, namespace: str, key: str, result: Mapping[str, Any], *, claim_token: str
    ) -> None:
        await self._settle(
            namespace,
            key,
            claim_token=claim_token,
            state=IdempotencyState.COMPLETED,
            result=dict(result),
        )

    async def fail(self, namespace: str, key: str, reason: str, *, claim_token: str) -> None:
        await self._settle(
            namespace,
            key,
            claim_token=claim_token,
            state=IdempotencyState.FAILED,
            result={"reason": reason},
        )

    async def _settle(
        self,
        namespace: str,
        key: str,
        *,
        claim_token: str,
        state: IdempotencyState,
        result: dict[str, Any],
    ) -> None:
        """Record an outcome, but only for the caller that still owns the claim.

        The claim token is part of the WHERE clause rather than something read
        and then checked, so a claim that expires and is taken over between the
        read and the write cannot be settled by its former owner. Settling a
        row that is no longer ``STARTED`` is refused for the same reason: the
        outcome is already decided and overwriting it would lose the result a
        caller was handed.
        """
        updated = cast(
            "CursorResult[Any]",
            await self._session.execute(
                sa.update(IdempotencyRecordModel)
                .where(
                    IdempotencyRecordModel.namespace == namespace,
                    IdempotencyRecordModel.key == key,
                    IdempotencyRecordModel.state == IdempotencyState.STARTED.value,
                    IdempotencyRecordModel.claim_token == claim_token,
                )
                .values(state=state.value, result=result, updated_at=utc_now())
                .execution_options(synchronize_session=False),
            ),
        )
        if updated.rowcount == 1:
            return

        if await self._get_row(namespace, key) is None:
            raise NotFoundError(
                "Idempotency record not found.", code="idempotency_record_not_found"
            )
        raise ConflictError(
            "This attempt no longer owns the idempotency claim.",
            code="idempotency_claim_lost",
            namespace=namespace,
        )

    async def _claim_existing(
        self, row: IdempotencyRecordModel, claim_ttl_seconds: int
    ) -> IdempotencyRecord:
        token: str | None = None
        if row.state == IdempotencyState.STARTED.value:
            token = await self._reclaim_if_expired(row, claim_ttl_seconds)
        return IdempotencyRecord(
            namespace=row.namespace,
            key=row.key,
            state=IdempotencyState(row.state),
            result=row.result,
            owned=token is not None,
            claim_token=token,
        )

    async def _reclaim_if_expired(
        self, row: IdempotencyRecordModel, claim_ttl_seconds: int
    ) -> str | None:
        """Take over a claim whose owner never settled it.

        The staleness arithmetic happens in Python because not every backend
        stores timezone-aware timestamps, but the handover stays atomic: the
        update is a compare-and-swap on the claim token we observed. If another
        caller reclaims first, its write changes that token and this statement
        matches nothing. Returns the new token on success, ``None`` otherwise.
        """
        if utc_now() - ensure_utc(row.claimed_at) < timedelta(seconds=claim_ttl_seconds):
            return None

        token = _new_claim_token()
        # DML returns a CursorResult; only that carries `rowcount`.
        result = cast(
            "CursorResult[Any]",
            await self._session.execute(
                sa.update(IdempotencyRecordModel)
                .where(
                    IdempotencyRecordModel.id == row.id,
                    IdempotencyRecordModel.state == IdempotencyState.STARTED.value,
                    IdempotencyRecordModel.claim_token == row.claim_token,
                )
                .values(claimed_at=utc_now(), claim_token=token)
                .execution_options(synchronize_session=False),
            ),
        )
        if result.rowcount != 1:
            return None
        # The row in this session's identity map still holds the old claim;
        # refresh it so a later read in the same unit of work is not stale.
        await self._session.refresh(row)
        return token

    async def _get_row(self, namespace: str, key: str) -> IdempotencyRecordModel | None:
        return (
            await self._session.execute(
                sa.select(IdempotencyRecordModel).where(
                    IdempotencyRecordModel.namespace == namespace,
                    IdempotencyRecordModel.key == key,
                )
            )
        ).scalar_one_or_none()

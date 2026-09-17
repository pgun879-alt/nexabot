"""SQLAlchemy implementation of the idempotency repository port."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nexabot.domain.common.errors import ConflictError, NotFoundError
from nexabot.domain.common.time import utc_now
from nexabot.infrastructure.database.models import IdempotencyRecordModel
from nexabot.ports.repositories.idempotency import (
    DEFAULT_CLAIM_TTL_SECONDS,
    IdempotencyRecord,
    IdempotencyState,
)


def _as_utc(value: datetime) -> datetime:
    """Backends without timezone support hand back naive UTC timestamps."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


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

        ``owned`` is the caller's authority to do the work. Exactly one caller
        can obtain it per claim: either by inserting the row, or by winning the
        conditional update that reclaims an expired claim.
        """
        existing = await self._get_row(namespace, key)
        if existing is not None:
            return await self._claim_existing(existing, claim_ttl_seconds)

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
            namespace=namespace, key=key, state=IdempotencyState.STARTED, owned=True
        )

    async def complete(self, namespace: str, key: str, result: Mapping[str, Any]) -> None:
        row = await self._get_row(namespace, key)
        if row is None:
            raise NotFoundError(
                "Idempotency record not found.", code="idempotency_record_not_found"
            )
        row.state = IdempotencyState.COMPLETED.value
        row.result = dict(result)

    async def fail(self, namespace: str, key: str, reason: str) -> None:
        row = await self._get_row(namespace, key)
        if row is None:
            raise NotFoundError(
                "Idempotency record not found.", code="idempotency_record_not_found"
            )
        row.state = IdempotencyState.FAILED.value
        row.result = {"reason": reason}

    async def _claim_existing(
        self, row: IdempotencyRecordModel, claim_ttl_seconds: int
    ) -> IdempotencyRecord:
        owned = False
        if row.state == IdempotencyState.STARTED.value:
            owned = await self._reclaim_if_expired(row, claim_ttl_seconds)
        return IdempotencyRecord(
            namespace=row.namespace,
            key=row.key,
            state=IdempotencyState(row.state),
            result=row.result,
            owned=owned,
        )

    async def _reclaim_if_expired(
        self, row: IdempotencyRecordModel, claim_ttl_seconds: int
    ) -> bool:
        """Take over a claim whose owner never settled it.

        The staleness arithmetic happens in Python because not every backend
        stores timezone-aware timestamps, but the handover stays atomic: the
        update is a compare-and-swap on the claim timestamp we observed. If
        another caller reclaims first, its write changes that value and this
        statement matches nothing.
        """
        if utc_now() - _as_utc(row.claimed_at) < timedelta(seconds=claim_ttl_seconds):
            return False

        # DML returns a CursorResult; only that carries `rowcount`.
        result = cast(
            "CursorResult[Any]",
            await self._session.execute(
                sa.update(IdempotencyRecordModel)
                .where(
                    IdempotencyRecordModel.id == row.id,
                    IdempotencyRecordModel.state == IdempotencyState.STARTED.value,
                    IdempotencyRecordModel.claimed_at == row.claimed_at,
                )
                .values(claimed_at=utc_now())
                .execution_options(synchronize_session=False)
            ),
        )
        return result.rowcount == 1

    async def _get_row(self, namespace: str, key: str) -> IdempotencyRecordModel | None:
        return (
            await self._session.execute(
                sa.select(IdempotencyRecordModel).where(
                    IdempotencyRecordModel.namespace == namespace,
                    IdempotencyRecordModel.key == key,
                )
            )
        ).scalar_one_or_none()

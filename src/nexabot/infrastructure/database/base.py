"""SQLAlchemy declarative base and shared metadata."""

from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from nexabot.domain.common.time import utc_now

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = sa.MetaData(naming_convention=NAMING_CONVENTION)


def ensure_utc(value: datetime) -> datetime:
    """Re-attach UTC to a timestamp a backend handed back naive.

    Every timestamp NexaBot writes is timezone-aware UTC, but not every backend
    stores the offset: PostgreSQL's ``timestamptz`` returns it, SQLite does
    not. A naive value reaching a domain entity is a latent ``TypeError`` —
    subtracting it from ``utc_now()`` (as the agent run deadline does) raises
    "can't subtract offset-naive and offset-aware datetimes" only on the
    backend where it was not caught. Normalising at the row-to-entity boundary
    keeps that difference out of the domain entirely.
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def ensure_utc_optional(value: datetime | None) -> datetime | None:
    return None if value is None else ensure_utc(value)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

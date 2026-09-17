"""Translate SQLAlchemy failures into the domain error taxonomy.

Persistence exceptions must not cross the ports boundary. Application code is
written against ``NexaBotError`` and interfaces map that taxonomy onto HTTP or
Telegram replies; a raw ``IntegrityError`` escaping a repository bypasses all of
that and surfaces as an opaque 500.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.exc import DBAPIError, IntegrityError, SQLAlchemyError

from nexabot.domain.common.errors import ConflictError, ExternalServiceError


@contextmanager
def translate_database_errors(*, operation: str) -> Iterator[None]:
    """Re-raise SQLAlchemy errors as domain errors.

    Safe to wrap ``await`` expressions inside: the exception propagates through
    the block exactly as it would in synchronous code.
    """
    try:
        yield
    except IntegrityError as exc:
        # Unique/foreign-key violations are a caller-visible conflict, not an outage.
        raise ConflictError(
            "The requested change conflicts with existing data.",
            code="database_conflict",
            operation=operation,
        ) from exc
    except DBAPIError as exc:
        # Connection loss, timeouts, deadlocks: worth retrying.
        raise ExternalServiceError(
            "The database is currently unavailable.",
            code="database_unavailable",
            retryable=True,
            operation=operation,
        ) from exc
    except SQLAlchemyError as exc:
        raise ExternalServiceError(
            "The database rejected the operation.",
            code="database_error",
            retryable=False,
            operation=operation,
        ) from exc

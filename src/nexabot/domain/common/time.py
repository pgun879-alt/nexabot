"""Time helpers.

Using a single helper keeps tests deterministic when clocks are injected later.
"""

from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    return datetime.now(UTC)

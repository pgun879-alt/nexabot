"""Health endpoints for orchestration and deployments."""

from __future__ import annotations

from typing import Protocol

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict


class ComponentHealth(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str
    details: dict[str, object] = {}


class ReadinessReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str
    components: dict[str, ComponentHealth]


class ReadinessChecker(Protocol):
    async def check(self) -> ReadinessReport:
        """Verify critical dependencies."""


router = APIRouter(tags=["health"])


@router.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready", response_model=ReadinessReport)
async def ready(request: Request) -> ReadinessReport:
    checker: ReadinessChecker | None = getattr(request.app.state, "readiness_checker", None)
    if checker is None:
        report = ReadinessReport(
            status="degraded",
            components={
                "application": ComponentHealth(status="ok"),
                "dependencies": ComponentHealth(status="unknown"),
            },
        )
    else:
        report = await checker.check()

    if report.status != "ok":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=report.model_dump()
        )
    return report

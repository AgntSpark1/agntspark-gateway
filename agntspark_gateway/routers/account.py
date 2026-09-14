"""GET /v1/account/usage — the caller's plan, limits and current usage."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..schemas.account import AccountUsage, PlanLimitsOut, UsageOut
from ..security.dependencies import Principal, get_current_principal
from ..services import quota_service

router = APIRouter(prefix="/v1/account", tags=["account"])


@router.get("/usage", response_model=AccountUsage)
async def usage(
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
) -> AccountUsage:
    user, limits, current = await quota_service.usage_report(db, user_id=principal.user_id)
    return AccountUsage(
        plan=user.plan,
        exempt=quota_service.is_exempt(user),
        limits=PlanLimitsOut(**asdict(limits)),
        usage=UsageOut(**asdict(current)),
    )

"""Admin-only endpoints (Role.ADMIN): invite codes for invite-only registration."""

from __future__ import annotations

import uuid

from agntspark_core.auth import Role
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models.invite import Invite
from ..schemas.invites import InviteCreateRequest, InviteCreateResponse, InviteOut
from ..security.dependencies import Principal, require_role
from ..services import invite_service

router = APIRouter(prefix="/v1/admin", tags=["admin"])
_require_admin = require_role(Role.ADMIN)


def _invite_out(invite: Invite) -> InviteOut:
    return InviteOut(
        id=invite.id,
        code_prefix=invite.code_prefix,
        note=invite.note,
        max_uses=invite.max_uses,
        use_count=invite.use_count,
        status=invite.status,  # type: ignore[arg-type]  # Invite.status only returns the Literal values
        expires_at=invite.expires_at,
        created_at=invite.created_at,
    )


@router.post("/invites", response_model=InviteCreateResponse, status_code=201)
async def create_invite(
    body: InviteCreateRequest,
    principal: Principal = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> InviteCreateResponse:
    invite, code = await invite_service.create_invite(
        db,
        created_by=principal.user_id,
        note=body.note.strip(),
        max_uses=body.max_uses,
        expires_in_days=body.expires_in_days,
    )
    return InviteCreateResponse(**_invite_out(invite).model_dump(), code=code)


@router.get("/invites", response_model=list[InviteOut])
async def list_invites(
    _: Principal = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[InviteOut]:
    return [_invite_out(i) for i in await invite_service.list_invites(db)]


@router.delete("/invites/{invite_id}", status_code=204)
async def revoke_invite(
    invite_id: uuid.UUID,
    _: Principal = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    await invite_service.revoke_invite(db, invite_id=invite_id)

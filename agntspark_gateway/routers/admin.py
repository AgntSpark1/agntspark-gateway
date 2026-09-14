"""Admin-only endpoints (Role.ADMIN): invite codes, and users' roles and plans."""

from __future__ import annotations

import uuid

from agntspark_core.auth import Role
from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..exceptions import AuthorisationError, UserNotFoundError
from ..models.agent import Agent
from ..models.invite import Invite
from ..models.user import User
from ..roles import role_to_str, str_to_role
from ..schemas.account import AdminUserOut, AdminUserUpdate
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


def _user_out(user: User, agents: int) -> AdminUserOut:
    return AdminUserOut(
        id=user.id,
        email=user.email,
        name=user.name,
        role=role_to_str(user.role_enum),
        plan=user.plan,
        is_active=user.is_active,
        agents=agents,
        created_at=user.created_at,
    )


async def _agent_count(db: AsyncSession, user_id: uuid.UUID) -> int:
    query = select(func.count()).select_from(Agent).where(Agent.user_id == user_id)
    return int((await db.execute(query)).scalar_one())


@router.get("/users", response_model=list[AdminUserOut])
async def list_users(
    _: Principal = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[AdminUserOut]:
    rows = await db.execute(
        select(User, func.count(Agent.id))
        .outerjoin(Agent, Agent.user_id == User.id)
        .group_by(User.id)
        .order_by(User.created_at.desc())
    )
    return [_user_out(user, int(agents)) for user, agents in rows.all()]


@router.patch("/users/{user_id}", response_model=AdminUserOut)
async def update_user(
    user_id: uuid.UUID,
    body: AdminUserUpdate,
    principal: Principal = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminUserOut:
    user = await db.get(User, user_id)
    if user is None:
        raise UserNotFoundError(str(user_id))

    # An admin can't lock themselves out by demoting or deactivating their own account.
    if user.id == principal.user_id and (
        (body.role is not None and body.role != "admin") or body.is_active is False
    ):
        raise AuthorisationError(str(principal.user_id), "change own admin access")

    if body.role is not None:
        user.role = int(str_to_role(body.role))
    if body.plan is not None:
        user.plan = body.plan
    if body.is_active is not None:
        user.is_active = body.is_active
    await db.commit()
    await db.refresh(user)
    return _user_out(user, await _agent_count(db, user.id))

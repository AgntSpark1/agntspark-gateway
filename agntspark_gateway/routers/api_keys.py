"""POST/GET /v1/api-keys, DELETE /v1/api-keys/{id}."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..exceptions import AuthenticationError
from ..models.api_key import ApiKey
from ..models.user import User
from ..schemas.api_keys import ApiKeyCreateRequest, ApiKeyCreateResponse, ApiKeyOut
from ..security.dependencies import Principal, get_current_principal, require_jwt_principal
from ..services.api_key_service import create_api_key, list_api_keys, revoke_api_key

router = APIRouter(prefix="/v1/api-keys", tags=["api-keys"])


def _key_out(key: ApiKey) -> ApiKeyOut:
    return ApiKeyOut(
        id=key.id,
        label=key.label,
        keyPreview=f"{key.key_prefix}...",
        createdAt=key.created_at,
        lastUsedAt=key.last_used_at,
        scopes=key.scopes,
    )


@router.post("", response_model=ApiKeyCreateResponse, status_code=201)
async def create_key(
    body: ApiKeyCreateRequest,
    principal: Principal = Depends(require_jwt_principal),
    db: AsyncSession = Depends(get_db),
) -> ApiKeyCreateResponse:
    user = await db.get(User, principal.user_id)
    if user is None:
        raise AuthenticationError("Invalid or expired token.")
    key, raw_key = await create_api_key(db, user=user, label=body.label, scopes=body.scopes)
    return ApiKeyCreateResponse(**_key_out(key).model_dump(), key=raw_key)


@router.get("", response_model=list[ApiKeyOut])
async def list_keys(
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
) -> list[ApiKeyOut]:
    keys = await list_api_keys(db, user_id=principal.user_id)
    return [_key_out(k) for k in keys]


@router.delete("/{key_id}", status_code=204)
async def delete_key(
    key_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
) -> None:
    await revoke_api_key(db, user_id=principal.user_id, key_id=key_id)

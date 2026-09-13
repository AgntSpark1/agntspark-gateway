"""POST /v1/auth/register, POST /v1/auth/login, GET /v1/auth/me."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..exceptions import AuthenticationError
from ..models.user import User
from ..roles import role_to_str
from ..schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserOut
from ..security.dependencies import Principal, get_current_principal
from ..security.jwt import create_access_token
from ..security.rate_limit import enforce_login_rate_limit
from ..services.auth_service import authenticate_user, register_user

router = APIRouter(prefix="/v1/auth", tags=["auth"])


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        name=user.name,
        email=user.email,
        role=role_to_str(user.role_enum),
    )


def _token_response(user: User) -> TokenResponse:
    token, expires_in = create_access_token(user_id=user.id, email=user.email, role=user.role)
    return TokenResponse(
        access_token=token,
        expires_in=expires_in,
        user=_user_out(user),
    )


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=201,
    dependencies=[Depends(enforce_login_rate_limit)],
)
async def register(body: RegisterRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    user = await register_user(db, email=body.email, password=body.password, name=body.name)
    return _token_response(user)


@router.post(
    "/login", response_model=TokenResponse, dependencies=[Depends(enforce_login_rate_limit)]
)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    user = await authenticate_user(db, email=body.email, password=body.password)
    return _token_response(user)


@router.get("/me", response_model=UserOut)
async def me(
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
) -> UserOut:
    user = await db.get(User, principal.user_id)
    if user is None:
        # The account backing an already-validated token was deleted
        # between authentication and this lookup — vanishingly rare.
        raise AuthenticationError("Invalid or expired token.")
    return _user_out(user)

"""/v1/agents/* — reserved by the API contract (agntspark-sdk already calls
these paths) but not implemented yet. Real Agent CRUD/deploy/scale/logs
logic is a separate future task (Agent Runtime).

Auth still runs first: an unauthenticated or unauthorized caller gets
401/403, never 501 — so the contract behaves correctly once real logic
lands here.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..exceptions import NotImplementedStubError
from ..security.dependencies import Principal, get_current_principal

router = APIRouter(prefix="/v1/agents", tags=["agents"])


@router.api_route("", methods=["GET", "POST"])
@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def agents_stub(
    request: Request,
    path: str = "",
    principal: Principal = Depends(get_current_principal),
) -> None:
    raise NotImplementedStubError(request.url.path)

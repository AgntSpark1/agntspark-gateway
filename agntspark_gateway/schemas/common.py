"""Shared response schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    """SDK-compatible error envelope — mirrors ``AgntSparkError.to_dict()``."""

    code: str
    message: str
    details: dict[str, Any] | None = None

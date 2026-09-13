"""Agent ORM model.

Stores agent identity/metadata plus a flattened DeployConfig (matching
agntspark-sdk's ``DeployConfig`` shape). ``env`` is a JSON array of
``{key, value, secret}`` objects — for entries with ``secret: true``,
``value`` is Fernet-ciphertext (see ``security/secrets.py``), decrypted
only right before injection into a container's environment.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ARRAY, JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    runtime: Mapped[str] = mapped_column(String(32), nullable=False, default="python3.12")
    framework: Mapped[str] = mapped_column(String(32), nullable=False, default="custom")
    model: Mapped[str] = mapped_column(String(128), nullable=False, default="gpt-4o")
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    agent_metadata: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)

    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # --- flattened DeployConfig ---
    # NOTE: unlike the other deploy-config fields below, `replicas` doubles
    # as the *live* running-replica count once deployed (see
    # deploy_agent/scale_agent in agent_service.py, which overwrite it from
    # actual Docker state) — it defaults to 0 because an undeployed agent
    # has zero running replicas, matching agntspark-sdk's AgentResponse
    # docstring ("Current number of running replicas").
    replicas: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cpu: Mapped[float] = mapped_column(nullable=False, default=1.0)
    memory_mb: Mapped[int] = mapped_column(Integer, nullable=False, default=512)
    gpu: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    gpu_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    disk_gb: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    ephemeral_storage_gb: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    env: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    image: Mapped[str | None] = mapped_column(String(512), nullable=True)
    build_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    command: Mapped[str | None] = mapped_column(String(512), nullable=True)
    args: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    health_check_path: Mapped[str | None] = mapped_column(String(256), nullable=True)
    auto_scale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    min_replicas: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    max_replicas: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    port: Mapped[int] = mapped_column(Integer, nullable=False, default=8080)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

"""Schemas for /v1/agents* — field-for-field compatible with the
Pydantic models agntspark-sdk already ships (agntspark/models.py), since
the SDK deserialises gateway responses directly into those classes.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class AgentStatus(str, Enum):
    PENDING = "pending"
    BUILDING = "building"
    STARTING = "starting"
    RUNNING = "running"
    SCALING = "scaling"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"
    CRASHED = "crashed"


class AgentRuntimeKind(str, Enum):
    PYTHON_3_12 = "python3.12"
    PYTHON_3_11 = "python3.11"
    PYTHON_3_10 = "python3.10"
    NODE_20 = "node20"
    NODE_22 = "node22"
    CUSTOM = "custom"


class ScaleDirection(str, Enum):
    UP = "up"
    DOWN = "down"


class ResourceLimits(BaseModel):
    cpu: float = Field(default=1.0, ge=0.1, le=64.0)
    memory_mb: int = Field(default=512, ge=128, le=65536)
    gpu: int = Field(default=0, ge=0, le=8)
    gpu_type: str | None = None
    disk_gb: int = Field(default=10, ge=1, le=1000)
    ephemeral_storage_gb: int = Field(default=5, ge=1, le=500)


class EnvVar(BaseModel):
    key: str = Field(min_length=1, max_length=256)
    value: str = Field(default="", max_length=4096)
    secret: bool = False


class DeployConfig(BaseModel):
    replicas: int = Field(default=1, ge=1, le=100)
    resources: ResourceLimits = Field(default_factory=ResourceLimits)
    env: list[EnvVar] = Field(default_factory=list)
    # Neither image nor build_path → the platform's runtime image
    # (settings.docker_image), which serves the runtime contract.
    image: str | None = None
    build_path: str | None = None
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    health_check_path: str | None = None
    auto_scale: bool = False
    min_replicas: int = Field(default=1, ge=1, le=100)
    max_replicas: int = Field(default=10, ge=1, le=100)
    port: int = Field(default=8080, ge=1, le=65535)

    @field_validator("max_replicas")
    @classmethod
    def _max_ge_min(cls, v: int, info: Any) -> int:
        min_r = info.data.get("min_replicas", 1)
        if v < min_r:
            raise ValueError("max_replicas must be >= min_replicas")
        return v


class AgentConfigIn(BaseModel):
    """Request body for POST /v1/agents."""

    name: str = Field(min_length=1, max_length=128)
    runtime: AgentRuntimeKind = AgentRuntimeKind.PYTHON_3_12
    framework: str = "custom"
    model: str = "gpt-4o"
    api_key: str | None = None
    system_prompt: str | None = Field(default=None, max_length=32_000)
    deploy: DeployConfig | None = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("tags")
    @classmethod
    def _validate_tags(cls, v: list[str]) -> list[str]:
        for tag in v:
            if len(tag) > 64:
                raise ValueError(f"Tag {tag[:20]!r}... exceeds 64 characters")
        return v


class AgentResponse(BaseModel):
    id: str
    name: str
    runtime: AgentRuntimeKind
    framework: str
    model: str
    status: AgentStatus
    created_at: datetime
    updated_at: datetime
    url: str | None = None
    deploy: DeployConfig | None = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)
    error: str | None = None
    version: int = 1
    replicas: int = 0


class AgentListResponse(BaseModel):
    agents: list[AgentResponse]
    total: int
    page: int = 1
    page_size: int = 20
    has_next: bool = False


class AgentLog(BaseModel):
    agent_id: str
    replica_id: str
    timestamp: datetime
    level: str
    message: str
    source: str = "stdout"
    metadata: dict[str, Any] = Field(default_factory=dict)


class LogListResponse(BaseModel):
    logs: list[AgentLog]
    total: int
    has_next: bool = False
    next_cursor: str | None = None


class Metrics(BaseModel):
    agent_id: str
    timestamp: datetime
    cpu_percent: float = Field(ge=0.0)
    memory_mb: int = Field(ge=0)
    memory_percent: float = Field(ge=0.0, le=100.0)
    gpu_percent: float = 0.0
    gpu_memory_mb: int = 0
    request_count: int = 0
    request_rate: float = 0.0
    error_count: int = 0
    error_rate: float = 0.0
    p50_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0
    replicas: int = 0


class ScaleRequest(BaseModel):
    direction: ScaleDirection
    count: int = Field(default=1, ge=1, le=50)
    reason: str | None = Field(default=None, max_length=500)


class ScaleResponse(BaseModel):
    agent_id: str
    previous_replicas: int
    current_replicas: int
    direction: ScaleDirection
    status: AgentStatus

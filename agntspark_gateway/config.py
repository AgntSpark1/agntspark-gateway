"""Configuration for AgntSpark Gateway.

Follows the same pydantic-settings convention as ``agntspark_core.config``,
but uses a dedicated ``AGNTSPARK_GATEWAY_`` prefix so the gateway's settings
never cross-wire with agntspark-core's ``AGNTSPARK_`` settings when both
processes share an environment (e.g. the same ``.env`` / compose file).
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class GatewaySettings(BaseSettings):
    """Global gateway settings, populated from the environment."""

    model_config = SettingsConfigDict(
        env_prefix="AGNTSPARK_GATEWAY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+asyncpg://agntspark:agntspark@localhost:5432/agntspark_gateway"
    jwt_secret: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60
    bcrypt_rounds: int = 12
    api_key_prefix: str = "agnt"
    cors_allow_origins: list[str] = ["http://localhost:5173"]
    redis_url: str | None = None
    log_level: str = "INFO"
    environment: str = "development"


settings = GatewaySettings()

__all__ = ["GatewaySettings", "settings"]

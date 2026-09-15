"""Configuration for AgntSpark Gateway.

Follows the same pydantic-settings convention as ``agntspark_core.config``,
but uses a dedicated ``AGNTSPARK_GATEWAY_`` prefix so the gateway's settings
never cross-wire with agntspark-core's ``AGNTSPARK_`` settings when both
processes share an environment (e.g. the same ``.env`` / compose file).
"""

from __future__ import annotations

from typing import Literal

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
    # Who may create an account: anyone ("open"), only holders of an
    # admin-issued invite code ("invite"), or nobody ("closed").
    registration_mode: Literal["open", "invite", "closed"] = "open"

    # Stripe billing. Leaving the key or the Pro price unset disables it.
    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None
    stripe_price_pro: str | None = None
    # The console's public URL, where Stripe sends people back to.
    public_base_url: str = "http://localhost:5173"
    cors_allow_origins: list[str] = ["http://localhost:5173"]
    redis_url: str | None = None
    log_level: str = "INFO"
    environment: str = "development"

    # Agent Runtime / Control Plane
    docker_socket: str = "unix:///var/run/docker.sock"
    docker_image: str = "agntspark/agent-runtime:latest"
    docker_network: str = "agntspark-net"
    # Public ingress for deployed agents: https://<slug>.<agent_base_domain>.
    # Unset disables it (AgentResponse.url stays null).
    agent_base_domain: str | None = None
    # Shared secret the edge proxy presents on /internal/ingress/route; unset
    # rejects every routing lookup.
    ingress_internal_token: str | None = None
    # Requests per minute one client IP may send an agent, unless its owner
    # sets its own rate_limit_rpm.
    ingress_default_rpm_per_ip: int = 120
    scheduler_interval_seconds: int = 30
    login_rate_limit_max_attempts: int = 10
    login_rate_limit_window_seconds: int = 300
    # Fernet key (44-char urlsafe-base64) for encrypting secret env vars at
    # rest. Generate a real one for production with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    secret_encryption_key: str = "Jyw3CWB_wTCDeOw8q_dNNJin6tfrQLnltmnRf0UB2T0="


settings = GatewaySettings()

__all__ = ["GatewaySettings", "settings"]

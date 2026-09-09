"""Kernel settings.

Environment (prefix `AFABRIC_`) or a `.env` file. Desired-state configuration is a
separate concern and lives in fabric.yaml + fabric.d/ — see phase 2.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic_settings import BaseSettings, SettingsConfigDict


class Transport(StrEnum):
    """Which backend the ToolBus dispatches to.

    The choice is about *authentication*, not capability: MCP carries a user identity
    from a browser or device-code sign-in, REST carries a service principal for
    unattended runs. Modules never see the difference.
    """

    MCP = "mcp"
    REST = "rest"


class TokenCache(StrEnum):
    """How the interactive sign-in is persisted between commands.

    Encrypted persistence needs libsecret and a session keyring, which a headless VM
    usually lacks. `AUTO` tries encrypted first and falls back to a plaintext cache
    with a warning; choose `NONE` to sign in every time instead.
    """

    AUTO = "auto"
    ENCRYPTED = "encrypted"
    UNENCRYPTED = "unencrypted"
    NONE = "none"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AFABRIC_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Entra ---------------------------------------------------------------
    tenant_id: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    scope: str = "https://api.fabric.microsoft.com/.default"

    # --- Endpoints -----------------------------------------------------------
    mcp_endpoint: str = "https://api.fabric.microsoft.com/v1/mcp/core"
    api_base: str = "https://api.fabric.microsoft.com/v1"

    transport: Transport = Transport.MCP
    token_cache: TokenCache = TokenCache.AUTO

    # --- Kernel --------------------------------------------------------------
    journal_path: str = ".afabric/journal.jsonl"
    max_blast_radius: int = 25

    # --- Agent loop ----------------------------------------------------------
    model: str = "claude-sonnet-5"

    @property
    def has_service_principal(self) -> bool:
        return bool(self.tenant_id and self.client_id and self.client_secret)


def load_settings() -> Settings:
    return Settings()

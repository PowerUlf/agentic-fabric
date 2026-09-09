"""Entra credentials for both transports.

Two identities, one token surface:

- **Interactive** — device code by default. The host is often a VM without a usable
  browser, and device code degrades to "open this URL on any device", which always
  works.
- **Service principal** — client credentials, used when tenant, client and secret are
  all configured. This is the unattended path.

Making a sign-in survive between commands takes two things, and both are easy to miss:
a token cache, *and* an `AuthenticationRecord` handed back on the next construction.
Without the record azure-identity has no account to look up and prompts again, however
warm the cache is. The record holds no secrets — only which account signed in — so it
sits next to the cache as plain JSON.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Protocol

from azure.core.credentials import AccessToken
from azure.identity import (
    AuthenticationRecord,
    ClientSecretCredential,
    DeviceCodeCredential,
    InteractiveBrowserCredential,
    TokenCachePersistenceOptions,
)

from afabric.kernel.config import Settings, TokenCache

_CACHE_NAME = "agentic-fabric"
_RECORD_PATH = Path.home() / ".config" / "agentic-fabric" / "auth_record.json"


class Credential(Protocol):
    def get_token(self, *scopes: str) -> AccessToken: ...


class AuthError(RuntimeError):
    pass


# --- authentication record ----------------------------------------------------


def _load_record() -> AuthenticationRecord | None:
    if not _RECORD_PATH.exists():
        return None
    try:
        return AuthenticationRecord.deserialize(_RECORD_PATH.read_text())
    except Exception:
        # A corrupt record must never be fatal — sign in again instead.
        return None


def _save_record(record: AuthenticationRecord) -> None:
    _RECORD_PATH.parent.mkdir(parents=True, exist_ok=True)
    _RECORD_PATH.write_text(record.serialize())
    _RECORD_PATH.chmod(0o600)


def forget() -> bool:
    """Drop the stored sign-in. Returns whether there was one."""
    if _RECORD_PATH.exists():
        _RECORD_PATH.unlink()
        return True
    return False


# --- credentials --------------------------------------------------------------


def _device_prompt(verification_uri: str, user_code: str, expires_on: datetime) -> None:
    """Show the device code on stderr.

    The default callback prints to stdout, which Python block-buffers whenever it is
    not a terminal — so the code stays invisible in logs, pipes and background runs
    until the flow has already timed out. stderr is unbuffered and always gets through.
    """
    print(
        f"\nSign in to continue:\n"
        f"  1. open {verification_uri}\n"
        f"  2. enter the code {user_code}\n"
        f"     (expires {expires_on:%H:%M:%S UTC})\n",
        file=sys.stderr,
        flush=True,
    )


def _cache_options(mode: TokenCache) -> TokenCachePersistenceOptions | None:
    if mode is TokenCache.NONE:
        return None
    return TokenCachePersistenceOptions(
        name=_CACHE_NAME,
        allow_unencrypted_storage=mode is TokenCache.UNENCRYPTED,
    )


def build_credential(
    settings: Settings,
    *,
    interactive: str | None = None,
    cache: TokenCache | None = None,
) -> Credential:
    """Return a credential for the configured identity.

    `interactive` selects a sign-in style ("device" or "browser"); it is ignored when a
    service principal is configured, since that path is non-interactive by definition.
    """
    if settings.has_service_principal:
        return ClientSecretCredential(
            tenant_id=settings.tenant_id,
            client_id=settings.client_id,
            client_secret=settings.client_secret,
        )

    mode = cache or settings.token_cache
    kwargs: dict = {}
    options = _cache_options(TokenCache.ENCRYPTED if mode is TokenCache.AUTO else mode)
    if options is not None:
        kwargs["cache_persistence_options"] = options
        record = _load_record()
        if record is not None:
            kwargs["authentication_record"] = record
    if settings.tenant_id:
        kwargs["tenant_id"] = settings.tenant_id

    if interactive == "browser":
        return InteractiveBrowserCredential(**kwargs)
    return DeviceCodeCredential(prompt_callback=_device_prompt, **kwargs)


def _is_cache_encryption_failure(exc: Exception) -> bool:
    return "encryption" in str(exc).lower()


def _authenticate(credential: Credential, settings: Settings) -> str:
    """Sign in if there is no usable record yet, then return a token."""
    if not settings.has_service_principal and settings.token_cache is not TokenCache.NONE:
        if _load_record() is None and hasattr(credential, "authenticate"):
            record = credential.authenticate(scopes=[settings.scope])
            _save_record(record)
    return credential.get_token(settings.scope).token


def acquire_token(settings: Settings, *, interactive: str | None = None) -> str:
    """Sign in if needed and return a bearer token for the Fabric API scope.

    Under `TokenCache.AUTO`, an unusable keyring is not a failure: retry once with a
    plaintext cache and warn, because on a headless box the alternative is a device
    code on every single command.
    """
    try:
        return _authenticate(build_credential(settings, interactive=interactive), settings)
    except Exception as exc:
        if settings.token_cache is TokenCache.AUTO and _is_cache_encryption_failure(exc):
            print(
                "warning: no usable keyring for the token cache — falling back to an "
                "unencrypted cache under ~/.IdentityService. "
                "Set AFABRIC_TOKEN_CACHE=none to disable caching instead.",
                file=sys.stderr,
                flush=True,
            )
            try:
                return _authenticate(
                    build_credential(
                        settings, interactive=interactive, cache=TokenCache.UNENCRYPTED
                    ),
                    settings,
                )
            except Exception as retry_exc:
                raise AuthError(
                    f"could not acquire a token for {settings.scope}: {retry_exc}"
                ) from retry_exc

        raise AuthError(f"could not acquire a token for {settings.scope}: {exc}") from exc

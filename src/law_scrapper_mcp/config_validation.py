"""Security-boundary validation for `Settings`.

Extracted from `config.py` (Task 6, Klaster 7 cleanup) so the field list in
`Settings` reads top-to-bottom without a ~65-line validator wedged between
unrelated groups of fields. Behaviour is unchanged — only the home of the
logic moved. Kept as a free function (not a method) so `config.py` can call
it from inside `Settings._enforce_security_boundary` via a local import,
which avoids a circular import at module load time (this module imports
`Settings`-adjacent constants from `config.py`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .config import MIN_AUTH_TOKEN_BYTES, is_loopback_entry

if TYPE_CHECKING:
    from .config import Settings


def enforce_security_boundary(current: Settings) -> Settings:
    """Fail fast at startup: bind network exposure to the configured auth mode."""
    if current.auth_mode == "bearer":
        if current.auth_token is not None and current.auth_token_file is not None:
            raise ValueError(
                "LAW_MCP_AUTH_TOKEN i LAW_MCP_AUTH_TOKEN_FILE ustawione jednocześnie. "
                "Wybierz jedno źródło tokenu — milcząca precedencja ukryłaby podmianę sekretu."
            )
        if current.auth_token is None and current.auth_token_file is None:
            raise ValueError("Tryb 'bearer' wymaga tokenu. Ustaw LAW_MCP_AUTH_TOKEN albo LAW_MCP_AUTH_TOKEN_FILE.")
        try:
            token = current.resolve_auth_token()
        except (OSError, UnicodeDecodeError) as error:
            raise ValueError(
                f"Nie udało się odczytać LAW_MCP_AUTH_TOKEN_FILE ({current.auth_token_file}): {error}"
            ) from error
        if len(token.encode("utf-8")) < MIN_AUTH_TOKEN_BYTES:
            raise ValueError(
                f"Token uwierzytelniający musi mieć co najmniej {MIN_AUTH_TOKEN_BYTES} bajtów UTF-8. "
                "Wygeneruj go poleceniem: openssl rand -base64 32"
            )

    if current.auth_mode == "oauth":
        required = (
            ("LAW_MCP_AUTH_ISSUER", current.auth_issuer),
            ("LAW_MCP_AUTH_AUDIENCE", current.auth_audience),
            ("LAW_MCP_AUTH_RESOURCE_SERVER_URL", current.auth_resource_server_url),
        )
        missing = [name for name, value in required if value is None]
        if missing:
            raise ValueError(
                f"Tryb 'oauth' wymaga zmiennych: {', '.join(missing)}. "
                "Serwer nie uruchomi się z niepełną konfiguracją OAuth."
            )

    if current.auth_mode == "none":
        if current.transport == "streamable-http" and not is_loopback_entry(current.host):
            raise ValueError(
                f"Bind '{current.host}' wykracza poza pętlę zwrotną przy wyłączonym uwierzytelnianiu. "
                "Ustaw LAW_MCP_AUTH_MODE na 'bearer' albo 'oauth', albo binduj na 127.0.0.1."
            )
        for name, entries in (
            ("LAW_MCP_ALLOWED_HOSTS", current.allowed_hosts),
            ("LAW_MCP_ALLOWED_ORIGINS", current.allowed_origins),
        ):
            remote = [entry for entry in entries if not is_loopback_entry(entry)]
            if remote:
                raise ValueError(
                    f"{name} zawiera wpisy spoza pętli zwrotnej ({', '.join(remote)}) "
                    "przy wyłączonym uwierzytelnianiu. Ustaw LAW_MCP_AUTH_MODE."
                )

    return current

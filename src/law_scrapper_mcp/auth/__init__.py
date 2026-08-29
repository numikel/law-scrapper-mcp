"""Authentication layer for the Streamable HTTP transport.

The SDK mounts the entire protocol envelope itself — BearerAuthBackend,
RequireAuthMiddleware, AuthContextMiddleware and the RFC 9728 routes — as soon
as `MCPServer` receives both `auth` and `token_verifier`
(mcp/server/mcpserver/server.py:158-170, :1125-1157). What is left for this
package is deciding which verifier to hand over.
"""

from __future__ import annotations

from mcp.server.auth.provider import TokenVerifier
from mcp.server.auth.settings import AuthSettings
from pydantic import AnyHttpUrl

from law_scrapper_mcp.auth.jwt_verifier import JwtTokenVerifier
from law_scrapper_mcp.auth.static_token import StaticTokenVerifier
from law_scrapper_mcp.config import Settings

__all__ = ["build_auth"]


def build_auth(current: Settings) -> tuple[AuthSettings | None, TokenVerifier | None]:
    """Translate the configured mode into the pair the SDK expects.

    Returns `(None, None)` for the unauthenticated mode. The SDK rejects a
    half-configured pair with ValueError, which is exactly the guarantee
    wanted here: there is no "empty token that lets everything past".
    """
    if current.auth_mode == "none":
        return None, None

    scopes = current.auth_required_scopes or None

    if current.auth_mode == "bearer":
        # The SDK requires an issuer even for a static secret, so the server
        # names itself as one (D16) instead of borrowing a stranger's URL.
        issuer = current.auth_resource_server_url or AnyHttpUrl(f"http://{current.host}:{current.port}")
        return (
            AuthSettings(issuer_url=issuer, resource_server_url=None, required_scopes=scopes),
            StaticTokenVerifier(token=current.resolve_auth_token(), scopes=current.auth_required_scopes),
        )

    assert current.auth_issuer is not None  # guaranteed by Settings validation
    assert current.auth_audience is not None
    return (
        AuthSettings(
            issuer_url=current.auth_issuer,
            resource_server_url=current.auth_resource_server_url,
            required_scopes=scopes,
        ),
        JwtTokenVerifier(
            issuer=str(current.auth_issuer),
            audience=current.auth_audience,
            jwks_uri=str(current.auth_jwks_uri) if current.auth_jwks_uri else None,
            algorithms=current.auth_algorithms,
            required_scopes=current.auth_required_scopes,
            cache_ttl=current.auth_jwks_cache_ttl,
        ),
    )

"""Static bearer token verification for simple and local deployments."""

from __future__ import annotations

import hmac

from mcp.server.auth.provider import AccessToken

STATIC_CLIENT_ID = "static-bearer"


class StaticTokenVerifier:
    """Compares a request token against one configured secret.

    Implements the SDK's `TokenVerifier` protocol, so the whole protocol
    envelope — the Authorization header, the 401 response, the auth context —
    stays inside the SDK. This class only answers "is this the secret".

    The comparison is constant-time rather than `==` on purpose: response time
    under `==` depends on the length of the shared prefix, which is enough to
    recover a remote secret byte by byte.
    """

    def __init__(self, *, token: str, scopes: list[str]) -> None:
        # `Settings` already refuses a short token, but this class is also
        # constructed directly, and `hmac.compare_digest(b"", b"")` is True —
        # an empty secret would accept an empty `Authorization` value. The
        # guard belongs where the comparison lives, not only upstream of it.
        if not token:
            raise ValueError(
                "Token statyczny nie może być pusty. Ustaw LAW_MCP_AUTH_TOKEN albo LAW_MCP_AUTH_TOKEN_FILE."
            )
        self._token = token.encode("utf-8")
        self._scopes = list(scopes)

    async def verify_token(self, token: str) -> AccessToken | None:
        if not hmac.compare_digest(token.encode("utf-8"), self._token):
            return None
        return AccessToken(token=token, client_id=STATIC_CLIENT_ID, scopes=list(self._scopes))

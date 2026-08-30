"""OAuth 2.1 resource server verification against a generic OIDC provider.

One implementation serves Entra ID, Google, AWS Cognito, Okta and Auth0 — they
differ by configuration, not by code (D3, A4). Providers issuing opaque tokens
(GitHub) are out of scope: they would need RFC 7662 introspection, which puts
the identity provider on the hot path of every tool call.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientError
from mcp.server.auth.provider import AccessToken

logger = logging.getLogger(__name__)

DISCOVERY_PATH = "/.well-known/openid-configuration"
DISCOVERY_TIMEOUT = 10.0


class JwtTokenVerifier:
    """Validates a signed JWT against the issuer's published key set.

    No circuit breaker guards the JWKS endpoint, unlike the Sejm API client:
    when authentication cannot be performed it must fail closed. A breaker here
    would turn an identity-provider outage into an authorization bypass.
    """

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_uri: str | None,
        algorithms: list[str],
        required_scopes: list[str],
        cache_ttl: int,
    ) -> None:
        self._issuer = issuer
        self._audience = audience
        self._configured_jwks_uri = jwks_uri
        self._algorithms = list(algorithms)
        self._required_scopes = set(required_scopes)
        self._cache_ttl = cache_ttl
        self._client: PyJWKClient | None = None
        self._lock = asyncio.Lock()

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            client = await self._jwk_client()
            signing_key = await asyncio.to_thread(client.get_signing_key_from_jwt, token)
            claims: dict[str, Any] = jwt.decode(
                token,
                signing_key.key,
                # An allowlist, never the token's own `alg` header (D18).
                algorithms=self._algorithms,
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "iss", "aud"]},
            )
        except (PyJWKClientError, httpx.HTTPError, KeyError, ValueError) as error:
            # PyJWKClientError is a jwt.PyJWTError subclass, so this clause
            # MUST come before `except jwt.PyJWTError` below — Python matches
            # top-down, and the base-class clause would otherwise silently
            # swallow it, routing real IdP outages through the INFO path.
            # KeyError/ValueError: a malformed discovery document (missing or
            # non-https `jwks_uri`) or a non-JSON JWKS/discovery body —
            # `json.JSONDecodeError` is a ValueError subclass, and neither
            # escapes as PyJWTError or httpx.HTTPError. These signal that the
            # identity provider is unreachable or misbehaving, worth a human
            # noticing — WARNING, not INFO.
            logger.warning("Odrzucono token: %s: %s", type(error).__name__, error)
            return None
        except jwt.PyJWTError as error:
            # A plain token-validation failure (bad signature, expired,
            # wrong audience/issuer, ...) is routine traffic, not an
            # operational problem — stays at INFO. Kept as type-name-only,
            # unlike the WARNING branch: `error` here can echo attacker-
            # controlled token content.
            logger.info("Odrzucono token: %s", type(error).__name__)
            return None

        scopes = _scopes_of(claims)
        if not self._required_scopes.issubset(scopes):
            logger.info("Odrzucono token: brak wymaganych uprawnień.")
            return None

        subject = claims.get("sub") or claims.get("client_id") or "unknown"
        return AccessToken(
            token=token,
            client_id=str(subject),
            scopes=sorted(scopes),
            expires_at=int(claims["exp"]),
        )

    async def _jwk_client(self) -> PyJWKClient:
        """Build the key-set client once, discovering its URI if needed."""
        if self._client is not None:
            return self._client
        async with self._lock:
            if self._client is None:
                uri = self._configured_jwks_uri or await self._discover_jwks_uri()
                # `cache_keys=True` is deliberately absent. It would enable
                # PyJWT's second cache tier: an `lru_cache` over individual
                # signing keys with no time-based expiry at all
                # (jwt/jwks_client.py:44-47, :100-104). Once a `kid` had been
                # resolved, `lifespan` would never be consulted for it again, so
                # a key the issuer retired would stay acceptable for the lifetime
                # of the process — and this server has no other revocation
                # channel. Worse, an issuer rotating the key under an unchanged
                # `kid` would leave us rejecting tokens signed with the live key
                # while still honouring the dead one. Tier 1 (`cache_jwk_set`,
                # on by default) already bounds the fetch rate, and honours
                # `lifespan`, which is what LAW_MCP_AUTH_JWKS_CACHE_TTL promises.
                self._client = PyJWKClient(uri, lifespan=self._cache_ttl)
            return self._client

    async def _discover_jwks_uri(self) -> str:
        url = f"{self._issuer.rstrip('/')}{DISCOVERY_PATH}"
        async with httpx.AsyncClient(timeout=DISCOVERY_TIMEOUT) as client:
            response = await client.get(url)
            response.raise_for_status()
            jwks_uri = str(response.json()["jwks_uri"])
            if not jwks_uri.startswith("https://"):
                # The discovery document itself arrived over HTTPS, but a
                # misconfigured or compromised IdP could still point key
                # fetching at plaintext HTTP — fail closed, same as every
                # other rejection path here.
                raise ValueError(f"Odkryty jwks_uri nie używa https: {jwks_uri!r}")
            return jwks_uri


def _scopes_of(claims: dict[str, Any]) -> set[str]:
    """Read scopes from either shape providers use: `scope` or `scp`."""
    raw = claims.get("scope") or claims.get("scp") or []
    if isinstance(raw, str):
        return set(raw.split())
    return {str(item) for item in raw}

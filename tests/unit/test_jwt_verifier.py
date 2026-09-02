"""OAuth 2.1 resource server mode: signature, audience, issuer, scopes.

JWKS retrieval is patched at `PyJWKClient.fetch_data` rather than mocked with
respx: PyJWT fetches the key set over urllib, which respx (an httpx mock) never
sees. Discovery is ours and goes over httpx, so that half uses respx.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac as hmac_module
import json
import logging
import time
from typing import Any

import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm
from jwt.exceptions import PyJWKClientConnectionError

from law_scrapper_mcp.auth import jwt_verifier as jwt_verifier_module
from law_scrapper_mcp.auth.jwt_verifier import JwtTokenVerifier

ISSUER = "https://login.example.com/tenant/v2.0"
AUDIENCE = "api://law-scrapper"
JWKS_URI = "https://login.example.com/tenant/discovery/keys"
KID = "test-key-1"

pytestmark = pytest.mark.asyncio


@pytest.fixture(scope="module")
def keypair() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def jwks(keypair: rsa.RSAPrivateKey) -> dict[str, Any]:
    key = json.loads(RSAAlgorithm.to_jwk(keypair.public_key()))
    key.update({"kid": KID, "use": "sig", "alg": "RS256"})
    return {"keys": [key]}


@pytest.fixture
def fetch_calls(monkeypatch: pytest.MonkeyPatch, jwks: dict[str, Any]) -> list[str]:
    """Patch the JWKS fetch and count how often the network would be hit."""
    calls: list[str] = []

    def fake_fetch(self: jwt.PyJWKClient) -> dict[str, Any]:
        calls.append(self.uri)
        # The real `fetch_data` writes the result into the key-set cache on
        # success (jwt/jwks_client.py:132-134). A stub that skips that step
        # leaves tier-1 caching inert, and a test counting fetches then measures
        # whichever *other* cache happens to be enabled instead of the one
        # LAW_MCP_AUTH_JWKS_CACHE_TTL is supposed to control.
        if self.jwk_set_cache is not None:
            self.jwk_set_cache.put(jwks)
        return jwks

    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", fake_fetch)
    return calls


def make_token(keypair: rsa.RSAPrivateKey, **overrides: Any) -> str:
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "user-42",
        "exp": int(time.time()) + 600,
        "iat": int(time.time()),
        "scope": "mcp:read mcp:write",
    }
    claims.update(overrides)
    return jwt.encode(claims, keypair, algorithm="RS256", headers={"kid": KID})


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _forge_hs256_token(claims: dict[str, Any], *, secret: bytes) -> str:
    """Hand-build an HS256 JWS, bypassing PyJWT's HMAC-key sanity check."""
    header = {"alg": "HS256", "typ": "JWT", "kid": KID}
    signing_input = f"{_b64url(json.dumps(header).encode())}.{_b64url(json.dumps(claims).encode())}"
    signature = hmac_module.new(secret, signing_input.encode("ascii"), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url(signature)}"


def make_verifier(**overrides: Any) -> JwtTokenVerifier:
    kwargs: dict[str, Any] = {
        "issuer": ISSUER,
        "audience": AUDIENCE,
        "jwks_uri": JWKS_URI,
        "algorithms": ["RS256", "ES256"],
        "required_scopes": [],
        "cache_ttl": 3600,
    }
    kwargs.update(overrides)
    return JwtTokenVerifier(**kwargs)


async def test_valid_token_is_accepted(keypair, fetch_calls) -> None:
    access = await make_verifier().verify_token(make_token(keypair))
    assert access is not None
    assert access.client_id == "user-42"
    assert set(access.scopes) == {"mcp:read", "mcp:write"}


async def test_tampered_signature_is_rejected(keypair, fetch_calls) -> None:
    token = make_token(keypair)
    head, payload, signature = token.split(".")
    assert await make_verifier().verify_token(f"{head}.{payload}.{signature[:-4]}AAAA") is None


async def test_wrong_audience_is_rejected(keypair, fetch_calls) -> None:
    """Criterion 9: a token minted for another resource in the same tenant is
    cryptographically valid — this check is what stops the confused deputy."""
    assert await make_verifier().verify_token(make_token(keypair, aud="api://other")) is None


async def test_wrong_issuer_is_rejected(keypair, fetch_calls) -> None:
    assert await make_verifier().verify_token(make_token(keypair, iss="https://evil.example")) is None


async def test_expired_token_is_rejected(keypair, fetch_calls) -> None:
    assert await make_verifier().verify_token(make_token(keypair, exp=int(time.time()) - 10)) is None


async def test_missing_required_scope_is_rejected(keypair, fetch_calls) -> None:
    verifier = make_verifier(required_scopes=["mcp:admin"])
    assert await verifier.verify_token(make_token(keypair)) is None


async def test_scp_claim_is_accepted_as_a_list(keypair, fetch_calls) -> None:
    """Entra ID emits `scp`, not `scope` — and sometimes as a list."""
    verifier = make_verifier(required_scopes=["mcp:read"])
    token = make_token(keypair, scope=None, scp=["mcp:read"])
    assert await verifier.verify_token(token) is not None


async def test_algorithm_confusion_is_rejected(keypair, jwks, fetch_calls) -> None:
    """Criterion 17: HS256 signed with the public key the attacker can read.

    PyJWT's `encode()` refuses to sign with an asymmetric key as an HMAC
    secret (`InvalidKeyError`) — long-standing `HMACAlgorithm.prepare_key`
    behavior, not a version-specific change, and itself an algorithm-confusion
    defense. A real attacker forging this token by hand would never go
    through PyJWT's encoder anyway, so the JWS is built manually here to
    still exercise the actual attack our verifier's algorithm allowlist has
    to stop.
    """
    public_pem = keypair.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    forged = _forge_hs256_token(
        {"iss": ISSUER, "aud": AUDIENCE, "sub": "attacker", "exp": int(time.time()) + 600},
        secret=public_pem,
    )
    assert await make_verifier().verify_token(forged) is None


async def test_alg_none_is_rejected(fetch_calls) -> None:
    """Criterion 17: an unsigned token must never satisfy the allowlist."""
    forged = jwt.encode(
        {"iss": ISSUER, "aud": AUDIENCE, "sub": "attacker", "exp": int(time.time()) + 600},
        key="",
        algorithm="none",
        headers={"kid": KID},
    )
    assert await make_verifier().verify_token(forged) is None


async def test_jwks_is_fetched_once_for_two_verifications(keypair, fetch_calls) -> None:
    verifier = make_verifier()
    await verifier.verify_token(make_token(keypair))
    await verifier.verify_token(make_token(keypair))
    assert len(fetch_calls) == 1


@respx.mock
async def test_jwks_uri_is_discovered_when_not_configured(keypair, fetch_calls) -> None:
    """Without an explicit JWKS URI the issuer's OIDC document supplies one."""
    respx.get(f"{ISSUER}/.well-known/openid-configuration").mock(
        return_value=httpx.Response(200, json={"jwks_uri": JWKS_URI})
    )
    verifier = make_verifier(jwks_uri=None)
    assert await verifier.verify_token(make_token(keypair)) is not None
    assert fetch_calls == [JWKS_URI]


async def test_discovery_runs_outside_the_lock(keypair, fetch_calls, monkeypatch: pytest.MonkeyPatch) -> None:
    """#39: a slow OIDC discovery must not serialise every verification behind it.

    The lock exists to publish the client exactly once, not to hold the whole
    network round trip. The discovery request is gated on an event so the
    call is observably stuck inside it, and the lock must be free at that
    moment (D5). Reaching into `_lock` is deliberate: the alternative — timing
    a second caller — measures the scheduler, not the code.
    """
    gate = asyncio.Event()

    async def gated_discovery(request: httpx.Request) -> httpx.Response:
        await gate.wait()
        return httpx.Response(200, json={"jwks_uri": JWKS_URI})

    real_async_client = httpx.AsyncClient

    def client_with_gated_transport(**kwargs: Any) -> httpx.AsyncClient:
        return real_async_client(transport=httpx.MockTransport(gated_discovery), **kwargs)

    monkeypatch.setattr(jwt_verifier_module.httpx, "AsyncClient", client_with_gated_transport)
    verifier = make_verifier(jwks_uri=None)

    in_flight = asyncio.create_task(verifier.verify_token(make_token(keypair)))
    for _ in range(10):  # let the task run up to the gated `await`
        await asyncio.sleep(0)
    assert not in_flight.done()

    assert verifier._lock.locked() is False

    gate.set()
    assert await in_flight is not None
    assert fetch_calls == [JWKS_URI]


@respx.mock
async def test_unreachable_discovery_rejects_instead_of_degrading(keypair) -> None:
    """Authentication degrades into a refusal, never into a bypass."""
    respx.get(f"{ISSUER}/.well-known/openid-configuration").mock(side_effect=httpx.ConnectError("down"))
    assert await make_verifier(jwks_uri=None).verify_token(make_token(keypair)) is None


@respx.mock
async def test_malformed_discovery_document_is_rejected(keypair) -> None:
    """A discovery document that is valid JSON but lacks `jwks_uri` raises
    `KeyError` inside `_discover_jwks_uri` — must reject, not propagate."""
    respx.get(f"{ISSUER}/.well-known/openid-configuration").mock(return_value=httpx.Response(200, json={}))
    assert await make_verifier(jwks_uri=None).verify_token(make_token(keypair)) is None


async def test_non_json_jwks_response_is_rejected(keypair, monkeypatch: pytest.MonkeyPatch) -> None:
    """`PyJWKClient.fetch_data` only wraps URLError/TimeoutError; a JWKS
    endpoint returning a non-JSON body leaks a raw `json.JSONDecodeError`
    (a `ValueError` subclass) that must be turned into a rejection."""

    def broken_fetch(self: jwt.PyJWKClient) -> Any:
        raise json.JSONDecodeError("Expecting value", "<html>not json</html>", 0)

    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", broken_fetch)
    assert await make_verifier().verify_token(make_token(keypair)) is None


@respx.mock
async def test_discovered_http_jwks_uri_is_rejected(keypair, fetch_calls) -> None:
    """A discovery document must not be trusted to point key fetching at
    plaintext HTTP, even though the document itself arrived over HTTPS.

    `fetch_calls` fakes `PyJWKClient.fetch_data` to succeed with a valid key
    set — without the scheme check, verification would go on to succeed,
    so this genuinely fails red before the fix rather than passing by
    accident because of a network error to a fake host.
    """
    respx.get(f"{ISSUER}/.well-known/openid-configuration").mock(
        return_value=httpx.Response(200, json={"jwks_uri": "http://login.example.com/tenant/discovery/keys"})
    )
    assert await make_verifier(jwks_uri=None).verify_token(make_token(keypair)) is None
    assert fetch_calls == []


async def test_token_validation_failure_logs_at_info(keypair, fetch_calls, caplog: pytest.LogCaptureFixture) -> None:
    """A routine rejection (expired token) is everyday traffic, not an
    operational problem — it must stay at INFO."""
    with caplog.at_level(logging.INFO, logger="law_scrapper_mcp.auth.jwt_verifier"):
        result = await make_verifier().verify_token(make_token(keypair, exp=int(time.time()) - 10))
    assert result is None
    assert any(record.levelno == logging.INFO for record in caplog.records)
    assert not any(record.levelno >= logging.WARNING for record in caplog.records)


async def test_jwks_communication_failure_logs_at_warning(
    keypair, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A malformed/unreachable JWKS response is an infrastructure problem
    worth a human noticing, so it must log at WARNING, not INFO."""

    def broken_fetch(self: jwt.PyJWKClient) -> Any:
        raise json.JSONDecodeError("Expecting value", "<html>not json</html>", 0)

    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", broken_fetch)
    with caplog.at_level(logging.INFO, logger="law_scrapper_mcp.auth.jwt_verifier"):
        result = await make_verifier().verify_token(make_token(keypair))
    assert result is None
    assert any(record.levelno == logging.WARNING for record in caplog.records)
    assert not any(record.levelno == logging.INFO for record in caplog.records)


async def test_jwks_connection_failure_logs_at_warning(
    keypair, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """`PyJWKClientConnectionError` is a `PyJWTError` subclass (DNS failure,
    timeout, TLS handshake failure fetching the key set) — it must not be
    swallowed by the `except jwt.PyJWTError` clause meant for ordinary
    token-validation failures, or it silently logs at INFO instead of WARNING."""

    def broken_fetch(self: jwt.PyJWKClient) -> Any:
        raise PyJWKClientConnectionError("boom")

    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", broken_fetch)
    with caplog.at_level(logging.INFO, logger="law_scrapper_mcp.auth.jwt_verifier"):
        result = await make_verifier().verify_token(make_token(keypair))
    assert result is None
    assert any(record.levelno == logging.WARNING for record in caplog.records)
    assert not any(record.levelno == logging.INFO for record in caplog.records)


def _jwk_for(key: rsa.RSAPrivateKey, kid: str) -> dict[str, Any]:
    jwk = json.loads(RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update({"kid": kid, "use": "sig", "alg": "RS256"})
    return jwk


def _token_signed_by(key: rsa.RSAPrivateKey, kid: str) -> str:
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "user-42",
        "exp": int(time.time()) + 600,
        "iat": int(time.time()),
    }
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


async def test_retired_signing_key_stops_being_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """A key the issuer withdrew must not survive in a process-lifetime cache.

    PyJWT's second cache tier (`cache_keys=True`) is an `lru_cache` over
    individual signing keys with no expiry, so once a `kid` had been resolved it
    would be honoured until restart. Revoking a compromised key at the identity
    provider is the only revocation channel a resource server has, so that cache
    would quietly disable it. This test rotates the published key set and
    demands the withdrawn key be refused.
    """
    retired = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    current = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    published = {"keys": [_jwk_for(retired, "kid-old")]}

    def fake_fetch(self: jwt.PyJWKClient) -> dict[str, Any]:
        if self.jwk_set_cache is not None:
            self.jwk_set_cache.put(published)
        return published

    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", fake_fetch)
    verifier = make_verifier()

    assert await verifier.verify_token(_token_signed_by(retired, "kid-old")) is not None

    # The issuer rotates: only the new key is published from now on.
    published = {"keys": [_jwk_for(current, "kid-new")]}
    # Stand in for LAW_MCP_AUTH_JWKS_CACHE_TTL elapsing, so the key set is read
    # afresh. Tier 1 is time-bounded and that bound is the documented contract;
    # tier 2 would have no bound to elapse, which is the point of this test.
    client = await verifier._jwk_client()
    client.jwk_set_cache = None

    assert await verifier.verify_token(_token_signed_by(current, "kid-new")) is not None
    assert await verifier.verify_token(_token_signed_by(retired, "kid-old")) is None


async def test_per_key_cache_tier_stays_disabled(monkeypatch: pytest.MonkeyPatch, jwks: dict[str, Any]) -> None:
    """Structural guard on the same defect, independent of key material.

    `cache_keys=True` swaps `get_signing_key` for an `lru_cache` wrapper, which
    is recognisable by the `cache_info` attribute the decorator adds.
    """
    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", lambda _self: jwks)
    client = await make_verifier()._jwk_client()

    assert not hasattr(client.get_signing_key, "cache_info")


async def test_unknown_kid_does_not_leak_into_the_warning_log(
    keypair, fetch_calls, caplog: pytest.LogCaptureFixture
) -> None:
    """`PyJWKClientError`'s message embeds the token's own unverified `kid`
    header (PyJWT reads it before any signature check), so it must never be
    logged verbatim — the WARNING branch has to stay type-name-only for this
    exception class specifically, unlike JWKS/discovery transport failures."""
    sentinel_kid = "attacker-controlled-marker-xyz123"
    forged = jwt.encode(
        {"iss": ISSUER, "aud": AUDIENCE, "sub": "user-42", "exp": int(time.time()) + 600},
        keypair,
        algorithm="RS256",
        headers={"kid": sentinel_kid},
    )
    with caplog.at_level(logging.INFO, logger="law_scrapper_mcp.auth.jwt_verifier"):
        result = await make_verifier().verify_token(forged)
    assert result is None
    assert any(record.levelno == logging.WARNING for record in caplog.records)
    for record in caplog.records:
        assert sentinel_kid not in record.getMessage()

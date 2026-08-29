"""The bearer mode: one shared secret, compared in constant time."""

from __future__ import annotations

from pathlib import Path

import pytest

from law_scrapper_mcp.auth.static_token import STATIC_CLIENT_ID, StaticTokenVerifier

TOKEN = "s" * 32

pytestmark = pytest.mark.asyncio


async def test_correct_token_is_accepted() -> None:
    verifier = StaticTokenVerifier(token=TOKEN, scopes=[])
    access = await verifier.verify_token(TOKEN)
    assert access is not None
    assert access.client_id == STATIC_CLIENT_ID


async def test_wrong_token_is_rejected() -> None:
    verifier = StaticTokenVerifier(token=TOKEN, scopes=[])
    assert await verifier.verify_token("t" * 32) is None


async def test_empty_token_is_rejected() -> None:
    """An empty Authorization value must never satisfy the comparison."""
    verifier = StaticTokenVerifier(token=TOKEN, scopes=[])
    assert await verifier.verify_token("") is None


async def test_prefix_of_the_token_is_rejected() -> None:
    verifier = StaticTokenVerifier(token=TOKEN, scopes=[])
    assert await verifier.verify_token(TOKEN[:-1]) is None


async def test_configured_scopes_are_returned() -> None:
    """RequireAuthMiddleware enforces scopes against exactly this list."""
    verifier = StaticTokenVerifier(token=TOKEN, scopes=["mcp:read"])
    access = await verifier.verify_token(TOKEN)
    assert access is not None
    assert access.scopes == ["mcp:read"]


async def test_token_from_file_loses_its_trailing_newline(tmp_path: Path) -> None:
    """A secret file written by Docker or Kubernetes ends with a newline."""
    from law_scrapper_mcp.config import Settings

    token_file = tmp_path / "token"
    token_file.write_text(f"{TOKEN}\n", encoding="utf-8")
    current = Settings(auth_mode="bearer", auth_token_file=token_file)
    verifier = StaticTokenVerifier(token=current.resolve_auth_token(), scopes=[])
    assert await verifier.verify_token(TOKEN) is not None

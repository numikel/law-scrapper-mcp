"""Deployment files must not reintroduce an unauthenticated exposure."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[2]


def test_dockerfile_declares_no_host() -> None:
    """Criterion 18 (D19): exposure is configured in one place, not two."""
    assert "LAW_MCP_HOST" not in (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")


def test_compose_requires_auth_token() -> None:
    """Criterion 12 (D10): the Docker path is authenticated by construction."""
    compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "LAW_MCP_AUTH_MODE=bearer" in compose
    assert "${LAW_MCP_AUTH_TOKEN:?" in compose


def test_readme_documents_token_generation() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    assert "openssl rand -base64 32" in readme
    assert "LAW_MCP_AUTH_MODE" in readme

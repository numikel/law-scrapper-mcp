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


def test_dockerfile_builder_copies_every_file_the_package_metadata_needs() -> None:
    """`uv sync --no-editable` builds the project wheel inside the builder stage.

    hatchling reads every file `pyproject.toml` names in its metadata (the readme
    today), so each of them must be in the build context before `uv sync` runs.
    Adding `readme = "README.md"` without copying the file broke every image build
    from 2026-08-10 until the Glama indexer reported it.
    """
    import tomllib

    metadata = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    required = {metadata["readme"]} if isinstance(metadata.get("readme"), str) else set()
    license_file = metadata.get("license")
    if isinstance(license_file, dict) and "file" in license_file:
        required.add(license_file["file"])
    assert required, "the test expects at least the readme to be declared"

    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
    builder_stage = dockerfile.split("\nFROM ", 1)[0]
    copied_before_sync: set[str] = set()
    for line in builder_stage.splitlines():
        if line.startswith("RUN uv sync"):
            break
        if line.startswith("COPY ") and "--from=" not in line:
            copied_before_sync.update(line.split()[1:-1])
    missing = required - copied_before_sync
    assert not missing, f"builder stage runs `uv sync` without {sorted(missing)} in the build context"

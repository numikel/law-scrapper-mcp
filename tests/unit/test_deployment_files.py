"""Deployment files must not reintroduce an unauthenticated exposure, and the image ships every module off."""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[2]

sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
import check_release  # noqa: E402

DOCKERFILE = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
DOCKERIGNORE = [line.strip() for line in (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()]


def _sync_line() -> str:
    lines = [line for line in DOCKERFILE.splitlines() if line.startswith("RUN uv sync")]
    assert len(lines) == 1, "the builder stage has exactly one `RUN uv sync`"
    return lines[0]


def _distribution(module_dir: str) -> str:
    data = tomllib.loads((PROJECT_ROOT / "modules" / module_dir / "pyproject.toml").read_text(encoding="utf-8"))
    name: str = data["project"]["name"]
    return name


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


def test_compose_shows_the_contact_setting_only_as_a_comment() -> None:
    """FR-016: the Docker path always binds beyond loopback, so the setting must be discoverable there.

    It stays a comment: an active but empty or invalid `LAW_MCP_CONTACT` stops startup, and a
    required variable would break every existing deployment on upgrade.
    """
    compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    lines = [line.strip() for line in compose.splitlines()]

    assert any(line.startswith("#") and "LAW_MCP_CONTACT" in line for line in lines)
    # Any active spelling counts: `- LAW_MCP_CONTACT=x`, `- "LAW_MCP_CONTACT=x"` and `LAW_MCP_CONTACT: x`.
    assert [line for line in lines if "LAW_MCP_CONTACT" in line and not line.startswith("#")] == []


def test_readme_documents_the_contact_setting() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert "| `LAW_MCP_CONTACT` |" in readme, "the settings table has a row for it"
    assert "### Identity towards upstream hosts" in readme
    assert "mailto:" in readme, "the section shows how an e-mail is written into the header"


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

    # Every released module is built in the same `uv sync`, so its readme must be there too.
    for module in check_release.released_modules():
        member = tomllib.loads(module.pyproject.read_text(encoding="utf-8"))["project"]
        if isinstance(member.get("readme"), str):
            required.add(f"modules/{module.directory}/{member['readme']}")

    builder_stage = DOCKERFILE.split("\nFROM ", 1)[0]
    copied_before_sync: set[str] = set()
    for line in builder_stage.splitlines():
        if line.startswith("RUN uv sync"):
            break
        if line.startswith("COPY ") and "--from=" not in line:
            copied_before_sync.update(line.split()[1:-1])

    def copied(path: str) -> bool:
        return any(path == item or (item.endswith("/") and path.startswith(item)) for item in copied_before_sync)

    missing = {path for path in required if not copied(path)}
    assert not missing, f"builder stage runs `uv sync` without {sorted(missing)} in the build context"


# --- source modules in the image ---------------------------------------------------------------


def test_the_image_bakes_every_module_off() -> None:
    """FR-002: in the official image every module is installed and inactive until the operator enables it."""
    final_stage = DOCKERFILE.rsplit("\nFROM ", 1)[1]

    assert "ENV LAW_MCP_SOURCE_MODULES=-*" in [line.strip() for line in final_stage.splitlines()]


def test_the_image_installs_every_member_but_the_never_released_ones() -> None:
    sync = _sync_line().split()

    assert "--all-packages" in sync
    excluded = {sync[i + 1] for i, token in enumerate(sync) if token == "--no-install-package"}
    assert excluded == {_distribution(directory) for directory in check_release.NEVER_RELEASED_MODULES}


def test_the_build_context_keeps_never_released_modules_and_module_tests_out() -> None:
    assert "modules/*/tests/" in DOCKERIGNORE
    # The last matching rule wins: the README re-include comes after `*.md`, and every never-released
    # member is excluded after the re-include, or its README alone would leave a member directory
    # without pyproject.toml and `uv sync --all-packages` would fail.
    readme = DOCKERIGNORE.index("!modules/*/README.md")
    assert readme > DOCKERIGNORE.index("*.md")
    for directory in check_release.NEVER_RELEASED_MODULES:
        assert f"modules/{directory}/" in DOCKERIGNORE, directory
        assert DOCKERIGNORE.index(f"modules/{directory}/") > readme, directory


def test_every_shown_source_modules_value_starts_with_every_module_off() -> None:
    """A bare `<name>` replaces the image's `-*` and turns every other installed module on (F-03 owner decision)."""
    shown: list[tuple[str, str]] = []
    for name in ("Dockerfile", "docker-compose.yml", "README.md"):
        text = (PROJECT_ROOT / name).read_text(encoding="utf-8")
        shown += [(name, value) for value in re.findall(r"LAW_MCP_SOURCE_MODULES=([^\s`'\"]+)", text)]

    assert ("Dockerfile", "-*") in shown
    assert [item for item in shown if not item[1].startswith("-*")] == []

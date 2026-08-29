"""Dependency policy tests for the staged MCP migration."""

import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[2]


def test_only_official_mcp_sdk_is_a_runtime_dependency() -> None:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as pyproject:
        dependencies = tomllib.load(pyproject)["project"]["dependencies"]

    legacy_framework = "fast" + "mcp"
    assert not any(dependency.lower().startswith(legacy_framework) for dependency in dependencies)
    official = [dependency for dependency in dependencies if dependency.lower().startswith("mcp[cli]==")]
    assert len(official) == 1


def test_jwt_library_is_a_direct_dependency() -> None:
    """Relying on `mcp`'s transitive PyJWT would be a silent dependency on
    someone else's tree — the JWT verifier imports it directly."""
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as pyproject:
        dependencies = tomllib.load(pyproject)["project"]["dependencies"]

    assert any(dependency.lower().startswith("pyjwt") for dependency in dependencies)
    assert not any(dependency.lower().startswith("joserfc") for dependency in dependencies)

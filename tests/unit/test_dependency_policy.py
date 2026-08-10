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

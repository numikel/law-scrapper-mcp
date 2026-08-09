"""Dependency policy tests for the staged MCP migration."""
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[2]


def test_fastmcp_baseline_is_pinned_exactly() -> None:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as pyproject:
        dependencies = tomllib.load(pyproject)["project"]["dependencies"]

    assert "fastmcp==3.4.3" in dependencies

"""Module code gets the same gates as core code: CI, the local mirror and the filter that starts them.

Reads `.github/workflows/ci.yml` as text (no YAML dependency) and runs the local mirror against a
recording `run_command`, so a gate dropped from either place fails a named test. What this cannot
prove is that GitHub runs the jobs; that stays a CI observation.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CI = REPO / ".github" / "workflows" / "ci.yml"

sys.path.insert(0, str(REPO / "scripts"))
import check_release  # noqa: E402

CORE_ONLY_SYNC = "uv sync --extra dev"
ALL_PACKAGES_SYNC = "uv sync --extra dev --all-packages"
JOB_HEADER = re.compile(r"^  ([a-z][a-z0-9-]*):\s*$")
FULL_SHA = re.compile(r"[\w./-]+@[0-9a-f]{40}")


def job_block(text: str, name: str) -> str:
    """The lines of one top-level job: from its `  name:` header to the next job header."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if JOB_HEADER.match(line) and line.strip() == f"{name}:"), None)
    assert start is not None, f"ci.yml has no job named {name}"
    end = next((i for i in range(start + 1, len(lines)) if JOB_HEADER.match(lines[i])), len(lines))
    return "\n".join(lines[start:end])


def run_lines(block: str) -> list[str]:
    return [m[1].strip() for line in block.splitlines() if (m := re.match(r"\s*run:\s*(.+)$", line)) and m[1] != "|"]


def code_filter(text: str) -> list[str]:
    """The patterns under `filters: code:` of the `changes` job."""
    lines = job_block(text, "changes").splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "code:")
    patterns: list[str] = []
    for line in lines[start + 1 :]:
        match = re.match(r"\s*- '(.+)'\s*$", line)
        if not match:
            break
        patterns.append(match[1])
    return patterns


def filter_matches(path: str, patterns: list[str]) -> bool:
    """The subset of the paths-filter glob syntax this file uses: `dir/**` or an exact path."""
    return any(path.startswith(p[:-2]) if p.endswith("/**") else path == p for p in patterns)


CI_TEXT = CI.read_text(encoding="utf-8")


# --- the filter that starts the jobs -----------------------------------------------------------


def test_a_change_that_touches_only_modules_starts_the_code_jobs() -> None:
    patterns = code_filter(CI_TEXT)

    assert filter_matches("modules/reference/src/law_scrapper_mcp_reference/state.py", patterns)
    assert filter_matches("modules/reference/tests/test_reference_tool.py", patterns)
    assert filter_matches("modules/reference/pyproject.toml", patterns)


def test_the_filter_still_covers_what_it_covered_before_modules() -> None:
    patterns = code_filter(CI_TEXT)

    for path in (
        "src/law_scrapper_mcp/server.py",
        "tests/unit/test_server.py",
        "pyproject.toml",
        "uv.lock",
        "Dockerfile",
    ):
        assert filter_matches(path, patterns), path


def test_the_filter_helpers_can_actually_fail() -> None:
    assert not filter_matches("modules/reference/README.md", ["src/**", "tests/**"])
    assert not filter_matches("docs/source-modules.md", code_filter(CI_TEXT))
    with pytest.raises(AssertionError, match="no job named"):
        job_block(CI_TEXT, "no-such-job")


# --- the jobs ----------------------------------------------------------------------------------


def test_lint_runs_ruff_and_mypy_over_modules_with_the_members_installed() -> None:
    commands = run_lines(job_block(CI_TEXT, "lint"))

    assert ALL_PACKAGES_SYNC in commands
    assert "uv run ruff check src tests modules" in commands
    assert "uv run ruff format --check src tests modules" in commands
    assert "uv run mypy src/law_scrapper_mcp/ modules/*/src/law_scrapper_mcp_*/" in commands


def test_the_test_job_stays_core_only() -> None:
    """The conformance server and the transport tests run there; no member may be installed."""
    block = job_block(CI_TEXT, "test")

    assert CORE_ONLY_SYNC in run_lines(block)
    assert "--all-packages" not in block


def test_the_modules_job_installs_the_members_and_runs_their_tests() -> None:
    block = job_block(CI_TEXT, "modules")
    commands = run_lines(block)

    assert "needs: changes" in block
    assert ALL_PACKAGES_SYNC in commands
    assert "uv run pytest modules/*/tests -v" in commands
    assert "uv run pytest tests/unit/ -q" in commands, "the root suite must be shown to stay core-only"


def test_every_action_is_pinned_to_a_full_commit_sha() -> None:
    uses = re.findall(r"^\s*(?:- )?uses:\s*(\S+)", CI_TEXT, flags=re.MULTILINE)

    assert len(uses) > 10
    assert [item for item in uses if not FULL_SHA.fullmatch(item)] == []


# --- the local mirror --------------------------------------------------------------------------


def recorded_commands(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    recorded: list[list[str]] = []
    monkeypatch.setattr(check_release, "run_command", lambda cmd, _desc: recorded.append(list(cmd)) or True)
    assert check_release.run_checks_and_tests() is True
    return recorded


def test_the_local_mirror_covers_modules_like_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    commands = recorded_commands(monkeypatch)

    assert ["uv", "run", "ruff", "check", "src", "tests", "modules"] in commands
    assert ["uv", "run", "ruff", "format", "--check", "src", "tests", "modules"] in commands
    mypy = next(cmd for cmd in commands if cmd[:3] == ["uv", "run", "mypy"])
    assert mypy[3] == "src/law_scrapper_mcp/"
    assert "modules/reference/src/law_scrapper_mcp_reference" in mypy[4:]
    module_tests = next(cmd for cmd in commands if "--all-packages" in cmd)
    assert module_tests[:5] == ["uv", "run", "--all-packages", "pytest", "modules/reference/tests"]


def test_the_local_mirror_runs_the_core_suites_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    commands = recorded_commands(monkeypatch)

    assert ["uv", "run", "pytest", "tests/unit/"] in commands
    assert ["uv", "run", "pytest", "tests/integration/", "-m", "integration"] in commands


def test_the_local_mirror_skips_the_module_test_command_when_there_is_no_module(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(check_release, "PROJECT_ROOT", tmp_path)

    commands = recorded_commands(monkeypatch)

    assert not any("--all-packages" in cmd for cmd in commands)
    assert next(cmd for cmd in commands if cmd[:3] == ["uv", "run", "mypy"]) == [
        "uv",
        "run",
        "mypy",
        "src/law_scrapper_mcp/",
    ]

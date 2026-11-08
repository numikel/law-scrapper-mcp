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
RELEASE = REPO / ".github" / "workflows" / "release.yml"
DOCKERFILE = REPO / "Dockerfile"

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
RELEASE_TEXT = RELEASE.read_text(encoding="utf-8")


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


def step_block(job: str, name: str) -> str:
    """The lines of one step: from its `- name:` line to the next step of the same job."""
    lines = job.splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == f"- name: {name}"), None)
    assert start is not None, f"no step named {name}"
    end = next((i for i in range(start + 1, len(lines)) if lines[i].strip().startswith("- name:")), len(lines))
    return "\n".join(lines[start:end])


CONFORMANCE_CONTRACT = (
    'CONFORMANCE_PKG="@modelcontextprotocol/conformance@0.2.0-alpha.11"',
    "--url http://127.0.0.1:7683/mcp",
    '--spec-version "$SDK_SPEC_VERSION"',
    "--scenario tools-list",
    "--expected-failures ./conformance-baseline.yml",
    "LAW_MCP_RATE_LIMIT_ENABLED=false",
    "LAW_MCP_TRANSPORT=streamable-http",
    'grep -q "Unknown spec version"',
    'exit "$PROBE_STATUS"',
)


def test_the_modules_job_runs_conformance_with_the_module_active() -> None:
    core = step_block(job_block(CI_TEXT, "test"), "MCP conformance")
    with_module = step_block(job_block(CI_TEXT, "modules"), "MCP conformance with a module active")

    assert "LAW_MCP_SOURCE_MODULES='-*,voivodeship-journals'" in with_module
    assert "uv run python -m law_scrapper_mcp" in with_module
    assert '"name":"POL_WOJ_PM"' in with_module, "the step must prove the module is active, not assume it"
    for piece in CONFORMANCE_CONTRACT:
        assert piece in core, f"the core conformance step lost {piece}"
        assert piece in with_module, f"the module conformance step is narrower than the core one: {piece}"


def test_the_core_conformance_step_stays_core_only() -> None:
    core = step_block(job_block(CI_TEXT, "test"), "MCP conformance")

    assert "LAW_MCP_SOURCE_MODULES" not in core
    assert "--all-packages" not in job_block(CI_TEXT, "test")


@pytest.mark.parametrize(("workflow", "minimum"), [(CI_TEXT, 10), (RELEASE_TEXT, 5)], ids=["ci.yml", "release.yml"])
def test_every_action_is_pinned_to_a_full_commit_sha(workflow: str, minimum: int) -> None:
    uses = re.findall(r"^\s*(?:- )?uses:\s*(\S+)", workflow, flags=re.MULTILINE)

    assert len(uses) >= minimum
    assert [item for item in uses if not FULL_SHA.fullmatch(item)] == []


# --- release packages --------------------------------------------------------------------------

BUILD_PACKAGES = "uv run --no-project python scripts/check_release.py --build-packages dist"


def setup_uv_versions(workflow: str) -> list[str | None]:
    """For every setup-uv step, the `version:` it pins (None when the step pins none)."""
    lines = workflow.splitlines()
    versions: list[str | None] = []
    for index, line in enumerate(lines):
        if "uses: astral-sh/setup-uv@" not in line:
            continue
        found: str | None = None
        for following in lines[index + 1 :]:
            if following.strip().startswith("- ") or JOB_HEADER.match(following):
                break
            if match := re.match(r'\s*version:\s*"([^"]+)"\s*$', following):
                found = match[1]
        versions.append(found)
    return versions


def test_every_setup_uv_step_pins_the_uv_of_the_dockerfile() -> None:
    dockerfile = re.search(r"ghcr\.io/astral-sh/uv:([0-9.]+)", DOCKERFILE.read_text(encoding="utf-8"))
    assert dockerfile is not None, "the Dockerfile no longer pins uv by tag"

    for name, workflow in (("ci.yml", CI_TEXT), ("release.yml", RELEASE_TEXT)):
        versions = setup_uv_versions(workflow)
        assert versions, f"{name} has no setup-uv step"
        assert versions == [dockerfile[1]] * len(versions), name


def test_the_setup_uv_helper_sees_a_missing_pin() -> None:
    unpinned = (
        "      - name: Install uv\n        uses: astral-sh/setup-uv@abc\n        with:\n          enable-cache: true\n"
    )

    assert setup_uv_versions(unpinned) == [None]


def test_the_packages_job_builds_installs_and_checks_the_release_on_every_pr() -> None:
    block = job_block(CI_TEXT, "packages")
    commands = run_lines(block)
    install = step_block(block, "Install the wheels into a clean environment")

    assert "needs: changes" in block
    assert "uv lock --locked" in commands
    assert BUILD_PACKAGES in commands
    assert "uv pip install" in install and "dist/*.whl" in install
    assert "scripts/check_release.py --check-installed" in install
    assert "--all-packages" not in install, "the clean environment gets the built wheels, not the workspace"


def test_the_release_workflow_validates_and_builds_every_released_package() -> None:
    validate = run_lines(job_block(RELEASE_TEXT, "validate"))
    build = run_lines(job_block(RELEASE_TEXT, "build"))

    assert 'python3 scripts/check_release.py --version "$VERSION" --skip-tests' in validate
    assert BUILD_PACKAGES in build
    assert "uv build" not in build, "a bare uv build releases the core only"
    assert "files: |" in job_block(RELEASE_TEXT, "release") and "dist/*" in job_block(RELEASE_TEXT, "release")


def test_the_audit_covers_module_dependencies() -> None:
    assert "uv run --extra dev --all-packages pip-audit" in run_lines(job_block(CI_TEXT, "audit"))


def test_a_change_to_the_release_scripts_or_workflow_starts_the_code_jobs() -> None:
    patterns = code_filter(CI_TEXT)

    assert filter_matches("scripts/check_release.py", patterns)
    assert filter_matches(".github/workflows/release.yml", patterns)


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


def test_the_local_mirror_builds_and_inspects_the_packages_like_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    commands = recorded_commands(monkeypatch)

    build = [cmd for cmd in commands if "--build-packages" in cmd]
    assert len(build) == 1
    assert build[0][1].endswith("check_release.py")
    assert not Path(build[0][-1]).exists(), "the throwaway build directory is removed afterwards"


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

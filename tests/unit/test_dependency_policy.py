"""Dependency policy tests for the staged MCP migration."""

import re
import tomllib
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parents[2]

# Floors below which a locked package carries an advisory `pip-audit` reports. Each one is held by
# a `[tool.uv] override-dependencies` entry, because the package is only a transitive dependency
# and its parent asks for far less. A floor only ever moves up.
SECURITY_FLOORS = {
    # GHSA-mf5v-w3w3-f6cg: quadratic `traceparent` parsing, reachable through `params._meta`.
    "opentelemetry-api": "1.45.2",
    # GHSA-mmhr-3c5m-f64f (fixed in 4.0), GHSA-7g44-g9rc-x53w and GHSA-6655-6qjp-jx7x (3.0.2).
    "soupsieve": "4.0",
}


def _release(version: str) -> tuple[int, ...]:
    """`1.45.2` -> `(1, 45, 2, 0)`: numeric release segments, padded so `4.0` equals `4.0.0`."""
    parts = [int(part) for part in re.findall(r"\d+", version)]
    return tuple(parts + [0] * (4 - len(parts)))


def _pyproject() -> dict[str, object]:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as pyproject:
        return tomllib.load(pyproject)


def _locked_versions() -> dict[str, str]:
    with (PROJECT_ROOT / "uv.lock").open("rb") as lock:
        return {package["name"]: package["version"] for package in tomllib.load(lock)["package"]}


def _sdk_pin() -> str:
    dependencies = _pyproject()["project"]["dependencies"]  # type: ignore[index]
    (pin,) = [dependency for dependency in dependencies if dependency.lower().startswith("mcp[cli]==")]
    return pin.split("==", 1)[1]


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


def test_uvicorn_is_a_direct_dependency() -> None:
    """`server.py` imports uvicorn at module level and owns the HTTP bootstrap,
    so it must not ride on `mcp[cli]`'s transitive pin — a future SDK release
    dropping or loosening it would break the server without any diff here."""
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as pyproject:
        dependencies = tomllib.load(pyproject)["project"]["dependencies"]

    assert any(dependency.lower().startswith("uvicorn>=") for dependency in dependencies)


@pytest.mark.parametrize("package", sorted(SECURITY_FLOORS))
def test_security_floor_is_an_override_and_the_lock_honours_it(package: str) -> None:
    """A relock must not fall back under a fixed advisory, and the floor must be written down
    in `pyproject.toml`: a lock that merely happens to be new enough is one `uv lock` from regressing."""
    floor = _release(SECURITY_FLOORS[package])
    overrides = _pyproject()["tool"]["uv"]["override-dependencies"]  # type: ignore[index]
    declared = [entry.split(">=", 1)[1] for entry in overrides if entry.lower().startswith(f"{package}>=")]

    assert len(declared) == 1, f"{package} needs exactly one `>=` override, found {declared}"
    assert _release(declared[0]) >= floor, f"override {package}>={declared[0]} is below {SECURITY_FLOORS[package]}"
    assert _release(_locked_versions()[package]) >= floor


def test_version_comparison_helper_orders_releases() -> None:
    """Positive control for `_release`: string order would put 1.45.10 under 1.45.2 and 4.0 under 4.0.0."""
    assert _release("1.45.10") > _release("1.45.2") > _release("1.45.1")
    assert _release("4.0") == _release("4.0.0") > _release("3.0.4") > _release("2.10")


def test_locked_sdk_is_the_pinned_one() -> None:
    """A pin bumped without `uv lock` installs the old SDK from the lock until CI's
    `uv lock --locked` refuses it; `mcp-types` is released in lockstep and pinned by `mcp` itself."""
    locked = _locked_versions()

    assert locked["mcp"] == _sdk_pin()
    assert locked["mcp-types"] == _sdk_pin()


@pytest.mark.parametrize("document", ["README.md", "CLAUDE.md"])
def test_documented_sdk_pin_matches_pyproject(document: str) -> None:
    """Both files say which SDK the server is "Built with"; a bump that skips them leaves a wrong
    version there. Only that sentence is read: the README also quotes the pin of older releases
    in its migration notes, and those must stay as they were."""
    text = (PROJECT_ROOT / document).read_text("utf-8")
    quoted = re.findall(r"Built with[^\n]*?`mcp\[cli\]==([^`]+)`", text)

    assert quoted == [_sdk_pin()], f"{document} says the server is built with mcp {quoted}"

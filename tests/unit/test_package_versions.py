"""Every released source module carries the core's version and pins the core to exactly it.

Inside the uv workspace the pin is ignored (the workspace source wins), so a forgotten bump passes
`uv lock` and every other test and fails only when an operator installs the wheels. These tests are
the guard on the repository side; the CI `packages` job installs the built wheels as the second one.
"""

from __future__ import annotations

import shutil
import sys
import tomllib
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import check_release  # noqa: E402

PROJECT_ROOT = check_release.PROJECT_ROOT


def _core_version() -> str:
    data = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version: str = data["project"]["version"]
    return version


# --- the release set ---------------------------------------------------------------------------


def test_reference_is_never_released_and_the_source_modules_are() -> None:
    released = {module.directory for module in check_release.released_modules()}

    assert "reference" not in released
    assert {"saos", "voivodeship-journals"} <= released


def test_every_never_released_name_is_a_real_module_directory() -> None:
    # A typo in the set would silently release the module it meant to hold back.
    for directory in check_release.NEVER_RELEASED_MODULES:
        assert (PROJECT_ROOT / "modules" / directory / "pyproject.toml").is_file(), directory


def test_every_module_directory_is_either_released_or_never_released() -> None:
    members = {path.parent.name for path in (PROJECT_ROOT / "modules").glob("*/pyproject.toml")}
    released = {module.directory for module in check_release.released_modules()}

    assert members == released | check_release.NEVER_RELEASED_MODULES
    assert not released & check_release.NEVER_RELEASED_MODULES


# --- the repository is in step -----------------------------------------------------------------


def test_every_released_module_has_the_core_version_and_pins_the_core_to_it() -> None:
    core_version = _core_version()

    for module in check_release.released_modules():
        project = tomllib.loads(module.pyproject.read_text(encoding="utf-8"))["project"]
        assert project["version"] == core_version, module.distribution
        assert check_release._core_requirements(project["dependencies"]) == [f"law-scrapper-mcp=={core_version}"]

    assert check_release.check_member_versions(core_version) is True


def test_the_core_requirement_is_told_apart_from_a_longer_name() -> None:
    dependencies = ["law-scrapper-mcp-saos==1.0.0", "law_scrapper_mcp==2.0.0", "httpx>=0.28"]

    assert check_release._core_requirements(dependencies) == ["law_scrapper_mcp==2.0.0"]


# --- each kind of drift fails, on a copy of the repository --------------------------------------


@pytest.fixture
def copied_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Module pyprojects and uv.lock copied to tmp_path, with the script pointed at the copy."""
    for pyproject in (PROJECT_ROOT / "modules").glob("*/pyproject.toml"):
        target = tmp_path / "modules" / pyproject.parent.name / "pyproject.toml"
        target.parent.mkdir(parents=True)
        shutil.copyfile(pyproject, target)
    shutil.copyfile(PROJECT_ROOT / "uv.lock", tmp_path / "uv.lock")

    rerooted = dict(check_release.VERSION_FILES)
    _, replace_pattern, search_pattern = rerooted["uv.lock"]
    rerooted["uv.lock"] = (tmp_path / "uv.lock", replace_pattern, search_pattern)
    monkeypatch.setattr(check_release, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(check_release, "VERSION_FILES", rerooted)
    return tmp_path


def _rewrite(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, f"{old!r} not in {path}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def test_the_copy_is_in_step_before_any_drift(copied_repo: Path) -> None:
    assert check_release.check_member_versions(_core_version()) is True


def test_a_module_version_behind_the_core_fails(copied_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    core_version = _core_version()
    _rewrite(copied_repo / "modules" / "saos" / "pyproject.toml", f'version = "{core_version}"', 'version = "0.1.0"')

    assert check_release.check_member_versions(core_version) is False
    assert "[FAIL] law-scrapper-mcp-saos: version 0.1.0" in capsys.readouterr().out


def test_a_pin_on_another_core_version_fails(copied_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    core_version = _core_version()
    _rewrite(
        copied_repo / "modules" / "saos" / "pyproject.toml",
        f'"law-scrapper-mcp=={core_version}"',
        '"law-scrapper-mcp==4.4.1"',
    )

    assert check_release.check_member_versions(core_version) is False
    assert "[FAIL] law-scrapper-mcp-saos: core requirement ['law-scrapper-mcp==4.4.1']" in capsys.readouterr().out


def test_an_unpinned_core_requirement_fails(copied_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    core_version = _core_version()
    _rewrite(
        copied_repo / "modules" / "saos" / "pyproject.toml", f'"law-scrapper-mcp=={core_version}"', '"law-scrapper-mcp"'
    )

    assert check_release.check_member_versions(core_version) is False
    assert "[FAIL] law-scrapper-mcp-saos: core requirement ['law-scrapper-mcp']" in capsys.readouterr().out


def test_a_stale_lock_entry_fails(copied_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    core_version = _core_version()
    _rewrite(
        copied_repo / "uv.lock",
        f'name = "law-scrapper-mcp-saos"\nversion = "{core_version}"',
        'name = "law-scrapper-mcp-saos"\nversion = "0.1.0"',
    )

    assert check_release.check_member_versions(core_version) is False
    assert "[FAIL] law-scrapper-mcp-saos: uv.lock version 0.1.0" in capsys.readouterr().out


# --- the core sdist ----------------------------------------------------------------------------


def test_the_core_sdist_is_an_allowlist() -> None:
    data = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    # Without it a local build packs gitignored private files and every workspace member.
    assert data["tool"]["hatch"]["build"]["targets"]["sdist"]["include"] == [
        "/src",
        "/README.md",
        "/LICENSE",
        "/CHANGELOG.md",
    ]

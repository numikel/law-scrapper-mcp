"""Every released source module carries the core's version and pins the core to exactly it.

Inside the uv workspace the pin is ignored (the workspace source wins), so a forgotten bump passes
`uv lock` and every other test and fails only when an operator installs the wheels. These tests are
the guard on the repository side; the CI `packages` job installs the built wheels as the second one.
"""

from __future__ import annotations

import io
import shutil
import sys
import tarfile
import tomllib
import zipfile
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


# --- inspection of built artefacts (synthetic files, no build) ---------------------------------

VERSION = "1.2.3"


def _wheel(path: Path, requires: list[str]) -> None:
    metadata = "Metadata-Version: 2.4\nName: x\n" + "".join(f"Requires-Dist: {item}\n" for item in requires)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("x-1.2.3.dist-info/METADATA", metadata)


def _sdist(path: Path, members: list[str]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for member in members:
            info = tarfile.TarInfo(member)
            info.size = 0
            archive.addfile(info, io.BytesIO(b""))


def _release(out_dir: Path) -> Path:
    """A correct release of VERSION: core plus every released module, sdist and wheel each."""
    out_dir.mkdir()
    for distribution in check_release.released_distributions():
        stem = check_release._file_stem(distribution)
        requires = [] if distribution == "law-scrapper-mcp" else [f"law-scrapper-mcp=={VERSION}", "httpx>=0.28"]
        _wheel(out_dir / f"{stem}-{VERSION}-py3-none-any.whl", requires)
        _sdist(out_dir / f"{stem}-{VERSION}.tar.gz", [f"{stem}-{VERSION}/src/{stem}/__init__.py"])
    (out_dir / ".gitignore").write_text("*\n", encoding="utf-8")  # uv build writes one; it is not released
    return out_dir


def test_a_correct_release_passes_inspection(tmp_path: Path) -> None:
    out_dir = _release(tmp_path / "dist")

    assert check_release.inspect_packages(out_dir, VERSION) == []
    assert len(check_release.expected_artefacts(VERSION)) == 2 * len(check_release.released_distributions())


def test_inspection_refuses_a_reference_artefact(tmp_path: Path) -> None:
    out_dir = _release(tmp_path / "dist")
    _wheel(out_dir / f"law_scrapper_mcp_reference-{VERSION}-py3-none-any.whl", [f"law-scrapper-mcp=={VERSION}"])

    failures = check_release.inspect_packages(out_dir, VERSION)

    assert f"law_scrapper_mcp_reference-{VERSION}-py3-none-any.whl: not part of the release" in failures
    assert f"law_scrapper_mcp_reference-{VERSION}-py3-none-any.whl: the reference module is never released" in failures


def test_inspection_refuses_a_missing_artefact(tmp_path: Path) -> None:
    out_dir = _release(tmp_path / "dist")
    (out_dir / f"law_scrapper_mcp_saos-{VERSION}.tar.gz").unlink()

    assert check_release.inspect_packages(out_dir, VERSION) == [f"law_scrapper_mcp_saos-{VERSION}.tar.gz: missing"]


def test_inspection_refuses_a_module_wheel_without_the_exact_core_pin(tmp_path: Path) -> None:
    out_dir = _release(tmp_path / "dist")
    _wheel(out_dir / f"law_scrapper_mcp_saos-{VERSION}-py3-none-any.whl", ["law-scrapper-mcp"])

    failures = check_release.inspect_packages(out_dir, VERSION)

    assert failures == [
        f"law_scrapper_mcp_saos-{VERSION}-py3-none-any.whl: Requires-Dist on the core is ['law-scrapper-mcp'], "
        f"expected ['law-scrapper-mcp=={VERSION}']"
    ]


@pytest.mark.parametrize("directory", check_release.SDIST_FORBIDDEN_DIRS)
def test_inspection_refuses_a_core_sdist_that_packs_files_outside_the_allowlist(tmp_path: Path, directory: str) -> None:
    out_dir = _release(tmp_path / "dist")
    member = f"law_scrapper_mcp-{VERSION}/{directory}/leak.txt"
    _sdist(out_dir / f"law_scrapper_mcp-{VERSION}.tar.gz", [f"law_scrapper_mcp-{VERSION}/src/x.py", member])

    assert check_release.inspect_packages(out_dir, VERSION) == [f"law_scrapper_mcp-{VERSION}.tar.gz: contains {member}"]


def test_a_build_into_a_directory_with_files_is_refused(tmp_path: Path) -> None:
    out_dir = _release(tmp_path / "dist")

    assert check_release.build_packages(out_dir) is False


# --- an environment the wheels were installed into ---------------------------------------------


def _installed_release() -> dict[str, str]:
    return {name: VERSION for name in check_release.released_distributions()}


def test_an_environment_with_exactly_the_release_passes() -> None:
    entry_points = {"saos", "voivodeship-journals"}

    assert check_release.compare_installed(_installed_release(), entry_points, VERSION) == []


def test_an_environment_with_the_reference_module_fails() -> None:
    installed = {**_installed_release(), "law-scrapper-mcp-reference": "0.0.0"}

    failures = check_release.compare_installed(installed, {"reference", "saos", "voivodeship-journals"}, VERSION)

    assert "law-scrapper-mcp-reference is installed but is not part of the release" in failures
    assert any(failure.startswith("entry points ['reference',") for failure in failures)


def test_an_environment_with_a_module_of_another_version_fails() -> None:
    installed = {**_installed_release(), "law-scrapper-mcp-saos": "1.2.2"}

    failures = check_release.compare_installed(installed, {"saos", "voivodeship-journals"}, VERSION)

    assert failures == [f"law-scrapper-mcp-saos 1.2.2 (expected {VERSION})"]


def test_an_environment_missing_a_module_fails() -> None:
    installed = _installed_release()
    del installed["law-scrapper-mcp-saos"]

    failures = check_release.compare_installed(installed, {"voivodeship-journals"}, VERSION)

    assert "law-scrapper-mcp-saos is not installed" in failures


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

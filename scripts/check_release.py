#!/usr/bin/env python3
"""Pre-release verification and version synchronization script.

Validates release readiness for law-scrapper-mcp by:
1. Synchronizing or checking version strings across project files, including every released
   source module's version and its pin on the core.
2. Checking existence and completeness of per-version changelogs.
3. Running linters, type checkers, and unit test suite via uv.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from dataclasses import dataclass
from pathlib import Path

# Project root directory
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Files containing the version number, with their update and search regex patterns
VERSION_FILES: dict[str, tuple[Path, str, str]] = {
    "pyproject.toml": (
        PROJECT_ROOT / "pyproject.toml",
        r'(^version\s*=\s*")[^"]+(")',
        r'^version\s*=\s*"([^"]+)"',
    ),
    "src/law_scrapper_mcp/config.py": (
        PROJECT_ROOT / "src" / "law_scrapper_mcp" / "config.py",
        r'(server_version\s*:\s*str\s*=\s*")[^"]+(")',
        r'server_version\s*:\s*str\s*=\s*"([^"]+)"',
    ),
    "tests/unit/test_config.py": (
        PROJECT_ROOT / "tests" / "unit" / "test_config.py",
        r'(assert\s+settings\.server_version\s*==\s*")[^"]+(")',
        r'assert\s+settings\.server_version\s*==\s*"([^"]+)"',
    ),
    "README.md": (
        PROJECT_ROOT / "README.md",
        r"(img\.shields\.io/badge/version-)[0-9]+\.[0-9]+\.[0-9]+[a-zA-Z0-9.-]*(-orange)",
        r"img\.shields\.io/badge/version-([0-9]+\.[0-9]+\.[0-9]+[a-zA-Z0-9.-]*)-orange",
    ),
    "CLAUDE.md": (
        PROJECT_ROOT / "CLAUDE.md",
        r"(Law Scrapper MCP v)[0-9]+\.[0-9]+\.[0-9]+[a-zA-Z0-9.-]*",
        r"Law Scrapper MCP v([0-9]+\.[0-9]+\.[0-9]+[a-zA-Z0-9.-]*)",
    ),
    # The root package entry only; the name line keeps every dependency's version out of reach.
    "uv.lock": (
        PROJECT_ROOT / "uv.lock",
        r'(^name = "law-scrapper-mcp"\nversion = ")[^"]+(")',
        r'^name = "law-scrapper-mcp"\nversion = "([^"]+)"',
    ),
    "tests/TEST_SUITE_SUMMARY.md": (
        PROJECT_ROOT / "tests" / "TEST_SUITE_SUMMARY.md",
        r"(Law Scrapper MCP v)[0-9]+\.[0-9]+\.[0-9]+[a-zA-Z0-9.-]*",
        r"Law Scrapper MCP v([0-9]+\.[0-9]+\.[0-9]+[a-zA-Z0-9.-]*)",
    ),
}

CORE_DISTRIBUTION = "law-scrapper-mcp"

# Workspace members under modules/ (directory names) that are never built into release assets
# and never installed in the container image. Every other member is released with the core.
NEVER_RELEASED_MODULES: frozenset[str] = frozenset({"reference"})

# A requirement's distribution name: the leading name token of a PEP 508 string.
_REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


@dataclass(frozen=True)
class ReleasedModule:
    """A source module that ships with the core: its directory, distribution name and pyproject."""

    directory: str
    distribution: str
    pyproject: Path


def released_modules() -> list[ReleasedModule]:
    """Every member under `modules/` that is not in NEVER_RELEASED_MODULES, sorted by directory."""
    modules_dir = PROJECT_ROOT / "modules"
    found: list[ReleasedModule] = []
    if not modules_dir.is_dir():
        return found
    for pyproject in sorted(modules_dir.glob("*/pyproject.toml")):
        directory = pyproject.parent.name
        if directory in NEVER_RELEASED_MODULES:
            continue
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        found.append(ReleasedModule(directory, data["project"]["name"], pyproject))
    return found


def _lock_path() -> Path:
    """The lock file the root entry is synced in; member entries live in the same file."""
    return VERSION_FILES["uv.lock"][0]


def _lock_entry_pattern(distribution: str) -> str:
    return rf'(^name = "{re.escape(distribution)}"\nversion = ")([^"]+)(")'


def _core_requirements(dependencies: list[str]) -> list[str]:
    """The entries of a dependency list that name the core distribution."""
    core: list[str] = []
    for requirement in dependencies:
        match = _REQUIREMENT_NAME.match(requirement)
        if match and match.group(1).lower().replace("_", "-") == CORE_DISTRIBUTION:
            core.append(requirement.strip())
    return core


def check_member_versions(expected_version: str) -> bool:
    """Every released module carries `expected_version` and pins the core to exactly it.

    Checks the module's `[project].version`, its single core requirement and its `uv.lock`
    version line. Inside the uv workspace a wrong pin is ignored, so this is the guard that
    keeps the built wheels installable together.
    """
    clean_expected = expected_version.lstrip("v")
    expected_pin = f"{CORE_DISTRIBUTION}=={clean_expected}"
    lock_text = _lock_path().read_text(encoding="utf-8") if _lock_path().exists() else ""
    modules = released_modules()
    all_ok = True

    print(f"   Released source modules ({len(modules)}):")
    for module in modules:
        project = tomllib.loads(module.pyproject.read_text(encoding="utf-8"))["project"]
        failures: list[str] = []
        version = project.get("version")
        if version != clean_expected:
            failures.append(f"version {version} (expected {clean_expected})")
        core = _core_requirements(project.get("dependencies", []))
        if core != [expected_pin]:
            failures.append(f"core requirement {core} (expected ['{expected_pin}'])")
        lock_match = re.search(_lock_entry_pattern(module.distribution), lock_text, re.MULTILINE)
        locked = lock_match.group(2) if lock_match else None
        if locked != clean_expected:
            failures.append(f"uv.lock version {locked} (expected {clean_expected})")
        for failure in failures:
            print(f"  [FAIL] {module.distribution}: {failure}")
        if failures:
            all_ok = False
        else:
            print(f"  [OK] {module.distribution}: {clean_expected}, pins {expected_pin}")
    return all_ok


def _sync_member_versions(clean_version: str) -> bool:
    """Rewrite each released module's version, core pin and uv.lock version line.

    Every rewrite is computed first; nothing is written unless all of them found exactly one target.
    """
    lock = _lock_path()
    lock_text = lock.read_text(encoding="utf-8") if lock.exists() else ""
    pending: list[tuple[ReleasedModule, str]] = []
    success = True
    for module in released_modules():
        content = module.pyproject.read_text(encoding="utf-8")
        content, versions = re.subn(
            r'(^version\s*=\s*")[^"]+(")', r"\g<1>" + clean_version + r"\g<2>", content, count=1, flags=re.MULTILINE
        )
        # Matches the bare or the pinned core requirement, never a longer name such as law-scrapper-mcp-saos.
        content, pins = re.subn(
            rf'"{re.escape(CORE_DISTRIBUTION)}(?:==[^"]*)?"', f'"{CORE_DISTRIBUTION}=={clean_version}"', content
        )
        lock_text, locked = re.subn(
            _lock_entry_pattern(module.distribution),
            r"\g<1>" + clean_version + r"\g<3>",
            lock_text,
            count=1,
            flags=re.MULTILINE,
        )
        if versions != 1 or pins != 1 or locked != 1:
            print(
                f"  [FAIL] {module.distribution}: expected one version line, one core requirement and one "
                f"uv.lock entry, found {versions}, {pins} and {locked}"
            )
            success = False
        pending.append((module, content))

    if not success:
        return False
    for module, content in pending:
        module.pyproject.write_text(content, encoding="utf-8")
        print(f"  [OK] {module.distribution}: version and core pin updated to {clean_version}")
    if pending:
        lock.write_text(lock_text, encoding="utf-8")
    return True


def sync_version_across_files(target_version: str) -> bool:
    """Update every file in VERSION_FILES to target_version."""
    clean_version = target_version.lstrip("v")
    print(f"Synchronizing version to '{clean_version}' across {len(VERSION_FILES)} files...")
    print("-" * 60)

    success = True
    for key, (file_path, replace_pattern, search_pattern) in VERSION_FILES.items():
        if not file_path.exists():
            print(f"  [FAIL] {key}: File not found ({file_path})")
            success = False
            continue

        content = file_path.read_text(encoding="utf-8")
        if not re.search(search_pattern, content, re.MULTILINE):
            print(f"  [FAIL] {key}: Search pattern match failed")
            success = False
            continue

        if key == "README.md":
            new_content = re.sub(
                replace_pattern, r"\g<1>" + clean_version + r"\g<2>", content, count=1, flags=re.MULTILINE
            )
        elif key in ("CLAUDE.md", "tests/TEST_SUITE_SUMMARY.md"):
            new_content = re.sub(replace_pattern, r"\g<1>" + clean_version, content, count=1, flags=re.MULTILINE)
        else:
            new_content = re.sub(
                replace_pattern, r"\g<1>" + clean_version + r"\g<2>", content, count=1, flags=re.MULTILINE
            )

        file_path.write_text(new_content, encoding="utf-8")
        print(f"  [OK] {key}: Updated to {clean_version}")

    # After the loop: the member entries share uv.lock with the root entry written above.
    if not _sync_member_versions(clean_version):
        success = False

    print("-" * 60)
    print("Version synchronization complete.\n")
    return success


def check_version_sync(expected_version: str | None = None) -> tuple[bool, str | None]:
    """Verify version match across VERSION_FILES and validate against expected_version if provided."""
    clean_expected = expected_version.lstrip("v") if expected_version else None
    versions: dict[str, str | None] = {}
    all_ok = True

    print(f"1. Checking version synchronization across {len(VERSION_FILES)} files:")
    print("-" * 60)

    for key, (file_path, _, search_pattern) in VERSION_FILES.items():
        if not file_path.exists():
            print(f"  [FAIL] {key}: File not found ({file_path})")
            versions[key] = None
            all_ok = False
            continue

        content = file_path.read_text(encoding="utf-8")
        match = re.search(search_pattern, content, re.MULTILINE)
        if match:
            ver = match.group(1)
            versions[key] = ver
            if clean_expected and ver != clean_expected:
                print(f"  [FAIL] {key}: {ver} (expected {clean_expected})")
                all_ok = False
            else:
                print(f"  [OK] {key}: {ver}")
        else:
            print(f"  [FAIL] {key}: Version pattern match failed")
            versions[key] = None
            all_ok = False

    found_versions = set(v for v in versions.values() if v is not None)
    if all_ok and len(found_versions) == 1:
        all_ok = check_member_versions(clean_expected or next(iter(found_versions)))

    print("-" * 60)

    if not all_ok or len(found_versions) != 1:
        print("[FAIL] Version sync check FAILED: Mismatched or missing versions detected.")
        if clean_expected:
            print(f"Hint: Run python scripts/check_release.py --sync-version {clean_expected} to update files.")
        return False, None

    detected_ver = list(found_versions)[0]
    print(f"[OK] All {len(VERSION_FILES)} files are synchronized to version v{detected_ver}\n")
    return True, detected_ver


def check_changelog(version: str) -> bool:
    """Check that docs/changelogs/vX.Y.Z.md exists and contains no TBD or TODO strings."""
    clean_version = version.lstrip("v")
    changelog_path = PROJECT_ROOT / "docs" / "changelogs" / f"v{clean_version}.md"

    print(f"2. Checking changelog file: docs/changelogs/v{clean_version}.md")
    print("-" * 60)

    if not changelog_path.exists():
        print(f"  [FAIL] Changelog file does not exist: {changelog_path}")
        print("-" * 60)
        return False

    content = changelog_path.read_text(encoding="utf-8")

    matches = list(re.finditer(r"\b(TBD|TODO)\b", content, re.IGNORECASE))
    if matches:
        print(f"  [FAIL] Changelog contains unfinished placeholder items (found {len(matches)} instance(s)):")
        for match in matches:
            line_no = content[: match.start()].count("\n") + 1
            line_str = content.splitlines()[line_no - 1]
            print(f"     Line {line_no}: {line_str.strip()}")
        print("-" * 60)
        return False

    print(f"  [OK] Changelog docs/changelogs/v{clean_version}.md exists and contains no TBD/TODO markers.")
    print("-" * 60)
    print()
    return True


# Top-level directories of the repository that must never reach the core sdist.
SDIST_FORBIDDEN_DIRS = (".claude", "modules", "tests", "docs")
ENTRY_POINT_GROUP = "law_scrapper_mcp.source_modules"


def core_version() -> str:
    """The root project's version, read from pyproject.toml."""
    data = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version: str = data["project"]["version"]
    return version


def released_distributions() -> list[str]:
    """The core followed by every released module's distribution name."""
    return [CORE_DISTRIBUTION, *(module.distribution for module in released_modules())]


def _file_stem(distribution: str) -> str:
    """The normalized name build backends use in artefact file names (PEP 625 / wheel spec)."""
    return re.sub(r"[-_.]+", "_", distribution).lower()


def expected_artefacts(version: str) -> set[str]:
    """The exact file names a release of `version` consists of: one sdist and one wheel per distribution."""
    names: set[str] = set()
    for distribution in released_distributions():
        stem = _file_stem(distribution)
        names.add(f"{stem}-{version}.tar.gz")
        names.add(f"{stem}-{version}-py3-none-any.whl")
    return names


def _wheel_requirements(wheel: Path) -> list[str]:
    """The `Requires-Dist` values in a wheel's METADATA."""
    with zipfile.ZipFile(wheel) as archive:
        metadata_name = next(name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))
        metadata = archive.read(metadata_name).decode("utf-8")
    return [line.split(":", 1)[1].strip() for line in metadata.splitlines() if line.startswith("Requires-Dist:")]


def inspect_packages(out_dir: Path, version: str) -> list[str]:
    """Every way the built artefacts in `out_dir` differ from a correct release of `version`."""
    failures: list[str] = []
    found = {path.name for path in out_dir.iterdir() if not path.name.startswith(".")}
    expected = expected_artefacts(version)
    for name in sorted(found - expected):
        failures.append(f"{name}: not part of the release")
    for name in sorted(expected - found):
        failures.append(f"{name}: missing")
    for name in sorted(found):
        if "reference" in name:
            failures.append(f"{name}: the reference module is never released")

    expected_pin = f"{CORE_DISTRIBUTION}=={version}"
    for module in released_modules():
        wheel = out_dir / f"{_file_stem(module.distribution)}-{version}-py3-none-any.whl"
        if wheel.exists():
            core = _core_requirements(_wheel_requirements(wheel))
            if core != [expected_pin]:
                failures.append(f"{wheel.name}: Requires-Dist on the core is {core}, expected ['{expected_pin}']")

    sdist = out_dir / f"{_file_stem(CORE_DISTRIBUTION)}-{version}.tar.gz"
    if sdist.exists():
        with tarfile.open(sdist) as archive:
            members = archive.getnames()
        for member in members:
            parts = member.split("/")
            if len(parts) > 1 and parts[1] in SDIST_FORBIDDEN_DIRS:
                failures.append(f"{sdist.name}: contains {member}")
    return failures


def build_packages(out_dir: Path) -> bool:
    """Build the core and every released module into an empty `out_dir`, then inspect the result."""
    if out_dir.exists() and any(out_dir.iterdir()):
        print(f"[FAIL] {out_dir} is not empty; build into a fresh directory so no stale file is released")
        return False
    out_dir.mkdir(parents=True, exist_ok=True)
    version = core_version()
    print(f"Building release packages v{version} into {out_dir}:")
    print("-" * 60)
    for distribution in released_distributions():
        command = ["uv", "build", "--package", distribution, "--out-dir", str(out_dir)]
        if not run_command(command, f"Build {distribution}"):
            return False

    failures = inspect_packages(out_dir, version)
    for failure in failures:
        print(f"  [FAIL] {failure}")
    if failures:
        return False
    for name in sorted(expected_artefacts(version)):
        print(f"  [OK] {name}")
    print("-" * 60)
    return True


def _declared_entry_points() -> set[str]:
    """The source-module entry point names the released modules declare in their pyprojects."""
    names: set[str] = set()
    for module in released_modules():
        project = tomllib.loads(module.pyproject.read_text(encoding="utf-8"))["project"]
        names.update(project.get("entry-points", {}).get(ENTRY_POINT_GROUP, {}))
    return names


def compare_installed(installed: dict[str, str], entry_points: set[str], version: str) -> list[str]:
    """Every way an environment's law-scrapper distributions and module entry points differ from a release.

    `installed` maps distribution names that start with the core's name to their versions.
    """
    failures: list[str] = []
    expected = set(released_distributions())
    for name in sorted(set(installed) - expected):
        failures.append(f"{name} is installed but is not part of the release")
    for name in sorted(expected - set(installed)):
        failures.append(f"{name} is not installed")
    for name, installed_version in sorted(installed.items()):
        if name in expected and installed_version != version:
            failures.append(f"{name} {installed_version} (expected {version})")
    declared = _declared_entry_points()
    if entry_points != declared:
        failures.append(f"entry points {sorted(entry_points)} (expected {sorted(declared)})")
    return failures


def check_installed() -> bool:
    """Run in the environment the released wheels were installed into; compare it with the release."""
    installed: dict[str, str] = {}
    for dist in importlib.metadata.distributions():
        name = re.sub(r"[-_.]+", "-", dist.metadata["Name"] or "").lower()
        if name.startswith(CORE_DISTRIBUTION):
            installed[name] = dist.version
    entry_points = {ep.name for ep in importlib.metadata.entry_points(group=ENTRY_POINT_GROUP)}
    failures = compare_installed(installed, entry_points, core_version())
    for failure in failures:
        print(f"  [FAIL] {failure}")
    if failures:
        return False
    print(f"  [OK] installed: {sorted(installed.items())}; entry points: {sorted(entry_points)}")
    return True


def run_command(cmd: list[str], description: str) -> bool:
    """Execute command via subprocess and print status."""
    print(f"Running: {' '.join(cmd)}")
    executable = cmd[0]
    resolved = shutil.which(executable)
    if resolved:
        cmd[0] = resolved

    use_shell = sys.platform == "win32" and executable in ("uv", "pytest", "ruff", "mypy")

    try:
        result = subprocess.run(cmd, cwd=PROJECT_ROOT, shell=use_shell)
        if result.returncode == 0:
            print(f"[OK] {description} PASSED.\n")
            return True
        else:
            print(f"[FAIL] {description} FAILED with exit code {result.returncode}.\n")
            return False
    except Exception as e:
        print(f"[FAIL] {description} FAILED with exception: {e}\n")
        return False


def _module_paths(pattern: str) -> list[str]:
    """Directories under `modules/` matching `pattern`, as repo-relative POSIX paths."""
    found = (PROJECT_ROOT / "modules").glob(pattern)
    return sorted(path.relative_to(PROJECT_ROOT).as_posix() for path in found if path.is_dir())


def run_checks_and_tests() -> bool:
    """Run the lint, format, type and test gates of the CI `lint`, `test` and `modules` jobs.

    Mirrors `.github/workflows/ci.yml`, so a green run here means those jobs pass too.
    MCP conformance, the Docker smoke tests and pip-audit stay CI-only.
    """
    print("3. Running quality gates and test suite:")
    print("-" * 60)

    # Source modules are linted, type-checked and tested like core code (CI `lint` and `modules`
    # jobs). The globs mean a new module needs no edit here.
    module_sources = _module_paths("*/src/law_scrapper_mcp_*")
    module_tests = _module_paths("*/tests")

    commands = [
        (["uv", "run", "ruff", "check", "src", "tests", "modules"], "Ruff linter"),
        (["uv", "run", "ruff", "format", "--check", "src", "tests", "modules"], "Ruff format"),
        (["uv", "run", "mypy", "src/law_scrapper_mcp/", *module_sources], "Mypy type checker"),
        (["uv", "run", "pytest", "tests/unit/"], "Pytest unit tests"),
        (["uv", "run", "pytest", "tests/integration/", "-m", "integration"], "Pytest integration tests"),
    ]
    if module_tests:
        # `--all-packages` installs the workspace members, which registers their entry points.
        commands.append((["uv", "run", "--all-packages", "pytest", *module_tests], "Pytest module tests"))

    # The CI `packages` job's build and inspection, into a throwaway directory.
    dist_dir = Path(tempfile.mkdtemp(prefix="law-mcp-dist-"))
    commands.append(
        (
            [sys.executable, str(PROJECT_ROOT / "scripts" / "check_release.py"), "--build-packages", str(dist_dir)],
            "Build and inspect release packages",
        )
    )
    try:
        return all(run_command(cmd, desc) for cmd, desc in commands)
    finally:
        shutil.rmtree(dist_dir, ignore_errors=True)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Pre-release verification script.")
    parser.add_argument("--version", help="Version to check (e.g. 2.4.0 or v2.4.0)")
    parser.add_argument("--sync-version", help="Automatically update every version file to specified version")
    parser.add_argument("--skip-tests", action="store_true", help="Skip the ruff, mypy and pytest gates")
    parser.add_argument(
        "--build-packages",
        metavar="DIR",
        help="Only build the core and every released module into DIR and inspect the files",
    )
    parser.add_argument(
        "--check-installed",
        action="store_true",
        help="Only compare this environment's law-scrapper packages and module entry points with the release",
    )

    args = parser.parse_args()

    if args.build_packages:
        sys.exit(0 if build_packages(Path(args.build_packages)) else 1)
    if args.check_installed:
        sys.exit(0 if check_installed() else 1)

    if args.sync_version:
        sync_ok = sync_version_across_files(args.sync_version)
        if not sync_ok:
            print("[FAIL] Failed to sync versions across all files.")
            sys.exit(1)

    target_ver = args.version or args.sync_version

    # Version check
    sync_ok, detected_ver = check_version_sync(target_ver)
    if not sync_ok:
        sys.exit(1)

    check_ver = (target_ver or detected_ver or "").lstrip("v")
    if not check_ver:
        print("[FAIL] Could not determine version to verify. Please specify --version X.Y.Z")
        sys.exit(1)

    # Changelog check
    if not check_changelog(check_ver):
        sys.exit(1)

    # Quality check & unit tests
    if not args.skip_tests and not run_checks_and_tests():
        sys.exit(1)

    print("============================================================")
    print(f"Pre-release verification PASSED for version v{check_ver}!")
    print("============================================================")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Pre-release verification and version synchronization script.

Validates release readiness for law-scrapper-mcp by:
1. Synchronizing or checking version strings across project files.
2. Checking existence and completeness of per-version changelogs.
3. Running linters, type checkers, and unit test suite via uv.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Project root directory
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 5 files containing version numbers with their update and search regex patterns
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
}


def sync_version_across_files(target_version: str) -> bool:
    """Update all 5 files to set version to target_version."""
    clean_version = target_version.lstrip("v")
    print(f"Synchronizing version to '{clean_version}' across 5 files...")
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
        elif key == "CLAUDE.md":
            new_content = re.sub(replace_pattern, r"\g<1>" + clean_version, content, count=1, flags=re.MULTILINE)
        else:
            new_content = re.sub(
                replace_pattern, r"\g<1>" + clean_version + r"\g<2>", content, count=1, flags=re.MULTILINE
            )

        file_path.write_text(new_content, encoding="utf-8")
        print(f"  [OK] {key}: Updated to {clean_version}")

    print("-" * 60)
    print("Version synchronization complete.\n")
    return success


def check_version_sync(expected_version: str | None = None) -> tuple[bool, str | None]:
    """Verify version match across 5 files and validate against expected_version if provided."""
    clean_expected = expected_version.lstrip("v") if expected_version else None
    versions: dict[str, str | None] = {}
    all_ok = True

    print("1. Checking version synchronization across 5 files:")
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

    print("-" * 60)

    found_versions = set(v for v in versions.values() if v is not None)
    if not all_ok or len(found_versions) != 1:
        print("[FAIL] Version sync check FAILED: Mismatched or missing versions detected.")
        if clean_expected:
            print(f"Hint: Run python scripts/check_release.py --sync-version {clean_expected} to update files.")
        return False, None

    detected_ver = list(found_versions)[0]
    print(f"[OK] All 5 files are synchronized to version v{detected_ver}\n")
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


def run_checks_and_tests() -> bool:
    """Run ruff check, mypy, and pytest unit tests."""
    print("3. Running quality gates and unit test suite:")
    print("-" * 60)

    commands = [
        (["uv", "run", "ruff", "check", "src/"], "Ruff linter"),
        (["uv", "run", "mypy", "src/law_scrapper_mcp/"], "Mypy type checker"),
        (["uv", "run", "pytest", "tests/unit/"], "Pytest unit tests"),
    ]

    return all(run_command(cmd, desc) for cmd, desc in commands)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Pre-release verification script.")
    parser.add_argument("--version", help="Version to check (e.g. 2.4.0 or v2.4.0)")
    parser.add_argument("--sync-version", help="Automatically update all 5 files to specified version")
    parser.add_argument("--skip-tests", action="store_true", help="Skip running ruff, mypy, pytest")

    args = parser.parse_args()

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

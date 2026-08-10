#!/usr/bin/env python3
"""Git release info extractor and version synchronization checker.

Parses git commit history using Conventional Commits convention and groups
them into release notes sections (Added, Fixed, Changed, Breaking Changes).
Also checks version synchronization across project metadata files.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Project root relative to this script
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Files containing version numbers to check
VERSION_FILES: dict[str, tuple[Path, str]] = {
    "pyproject.toml": (
        PROJECT_ROOT / "pyproject.toml",
        r'^version\s*=\s*"([^"]+)"',
    ),
    "src/law_scrapper_mcp/config.py": (
        PROJECT_ROOT / "src" / "law_scrapper_mcp" / "config.py",
        r'server_version\s*:\s*str\s*=\s*"([^"]+)"',
    ),
    "tests/unit/test_config.py": (
        PROJECT_ROOT / "tests" / "unit" / "test_config.py",
        r'assert\s+settings\.server_version\s*==\s*"([^"]+)"',
    ),
    "README.md": (
        PROJECT_ROOT / "README.md",
        r"img\.shields\.io/badge/version-([0-9]+\.[0-9]+\.[0-9]+[a-zA-Z0-9.-]*)-orange",
    ),
    "CLAUDE.md": (
        PROJECT_ROOT / "CLAUDE.md",
        r"Law Scrapper MCP v([0-9]+\.[0-9]+\.[0-9]+[a-zA-Z0-9.-]*)",
    ),
}

CONVENTIONAL_PATTERN = re.compile(
    r"^(?P<type>feat|fix|perf|refactor|docs|style|chore|test|ci|build)"
    r"(?:\((?P<scope>[^)]+)\))?"
    r"(?P<breaking>!)?"
    r":\s*(?P<subject>.+)$",
    re.IGNORECASE,
)


def get_git_executable() -> str:
    """Return path to git binary or raise error if missing."""
    git_bin = shutil.which("git")
    if not git_bin:
        raise RuntimeError("git CLI is not installed or not available in PATH.")
    return git_bin


def check_version_sync() -> bool:
    """Check if all 5 project files have identical version strings."""
    versions: dict[str, str | None] = {}
    all_ok = True

    print("Checking version synchronization across project files:")
    print("-" * 60)

    for key, (file_path, pattern) in VERSION_FILES.items():
        if not file_path.exists():
            print(f"  ❌ {key}: File not found ({file_path})")
            versions[key] = None
            all_ok = False
            continue

        content = file_path.read_text(encoding="utf-8")
        match = re.search(pattern, content, re.MULTILINE)
        if match:
            ver = match.group(1)
            versions[key] = ver
            print(f"  ✓ {key}: {ver}")
        else:
            print(f"  ❌ {key}: Version pattern not found")
            versions[key] = None
            all_ok = False

    print("-" * 60)

    found_versions = set(v for v in versions.values() if v is not None)
    if not all_ok or len(found_versions) != 1:
        print("❌ Version sync check FAILED: Mismatched or missing versions detected.")
        return False

    synced_version = list(found_versions)[0]
    print(f"✓ All 5 files are synchronized to version v{synced_version}")
    return True


def get_latest_tag() -> str | None:
    """Retrieve the latest git tag, or None if no tag exists."""
    git_bin = get_git_executable()
    result = subprocess.run(
        [git_bin, "describe", "--tags", "--abbrev=0"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return None


def get_initial_commit() -> str | None:
    """Retrieve the initial commit SHA if no tags exist."""
    git_bin = get_git_executable()
    result = subprocess.run(
        [git_bin, "rev-list", "--max-parents=0", "HEAD"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip().splitlines()[0]
    return None


def get_commit_range(user_range: str | None) -> str:
    """Determine git commit range based on user input or defaults."""
    if user_range:
        return user_range

    latest_tag = get_latest_tag()
    if latest_tag:
        return f"{latest_tag}..HEAD"

    initial_commit = get_initial_commit()
    if initial_commit:
        return f"{initial_commit}..HEAD"

    return "HEAD"


def get_git_log(commit_range: str) -> list[tuple[str, str, str]]:
    """Fetch list of (short_hash, subject, body) for given range."""
    git_bin = get_git_executable()
    format_str = "%h\x1f%s\x1f%b\x1e"
    cmd = [git_bin, "log", commit_range, f"--format={format_str}"]
    result = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"Warning: git log failed for range '{commit_range}': {result.stderr.strip()}", file=sys.stderr)
        return []

    commits: list[tuple[str, str, str]] = []
    raw_records = result.stdout.split("\x1e")
    for record in raw_records:
        record = record.strip()
        if not record:
            continue
        parts = record.split("\x1f")
        short_hash = parts[0].strip() if len(parts) > 0 else ""
        subject = parts[1].strip() if len(parts) > 1 else ""
        body = parts[2].strip() if len(parts) > 2 else ""
        commits.append((short_hash, subject, body))

    return commits


def parse_and_group_commits(commits: list[tuple[str, str, str]]) -> dict[str, list[str]]:
    """Group commits into Conventional Commits sections."""
    grouped: dict[str, list[str]] = {
        "Breaking Notes": [],
        "Added": [],
        "Changed": [],
        "Fixed": [],
        "Other": [],
    }

    for short_hash, subject, body in commits:
        is_breaking = False
        breaking_note = ""

        if "BREAKING CHANGE:" in body or "BREAKING-CHANGE:" in body:
            is_breaking = True
            for line in body.splitlines():
                if "BREAKING CHANGE:" in line or "BREAKING-CHANGE:" in line:
                    breaking_note = line.split(":", 1)[1].strip()
                    break
        elif "!" in subject.split(":", 1)[0]:
            is_breaking = True

        match = CONVENTIONAL_PATTERN.match(subject)
        if match:
            c_type = match.group("type").lower()
            scope = match.group("scope")
            c_subj = match.group("subject").strip()
            formatted_item = f"- **{scope}**: {c_subj} ({short_hash})" if scope else f"- {c_subj} ({short_hash})"

            if c_type == "feat":
                grouped["Added"].append(formatted_item)
            elif c_type == "fix":
                grouped["Fixed"].append(formatted_item)
            elif c_type in ("perf", "refactor", "docs", "style", "chore", "test", "ci", "build"):
                grouped["Changed"].append(formatted_item)
            else:
                grouped["Other"].append(formatted_item)
        else:
            grouped["Other"].append(f"- {subject} ({short_hash})")

        if is_breaking:
            note = breaking_note if breaking_note else subject
            grouped["Breaking Notes"].append(f"- **BREAKING CHANGE**: {note} ({short_hash})")

    return grouped


def generate_release_notes(grouped: dict[str, list[str]], commit_range: str) -> str:
    """Generate Markdown formatted release notes."""
    lines = [f"## Git Release Info (Range: `{commit_range}`)\n"]

    sections = [
        ("Breaking Notes", "### Breaking Notes"),
        ("Added", "### Added"),
        ("Changed", "### Changed"),
        ("Fixed", "### Fixed"),
        ("Other", "### Other"),
    ]

    has_content = False
    for key, title in sections:
        items = grouped.get(key, [])
        if items:
            has_content = True
            lines.append(title)
            lines.extend(items)
            lines.append("")

    if not has_content:
        lines.append("_No commits found in range._\n")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract git release info and check version synchronization.")
    parser.add_argument("--range", help="Git revision range (default: latest_tag..HEAD or initial commit)")
    parser.add_argument("--check-sync", action="store_true", help="Check version sync across project files")

    args = parser.parse_args()
    exit_code = 0

    if args.check_sync:
        sync_ok = check_version_sync()
        if not sync_ok:
            exit_code = 1
        print()

    # If --check-sync was the only option and specified explicitly, exit after sync check
    if args.check_sync and not args.range and "--range" not in sys.argv:
        sys.exit(exit_code)

    commit_range = get_commit_range(args.range)
    commits = get_git_log(commit_range)
    grouped = parse_and_group_commits(commits)
    notes = generate_release_notes(grouped, commit_range)

    print(notes)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()

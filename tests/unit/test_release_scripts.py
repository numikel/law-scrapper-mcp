"""Unit tests for release scripts (git_release_info.py and check_release.py)."""

from __future__ import annotations

import sys
from pathlib import Path

# Add scripts directory to sys.path so we can import helper modules
SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import check_release  # noqa: E402
import git_release_info  # noqa: E402


class TestGitReleaseInfo:
    """Tests for git_release_info.py functions."""

    def test_parse_and_group_conventional_commits(self):
        commits = [
            ("a1b2c3d", "feat(api): add new endpoint", "Details about endpoint"),
            ("b2c3d4e", "fix(cache): resolve cache key collision", ""),
            ("c3d4e5f", "refactor(config): cleanup settings logic", ""),
            ("d4e5f6a", "docs: update installation instructions", ""),
            ("e5f6a7b", "feat(auth)!: break legacy auth header", "BREAKING CHANGE: header format changed"),
            ("f6a7b8c", "chore: random cleanup", ""),
        ]

        grouped = git_release_info.parse_and_group_commits(commits)

        assert len(grouped["Added"]) == 2
        assert "- **api**: add new endpoint (a1b2c3d)" in grouped["Added"]

        assert len(grouped["Fixed"]) == 1
        assert "- **cache**: resolve cache key collision (b2c3d4e)" in grouped["Fixed"]

        assert len(grouped["Changed"]) == 3  # refactor, docs, chore
        assert any("cleanup settings logic" in item for item in grouped["Changed"])
        assert any("update installation instructions" in item for item in grouped["Changed"])

        assert len(grouped["Breaking Notes"]) == 1
        assert any("header format changed" in item for item in grouped["Breaking Notes"])

    def test_version_files_exist_and_sync(self):
        """Verify version files list maps to existing files in repository."""
        import re

        pyproject_path, pyproject_pattern = git_release_info.VERSION_FILES["pyproject.toml"]
        pyproject_match = re.search(pyproject_pattern, pyproject_path.read_text(encoding="utf-8"), re.MULTILINE)
        assert pyproject_match is not None, "Version pattern did not match in pyproject.toml"
        expected_version = pyproject_match.group(1)

        # Both scripts keep their own copy of the list; a file added to one only is the drift #65 fixed.
        assert git_release_info.VERSION_FILES.keys() == check_release.VERSION_FILES.keys()

        for key, (path, pattern) in git_release_info.VERSION_FILES.items():
            assert path.exists(), f"File for {key} does not exist: {path}"
            content = path.read_text(encoding="utf-8")

            match = re.search(pattern, content, re.MULTILINE)
            assert match is not None, f"Pattern for {key} did not match in {path}"
            assert match.group(1) == expected_version, f"Version mismatch in {key}: got {match.group(1)}"


class TestCheckRelease:
    """Tests for check_release.py functions."""

    def test_check_changelog_valid(self, tmp_path):
        changelog_file = tmp_path / "v2.4.0.md"
        changelog_file.write_text("# Changelog v2.4.0\n\n- Added feature X", encoding="utf-8")

        # Monkeypatch PROJECT_ROOT in check_release
        original_root = check_release.PROJECT_ROOT
        try:
            check_release.PROJECT_ROOT = tmp_path.parent
            changelogs_dir = tmp_path.parent / "docs" / "changelogs"
            changelogs_dir.mkdir(parents=True, exist_ok=True)
            (changelogs_dir / "v2.4.0.md").write_text("# Changelog v2.4.0\n\n- Added feature X", encoding="utf-8")

            assert check_release.check_changelog("2.4.0") is True
        finally:
            check_release.PROJECT_ROOT = original_root

    def test_check_changelog_contains_todo(self, tmp_path):
        original_root = check_release.PROJECT_ROOT
        try:
            check_release.PROJECT_ROOT = tmp_path
            changelogs_dir = tmp_path / "docs" / "changelogs"
            changelogs_dir.mkdir(parents=True, exist_ok=True)
            (changelogs_dir / "v2.4.0.md").write_text("# Changelog v2.4.0\n\n- TODO: complete notes", encoding="utf-8")

            assert check_release.check_changelog("2.4.0") is False
        finally:
            check_release.PROJECT_ROOT = original_root

    def test_check_version_sync_current_repo(self):
        """Test check_version_sync against repository files."""
        import re

        pyproject_path, pyproject_pattern = git_release_info.VERSION_FILES["pyproject.toml"]
        pyproject_match = re.search(pyproject_pattern, pyproject_path.read_text(encoding="utf-8"), re.MULTILINE)
        assert pyproject_match is not None, "Version pattern did not match in pyproject.toml"
        expected_version = pyproject_match.group(1)

        # The test-suite summary carries the version too, and went stale once (#65).
        assert "tests/TEST_SUITE_SUMMARY.md" in check_release.VERSION_FILES

        sync_ok, ver = check_release.check_version_sync()
        assert sync_ok is True
        assert ver == expected_version
        assert "uv.lock" in check_release.VERSION_FILES

    def test_sync_version_writes_only_the_uv_lock_root_entry(self, tmp_path, monkeypatch):
        """--sync-version bumps the root package entry in uv.lock and no other `version =` line."""
        import re
        import shutil

        rerooted = {}
        for key, (path, replace_pattern, search_pattern) in check_release.VERSION_FILES.items():
            target = tmp_path / key
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            rerooted[key] = (target, replace_pattern, search_pattern)
        lock = tmp_path / "uv.lock"
        shutil.copyfile(check_release.PROJECT_ROOT / "uv.lock", lock)
        monkeypatch.setattr(check_release, "VERSION_FILES", rerooted)

        original = lock.read_text(encoding="utf-8")
        root_entry = re.search(r'^name = "law-scrapper-mcp"\nversion = "[^"]+"$', original, re.MULTILINE)
        assert root_entry is not None, "uv.lock has no root package entry"

        assert check_release.sync_version_across_files("9.9.9") is True
        assert lock.read_text(encoding="utf-8") == original.replace(
            root_entry.group(0), 'name = "law-scrapper-mcp"\nversion = "9.9.9"'
        )
        assert check_release.check_version_sync("9.9.9") == (True, "9.9.9")

        # A lock left behind by the bump fails the check.
        lock.write_text(original, encoding="utf-8")
        assert check_release.check_version_sync("9.9.9") == (False, None)

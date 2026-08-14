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

        sync_ok, ver = check_release.check_version_sync()
        assert sync_ok is True
        assert ver == expected_version

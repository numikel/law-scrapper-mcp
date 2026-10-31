"""PostToolUse hook: format and lint a Python file right after Claude edits it.

Mirrors the CI lint job's scope (`src/` and `tests/`): `ruff format`, then `ruff check --fix`.
Exit codes follow the Claude Code hook contract:
- 0: nothing to do, or the file is clean after the automatic fixes
- 2: violations ruff cannot fix remain; stderr is fed back to Claude so it fixes them now
- 1: ruff itself failed (not installed, bad config); stderr is shown to the user, edit not blocked
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

LINTED_DIRS = ("src", "tests")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    try:
        payload = json.loads(sys.stdin.read().lstrip("﻿") or "{}")
    except json.JSONDecodeError:
        return 0
    tool_input = payload.get("tool_input") or {}
    tool_response = payload.get("tool_response") or {}
    raw_path = tool_input.get("file_path") or tool_response.get("filePath")
    if not raw_path or not str(raw_path).endswith(".py"):
        return 0

    project = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2]).resolve()
    target = Path(raw_path).resolve()
    try:
        relative = target.relative_to(project)
    except ValueError:
        return 0
    if not relative.parts or relative.parts[0] not in LINTED_DIRS or not target.is_file():
        return 0

    ruff = [sys.executable, "-m", "ruff"]
    fmt = subprocess.run([*ruff, "format", "--force-exclude", str(target)], capture_output=True, text=True, cwd=project)
    if fmt.returncode != 0:
        sys.stderr.write(f"ruff format failed on {relative.as_posix()}:\n{fmt.stdout}{fmt.stderr}")
        return 1

    check = subprocess.run(
        [*ruff, "check", "--fix", "--force-exclude", "--output-format", "concise", str(target)],
        capture_output=True,
        text=True,
        cwd=project,
    )
    if check.returncode == 0:
        return 0
    if check.returncode == 1:
        sys.stderr.write(f"ruff check found violations it cannot fix in {relative.as_posix()}:\n{check.stdout}")
        return 2
    sys.stderr.write(f"ruff check failed on {relative.as_posix()}:\n{check.stdout}{check.stderr}")
    return 1


if __name__ == "__main__":
    sys.exit(main())

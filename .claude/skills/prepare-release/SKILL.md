---
name: prepare-release
description: Prepare a law-scrapper-mcp release — write Keep a Changelog files, bump version across 5 locations, run release checks, and commit. Use when the user asks to prepare release vX.Y.Z or run /prepare-release.
---

# Prepare release

Prepare a `law-scrapper-mcp` release locally. **No LLM calls in CI** — all changelog generation and version synchronization happen here in the IDE.

## Invocation

User runs `/prepare-release X.Y.Z` (or `/prepare-release vX.Y.Z`).
- Always strip any leading `v` prefix so that `X.Y.Z` is pure semver (e.g., `2.5.0`).

## Mandatory checklist (follow in order)

### 1. Resolve commit range

Run the git release info script to discover changes since the last tagged release:

```bash
uv run python scripts/git_release_info.py --range
```

If no prior tags exist or if `--range` reports no commits since the last tag, stop and confirm with the user before proceeding with an empty changelog.

### 2. Classify commits

Group commit subjects using Conventional Commits into Keep a Changelog categories:

| Conventional Commit Type | Keep a Changelog Section |
|--------------------------|--------------------------|
| `feat` | `Added` |
| `fix` | `Fixed` |
| `perf`, `refactor` | `Changed` |
| `docs` | `Changed` (omit if purely internal) |
| `security` | `Security` |
| `deprecate` | `Deprecated` |
| `remove` | `Removed` |
| `ci`, `build`, `chore` | Omit unless user-facing |
| `BREAKING CHANGE:` / `!` | `Changed` (with explicit breaking change note) |

Write user-facing bullet points in English. Avoid raw commit SHAs in bullet text.

### 3. Write per-version changelog

Create `docs/changelogs/vX.Y.Z.md` using `docs/templates/changelog-version-template.md`:

- Follow [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/) standards.
- Include release date in UTC (`YYYY-MM-DD`).
- Include relevant sections (`Added`, `Changed`, `Deprecated`, `Removed`, `Fixed`, `Security`) and omit empty ones.
- Refer to `docs/changelogs/v2.4.0.md` for structure and tone consistency.

### 4. Update root CHANGELOG.md

Update the root `CHANGELOG.md` using `docs/templates/release-summary-template.md`:

- Move relevant `[Unreleased]` items into a new version section `## [X.Y.Z] - YYYY-MM-DD`.
- Add a summary referencing the per-version file `docs/changelogs/vX.Y.Z.md`.
- Keep the `[Unreleased]` section header ready for future changes.

### 5. Version bump across all 5 locations

Synchronize the version number `X.Y.Z` across all 5 target files using the helper script:

```bash
uv run python scripts/check_release.py --sync-version X.Y.Z
```

Verify that the following 5 locations have been updated to `X.Y.Z`:
1. `pyproject.toml` (`version = "X.Y.Z"`)
2. `src/law_scrapper_mcp/config.py` (`server_version: str = "X.Y.Z"`)
3. `tests/unit/test_config.py` (`assert settings.server_version == "X.Y.Z"`)
4. `README.md` (`![Version](https://img.shields.io/badge/version-X.Y.Z-orange.svg)`)
5. `CLAUDE.md` (`Law Scrapper MCP vX.Y.Z ...`)

### 6. Self-check (must pass before commit)

Run automated release verification and quality checks:

```bash
# 1. Verify release metadata and version alignment across all 5 locations
uv run python scripts/check_release.py --version X.Y.Z

# 2. Run code formatting and linting
uv run ruff check src/
uv run mypy src/law_scrapper_mcp/

# 3. Run test suite
uv run pytest tests/unit/ -v
uv run pytest tests/integration/ -v -m integration
```

Checklist validation:
- [ ] `docs/changelogs/vX.Y.Z.md` exists and contains no placeholder text (`TODO`, `TBD`).
- [ ] Version `X.Y.Z` is strictly consistent across all 5 files.
- [ ] Release date matches today UTC.
- [ ] All linting, typing, and tests pass clean.

### 7. Release commit

Stage changed release artifacts and create a single commit:

```bash
git add pyproject.toml src/law_scrapper_mcp/config.py tests/unit/test_config.py README.md CLAUDE.md CHANGELOG.md docs/changelogs/vX.Y.Z.md
git commit -m "chore(release): prepare vX.Y.Z"
```

### 8. Maintainer post-steps output

Output instructions for the maintainer to tag and push the release:

```bash
git tag vX.Y.Z
git push origin main --tags
```

CI `release.yml` will validate the tag, build sdist/wheel artifacts, and publish the GitHub release with `docs/changelogs/vX.Y.Z.md` as the release notes.

## Error Handling

| Scenario | Action |
|----------|--------|
| User passes `vX.Y.Z` | Strip leading `v` and use `X.Y.Z` |
| Empty commit range | Ask user to confirm before creating changelog |
| Version mismatch across files | Run `uv run python scripts/check_release.py --sync-version X.Y.Z` |
| Placeholders in changelog | Remove all `TBD`/`TODO` text prior to commit |

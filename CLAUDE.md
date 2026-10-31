# CLAUDE.md

## Project overview

Law Scrapper MCP v4.4.2 is a modular Python MCP server that exposes 13 tools for searching and analyzing Polish legal acts from the Sejm API (`api.sejm.gov.pl/eli/`). Built with the official Python MCP SDK (`mcp[cli]==2.2.0`, `MCPServer[AppContext]`), it supports STDIO (default) and stateless Streamable HTTP at `/mcp` on port 7683.

## Development commands

```bash
# Dev tools (pytest, ruff, mypy, pip-audit) are an extra — plain `uv sync` omits them
uv sync --extra dev

# The CI lint job, in full. `ruff format --check` is the gate most often missed locally
uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src/law_scrapper_mcp/

# Integration tests need the marker, or they are silently skipped
uv run pytest tests/integration/ -v -m integration

# HTTP transport is opt-in via env var, not a flag
LAW_MCP_TRANSPORT=streamable-http uv run python -m law_scrapper_mcp

# Local mirror of the CI lint + test jobs; MCP conformance, Docker smoke and pip-audit run only in CI
uv run python scripts/check_release.py --version X.Y.Z
```

mypy runs with `disallow_untyped_defs = true`: every new function, tests included, needs annotations.

## HTTP auth and binding

`LAW_MCP_AUTH_MODE` defaults to `none`, which is accepted only on loopback. A non-loopback
`LAW_MCP_HOST`, `LAW_MCP_ALLOWED_HOSTS` or `LAW_MCP_ALLOWED_ORIGINS` makes startup fail unless the
mode is `bearer` or `oauth` (`config_validation.py`). This is deliberate — fix the configuration,
never the validator. `docker-compose.yml` needs `LAW_MCP_AUTH_TOKEN` exported, and
`tests/unit/test_deployment_files.py` rejects an unauthenticated exposure in the Dockerfile,
compose file or README.

## Testing

Layers, commands and file layout: `tests/TEST_SUITE_SUMMARY.md`. Every new test file must be listed
there. Policy tests that fail on edits outside `src/`:
- `test_readme_tool_table.py` — the README tool table must match the live `tools/list`, so adding or
  renaming a tool means editing the README
- `test_dependency_policy.py` — `mcp[cli]` stays pinned with `==`, `pyjwt` and `uvicorn` stay direct
  dependencies, no `fastmcp` anywhere

## Release

Run `/prepare-release X.Y.Z` (maintainers). The version lives in 7 places, listed in
`scripts/check_release.py`; `--sync-version X.Y.Z` rewrites them all. One of them is the overview
line above: a regex matches its "Law Scrapper MCP v" + version form, so keep that phrasing.
A PR that bumps the version must add `docs/changelogs/vX.Y.Z.md` (CI `release-prep-check`);
`release.yml` runs only on a pushed `v*` tag and publishes that file as the release body.

## Architecture

Layered, src/ layout: `models/` → `client/` → `services/` → `tools/` → `server.py`. Imports point
left only: `tools/` reaches `services/` and `models/`, never `client/` or `httpx` (sole exception:
`tools/error_handling.py`, which maps client and `httpx` exceptions), and `httpx` stays in `client/`.
Root modules (`config.py`, `context.py`) are open to every layer. `tests/unit/test_tool_architecture.py`
enforces this.

**Key patterns:**
- **Document Store**: Acts loaded into memory for section-level reading and search
- **Result Store**: Search results persisted for chained filtering (LRU eviction, TTL)
- **Circuit Breaker**: `client/circuit_breaker.py` guards calls to the Sejm API
- **Egress restraint**: token-bucket pacing (`LAW_MCP_API_RATE_PER_SECOND`/`_BURST`), a bounded server-pause (`LAW_MCP_API_MAX_SERVER_PAUSE`), and document downloads aborted mid-stream past `LAW_MCP_DOC_STORE_MAX_SIZE_BYTES`
- **Enriched responses**: Every tool returns `EnrichedResponse` with hints; native `outputSchema` and object `structuredContent`
- **Pagination**: `PageInfo` model exposed as the `page_info` field with `limit`/`offset` on list and content tools (defaults: 20 items, 10,000 chars; maxima: 100 items, 50,000 chars)
- **TTL cache**: `client/cache.py`; per-category TTLs are the `LAW_MCP_CACHE_*` settings in `config.py`
- **Error handling**: `@handle_tool_errors` re-raises `ToolExecutionError` so failures surface as `is_error=True`; it subclasses the SDK's `ToolError` because from mcp 2.1 an arbitrary tool exception reaches the client as a bare `Error executing tool <name>`
- **Async throughout**: httpx.AsyncClient with an explicit retry loop (budget-bounded, `client/failure_policy.py`), semaphore rate limiting
- **Official MCP SDK**: Tools access lifespan resources via `ctx.request_context.lifespan_context`; HTTP via `streamable_http_app(stateless_http=True, streamable_http_path="/mcp")`

Tools are grouped by `meta` category tags (e.g. `metadata`, `search`, `filter`, `analysis`,
`tracking`, `dates`, `utility`); `get_system_metadata` consolidates six older metadata
tools, and `search_legal_acts` / `browse_acts` default to a limit of 20. `search_legal_acts` validates
`limit`/`offset` strictly (Polish `ValueError` unless `limit` ≥ 1 and `offset` ≥ 0) but deliberately has no
upper clamp on `limit`, because the value reaches `api.sejm.gov.pl` unchanged.

**Key API patterns:**
- ELI identifier: single string `{publisher}/{year}/{pos}` (e.g., `DU/2024/1`)
- Search uses AND logic for keywords — search one keyword at a time for OR behavior

## Conventions

- Toolchain (Python version, build backend, dependencies): `pyproject.toml`
- All tool parameters use `typing.Annotated` with description strings in Polish
- Each tool has `meta` tags for categorization and at least 5 usage examples in docstrings
- Every new tool needs a "Kiedy użyć" / "Kiedy NIE używać" decision tree in its docstring; no test
  enforces it, and five older tools still lack one
- Each tool passes a Polish `title=` and one annotation preset from `tools/annotations.py`
  (`READ_ONLY_REMOTE` when it calls `api.sejm.gov.pl`, `READ_ONLY_LOCAL` otherwise);
  `tests/unit/test_tool_architecture.py` rejects a decorator without both. A tool that
  writes anything needs its own named preset, not a `READ_ONLY_*` one: add a module-level
  `NAME = ToolAnnotations(...)` to `tools/annotations.py` and the architecture test picks it
  up; an inline `ToolAnnotations(...)` in the decorator is rejected
- Tools are thin adapters wrapped in `@handle_tool_errors`: a handler reads the context only through
  `get_app_context`, awaits at most one call and builds no f-string outside a hint; orchestration and
  response text belong in `services/` (`tests/unit/test_tool_architecture.py`)
- Pydantic models for all API responses with `ConfigDict(extra="ignore")`
- Configuration via environment variables with `LAW_MCP_` prefix
- Logging to stderr, format configurable via `LAW_MCP_LOG_FORMAT` (text/json)
- English everywhere except the agent-facing surface: docstrings, code comments (tests included),
  commit messages, PR text, `README.md` and `CHANGELOG.md`. Polish is reserved for strings an
  agent or end user actually reads — tool parameter descriptions, the "Kiedy użyć" trees inside
  tool docstrings, and exception messages. A Polish docstring in `client/` or `services/`, or a
  Polish `CHANGELOG` entry, is a convention violation, not a style preference. Match the file's
  convention, not the nearest neighbouring lines — that is how both drifts started
- asyncio.Lock for all in-memory stores (Cache, DocumentStore, ResultStore)
- Planning workspaces (`context/`, `docs/superpowers/`, `.ai/`) are gitignored — never commit them
  or propose tracking them

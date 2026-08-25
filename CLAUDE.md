# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Law Scrapper MCP v3.1.2 is a modular Python MCP server that exposes 13 tools for searching and analyzing Polish legal acts from the Sejm API (`api.sejm.gov.pl/eli/`). Built with the official Python MCP SDK (`mcp[cli]==2.0.0`, `MCPServer[AppContext]`), it supports STDIO (default) and stateless Streamable HTTP at `/mcp` on port 7683.

## Development commands

```bash
# Install dependencies
uv sync

# Install with dev dependencies
uv sync --extra dev

# Run the server (STDIO - default)
uv run python -m law_scrapper_mcp

# Run with HTTP transport
LAW_MCP_TRANSPORT=streamable-http uv run python -m law_scrapper_mcp

# Run via installed script
law-scrapper

# Run tests
uv run pytest tests/unit/ -v
uv run pytest tests/integration/ -v -m integration

# Run all tests with coverage
uv run pytest --cov=law_scrapper_mcp --cov-report=term-missing

# Quality gates
uv run ruff check src tests
uv run mypy src/law_scrapper_mcp/

# Release management
uv run python scripts/check_release.py --version 2.4.0
# Prepare new release (via skill): /prepare-release X.Y.Z
```

## Testing strategy

Three test layers:

1. **Unit tests** — Models, services, stores, pagination, error handling (`tests/unit/`)
2. **In-memory integration** — Official `mcp.Client` with mocked Sejm API via `respx` (`tests/integration/test_tools_e2e.py`, pagination suites)
3. **Transport integration** — Real STDIO subprocess (`test_stdio_transport.py`), loopback Streamable HTTP (`test_http_transport.py`); CI also runs MCP conformance against `/mcp`

See `tests/TEST_SUITE_SUMMARY.md` for exact commands and file layout.

## Architecture

The project follows a layered architecture with src/ layout:

```
src/law_scrapper_mcp/
├── server.py          # MCPServer, lifespan, transport config, /health endpoint
├── context.py         # AppContext dataclass and get_app_context()
├── config.py          # pydantic-settings configuration
├── logging_config.py  # Structured logging
├── models/            # Pydantic models (enums, API responses, tool I/O, pagination)
├── client/            # HTTP client (httpx), async cache, circuit breaker, exceptions
├── services/          # Business logic, document store, result store, content processor
└── tools/             # 13 MCP tool definitions + error_handling decorator
```

**Key patterns:**
- **Document Store**: Acts loaded into memory for section-level reading and search (asyncio.Lock)
- **Result Store**: Search results persisted for chained filtering (LRU eviction, TTL, asyncio.Lock)
- **Circuit Breaker**: CLOSED → OPEN → HALF_OPEN states protecting Sejm API from cascading failures
- **Enriched responses**: Every tool returns `EnrichedResponse` with hints; native `outputSchema` and object `structuredContent`
- **Pagination**: `PageInfo` model exposed as the `page_info` field with `limit`/`offset` on list and content tools (defaults: 20 items, 10,000 chars; maxima: 100 items, 50,000 chars)
- **TTL cache**: Async API response cache with configurable TTL (metadata=24h, search=10min)
- **Error handling**: `@handle_tool_errors` re-raises `ToolExecutionError` so failures surface as `is_error=True`
- **Async throughout**: httpx.AsyncClient with an explicit retry loop (budget-bounded, `client/failure_policy.py`), semaphore rate limiting, asyncio.Lock
- **Official MCP SDK**: Tools access lifespan resources via `ctx.request_context.lifespan_context`; HTTP via `streamable_http_app(stateless_http=True, streamable_http_path="/mcp")`

**Tool categories** (13 tools total):
- `metadata` — `get_system_metadata` (consolidates 6 old metadata tools)
- `search` — `search_legal_acts`, `browse_acts` (default limit: 20)
- `filter` — `filter_results`, `list_result_sets` (regex, type/status/year match, date ranges, sort, limit)
- `analysis` — `get_act_details`, `read_act_content`, `search_in_act`, `analyze_act_relationships`, `compare_acts`
- `tracking` — `track_legal_changes`
- `dates` — `calculate_legal_date`
- `utility` — `list_loaded_documents`

**Key API patterns:**
- Base URL: `https://api.sejm.gov.pl/eli/`
- ELI identifier: single string `{publisher}/{year}/{pos}` (e.g., `DU/2024/1`)
- Publishers: `DU` (Dziennik Ustaw), `MP` (Monitor Polski)
- Search uses AND logic for keywords — search one keyword at a time for OR behavior

**Entry point:** `pyproject.toml` defines `law-scrapper = "law_scrapper_mcp.server:main"`

## Conventions

- Python 3.13+, managed with `uv` (hatchling build backend, src layout)
- All tool parameters use `typing.Annotated` with description strings in Polish
- Each tool has `meta` tags for categorization and at least 5 usage examples in docstrings
- Each tool has "Kiedy użyć" / "Kiedy NIE używać" decision tree in docstring
- Services layer handles business logic; tools are thin wrappers with `@handle_tool_errors`
- Pydantic models for all API responses with `ConfigDict(extra="ignore")`
- Configuration via environment variables with `LAW_MCP_` prefix
- Logging to stderr, format configurable via `LAW_MCP_LOG_FORMAT` (text/json)
- All exception messages in Polish
- English everywhere except the agent-facing surface: docstrings, code comments (tests included),
  commit messages, PR text, `README.md` and `CHANGELOG.md`. Polish is reserved for strings an
  agent or end user actually reads — tool parameter descriptions, the "Kiedy użyć" trees inside
  tool docstrings, and exception messages. A Polish docstring in `client/` or `services/`, or a
  Polish `CHANGELOG` entry, is a convention violation, not a style preference. Match the file's
  convention, not the nearest neighbouring lines — that is how both drifts started
- asyncio.Lock for all in-memory stores (Cache, DocumentStore, ResultStore)

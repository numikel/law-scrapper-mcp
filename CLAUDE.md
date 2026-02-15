# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Law Scrapper MCP v2.3 is a modular Python MCP server that exposes 13 tools for searching and analyzing Polish legal acts from the Sejm API (`api.sejm.gov.pl/eli/`). Built with FastMCP, it supports STDIO (default) and streamable-http transport on port 7683.

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
uv run ruff check src/
uv run mypy src/law_scrapper_mcp/
```

## Architecture

The project follows a layered architecture with src/ layout:

```
src/law_scrapper_mcp/
├── server.py          # FastMCP app, lifespan, transport config, /health endpoint
├── config.py          # pydantic-settings configuration
├── logging_config.py  # Structured logging
├── models/            # Pydantic models (enums, API responses, tool I/O)
├── client/            # HTTP client (httpx), async cache, circuit breaker, exceptions
├── services/          # Business logic, document store, result store, content processor
└── tools/             # 13 MCP tool definitions + error_handling decorator
```

**Key patterns:**
- **Document Store**: Acts loaded into memory for section-level reading and search (asyncio.Lock)
- **Result Store**: Search results persisted for chained filtering (LRU eviction, TTL, asyncio.Lock)
- **Circuit Breaker**: CLOSED → OPEN → HALF_OPEN states protecting Sejm API from cascading failures
- **Enriched responses**: Every tool returns hints for suggested next steps
- **TTL cache**: Async API response cache with configurable TTL (metadata=24h, search=10min)
- **Error handling**: `@handle_tool_errors` decorator with error classification and traceback logging
- **Async throughout**: httpx.AsyncClient with retry (tenacity), semaphore rate limiting, asyncio.Lock

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
- Each tool has `tags` for categorization and at least 5 usage examples in docstrings
- Each tool has "Kiedy użyć" / "Kiedy NIE używać" decision tree in docstring
- Services layer handles business logic; tools are thin wrappers with `@handle_tool_errors`
- Pydantic models for all API responses with `ConfigDict(extra="ignore")`
- Configuration via environment variables with `LAW_MCP_` prefix
- Logging to stderr, format configurable via `LAW_MCP_LOG_FORMAT` (text/json)
- All exception messages in Polish
- asyncio.Lock for all in-memory stores (Cache, DocumentStore, ResultStore)

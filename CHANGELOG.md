# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `list_loaded_documents` and `list_result_sets` accept `limit`/`offset` and return `page_info` (cluster 2, L3)
- `search_legal_acts` and `browse_acts` return `page_info`; `browse_acts` accepts `offset` (cluster 2, scope extension)
- `MetadataOutput.failed_categories` — an explicit list of metadata categories that could not be fetched (cluster 2, L2)
- `SearchInActOutput.context_chars_requested` / `context_chars_applied`, plus a hint when the context was trimmed (cluster 2, L4)
- `LAW_MCP_API_MAX_ATTEMPTS` (default 3) and `LAW_MCP_API_RETRY_BUDGET` (default 45.0 s) — settings for the API client retry loop (cluster 4)

### Changed
- CI and release workflows bump their pinned GitHub Actions to the current Node 24 runtime: `actions/checkout` 4.2.2→7.0.1, `astral-sh/setup-uv` 5.3.1→10.0.1, `actions/upload-artifact` 4.4.3→7.0.1, `actions/download-artifact` 4.1.9→8.0.1, `softprops/action-gh-release` 2.2.1→3.0.2 (consolidates Dependabot #22-#26)
- `SejmApiClient` splits a request into three layers (`_send` / `_execute_with_resilience` / `_request`); exception translation happens outside the retry loop (cluster 4, F10)
- The circuit breaker gains a `try_acquire` / `release_success` / `release_failure` / `release_probe` contract; the probe counter grows on admission, not on request completion (cluster 4, F21)
- HTTP 500 and 504 raise `ApiUnavailableError` instead of a generic `SejmApiError`, so the exception type matches the breaker's classification. `ApiUnavailableError` inherits from `SejmApiError`, so existing `except` blocks catch exactly as before (cluster 4, F19/D7)
- `search_in_act` builds context and resolves sections only for hits on the current page; section mapping uses binary search (cluster 2, L1)
- `get_system_metadata(category="all")` fetches the five categories concurrently instead of sequentially, within the client semaphore (cluster 2, L2)
- The `context_chars` description in the `search_in_act` `inputSchema` states the 2000-character limit and the effect of exceeding it (cluster 2, L4)

### Fixed
- A failure to fetch a single metadata category no longer silently understates `total_count` (cluster 2, L2)
- `SearchOutput.total_count` is raised to the number of records actually returned when the Sejm API reports a `count` lower than its own response (cluster 2, Task 8)
- 5xx status errors are retried — until now the exception was intercepted before the retry policy could see it, so only timeouts were ever retried (cluster 4, F10)
- Transport errors other than timeouts (`ConnectError`, `ReadError`, `RemoteProtocolError`) are handled and no longer reach the `services/` layer as raw `httpx` exceptions (cluster 4, F11)
- One failed operation increments the breaker counter by 1 rather than by the number of attempts; the retry sequence has a time budget and aborts when concurrent traffic opens the circuit (cluster 4, F20)
- A failure confirmed earlier in a sequence is booked even when the breaker refuses admission for a later attempt — without this, a 5xx observed in HALF_OPEN never drove the circuit back to OPEN (cluster 4, review)
- `httpx.LocalProtocolError` (a malformed request built on our own side) is no longer retried or counted as a breaker failure — a retry would rebuild the same broken request, and the breaker would open against a healthy API (cluster 4, review)
- `LAW_MCP_API_MAX_ATTEMPTS` and `LAW_MCP_API_RETRY_BUDGET` are validated (`ge=1` / `gt=0`); a value of 0 previously produced a silent zero-iteration loop that sent no request at all (cluster 4, review)
- The `User-Agent` header reports the real server version and a contact address instead of a hardcoded `law-scrapper-mcp/2.0`, so the Sejm API administrator can flag a problem by some route other than a ban (cluster 8, F52)

### Removed
- The `tenacity` dependency — an explicit retry loop in `client/sejm_client.py` replaced the `@retry` decorator (cluster 4, D9)
- The `LAW_MCP_API_MAX_RETRIES` setting — it had no production consumer, because the attempt count was hardcoded in the `tenacity` decorator. Use `LAW_MCP_API_MAX_ATTEMPTS` instead; unknown `LAW_MCP_`-prefixed variables are ignored, so setting the old name raises no error (cluster 16, F38)

## [3.0.0] - 2026-08-14

See [docs/changelogs/v3.0.0.md](docs/changelogs/v3.0.0.md) for details.

- **Official Python MCP SDK Migration** — Replaced FastMCP with `mcp[cli]==2.0.0` and `MCPServer[AppContext]`, returning native `structuredContent` and protocol-level `isError` signals.
- **Structured Pagination & Domain Services** — Standardized `PageInfo` pagination across tools; extracted `ComparisonService`, `RelationshipService`, and `DateService`.
- **Regex Security & Bounded Resource Budgets** — Integrated `google-re2` for linear-time pattern matching in `filter_results`, preventing ReDoS vulnerabilities and capping result set sizes.
- **Streamable HTTP Transport Hardening** — Enforced Host/Origin allowlist validation for Streamable HTTP independent of server bind address.

## [2.4.0] - 2026-07-08

### Security

- **FastMCP 2.12.4 → 3.2.0+** — Resolves transitive vulnerabilities in starlette, mcp, authlib, python-multipart, and python-dotenv
- **uv override-dependencies** — Force minimum versions for cryptography (≥48.0.1), urllib3 (≥2.7.0), idna (≥3.15), werkzeug (≥3.1.6), requests (≥2.33.0)
- **pdfplumber 0.11.10+** — Pulls Pillow ≥12.2.0 to address image processing CVEs
- **pytest 9.0.3+** — Dev dependency security update

### Changed

- **FastMCP 3.x migration** — Tools access lifespan resources via `ctx.lifespan_context` (was `ctx.request_context.lifespan_context`)
- **HTTP server startup** — Replaced manual Starlette/uvicorn wiring with `app.run(transport=..., path="/mcp")` and `@app.custom_route("/health")`
- **Integration tests** — FastMCP in-memory `Client` fixture enables end-to-end tool tests without network
- **CI pipeline** — GitHub Actions workflow for ruff, mypy, pytest; Dependabot for pip and github-actions

### Added

- **`tests/unit/test_server.py`** — Verifies 13 tools registered and lifespan service keys
- **`tests/integration/test_http_transport.py`** — ASGI smoke test for `/health` endpoint
- **`.github/workflows/ci.yml`** and **`.github/dependabot.yml`**

## [2.3.1] - 2026-02-20

### Fixed

- **uvx / FastMCP tool registration** — Removed `from __future__ import annotations` from `compare.py` so that parameter type hints are resolved at definition time. Fixes `NameError: name 'Annotated' is not defined` when running via `uvx --from "git+https://github.com/numikel/law-scrapper-mcp" law-scrapper` (Pydantic/FastMCP type adapter evaluation context lacked `Annotated`).

## [2.3.0] - 2026-02-15

### Added

- **`compare_acts` tool** — Compare metadata of two legal acts (titles, types, statuses, dates, keywords overlap and differences)
- **`list_result_sets` tool** — Display active result sets in Result Store memory
- **`list_loaded_documents` tool** — Display loaded documents in Document Store memory
- **`/health` endpoint** — Healthcheck for Docker deployments (streamable-http transport)
- **Circuit breaker** — Protects against cascading failures when Sejm API is unavailable (CLOSED → OPEN → HALF_OPEN states)
- **Default search limit** — `search_legal_acts` and `browse_acts` return max 20 results by default to limit LLM token usage
- **Relationship hints** — `analyze_act_relationships` now returns contextual hints for next steps
- **Decision tree docstrings** — "When to use" / "When NOT to use" sections for search/browse/filter/details/content/compare tools

### Changed

- **Tool count: 10 → 13** — Added `compare_acts`, `list_result_sets`, `list_loaded_documents`
- **Centralized error handling** — `@handle_tool_errors` decorator replaces duplicated try/except in all tools, adds error classification and full traceback for internal errors
- **asyncio.Lock migration** — Cache, DocumentStore, ResultStore use `asyncio.Lock` instead of `threading.Lock` for proper async compatibility
- **Polish error messages** — All exception messages in Polish (ActNotFoundError, DocumentNotLoadedError, InvalidEliError, ResultSetNotFoundError, ContentNotAvailableError)
- **AND logic warning** — Docstring and hints for `search_legal_acts` clearly inform about AND logic for keywords
- **0-results hints** — Enhanced suggestions when search returns no results
- **ELI format standardization** — Consistent `eli` parameter annotations across all tools
- **load_content lifecycle docs** — Documentation of lifecycle: load → TTL 2h → expiration
- **Date parameter docs** — Clarified formats and +/- conventions for days/months/years
- **TTL warning hints** — Hint about TTL after loading a document into memory
- **section_id docs** — Flexible matching info (art_1 and "Art. 1" both work)

### Fixed

- **Healthcheck in Docker** — Dockerfile and docker-compose.yml referenced `/health` which didn't exist
- **Traceback logging** — `logger.error` in tools lost traceback for internal errors

## [2.1.0] - 2026-02-15

### Added

- **`filter_results` tool** - New tool for filtering and narrowing search/browse/changes results using regex patterns, exact match filters, date ranges, sorting and limiting. Works like grep on previously retrieved result sets
- **Result Store service** - In-memory store for search result persistence with LRU eviction (max 20 sets) and TTL (1 hour). Enables chained filtering workflows: search → filter → filter further
- **Result set IDs** - `search_legal_acts`, `browse_acts`, and `track_legal_changes` now return `result_set_id` for use with `filter_results`
- **Flexible date parsing** - `calculate_legal_date` now accepts YYYY, YYYY-MM, and YYYY-MM-DD formats (previously only YYYY-MM-DD)
- **Server instruction workflows** - Comprehensive Polish-language workflow descriptions in MCP server instructions for agent guidance (content reading, advanced search, change analysis, date calculation)

### Fixed

- **Critical: WAF blocking content loading** - HTTP client sent `Accept: application/json` header on `text.html` and `text.pdf` endpoints, causing Sejm API WAF to return "Request Rejected" page (3829 bytes HTML) instead of actual content. Every loaded document was 2406 bytes with 2 sections (the WAF rejection page converted to Markdown). Fixed by overriding `Accept` header in `get_text()` and `get_bytes()` methods
- **PDF extraction "No /Root object" error** - Same WAF issue caused PDF endpoint to return HTML, which pdfplumber couldn't parse. Now returns actual PDF content
- **`year_equals` type validation error** - MCP clients (e.g., Cursor) send integer parameters as strings (`"2024"` instead of `2024`), causing schema validation failure. Changed `year_equals` to accept `str | int | None` with internal conversion
- **`references` field type mismatch** - `ActDetail.references` was `str | None` but API returns `dict[str, Any]`. Fixed to match actual API response format

### Changed

- **Tool count: 9 → 10** - Added `filter_results` as the 10th tool
- **All tool descriptions in Polish** - Parameter annotations, docstrings, examples, and error messages now use Polish with concrete value examples (e.g., `type_equals="Ustawa"`, `status="akt obowiązujący"`) for better LLM discoverability with Polish legal data
- **Polish date pluralization** - `calculate_legal_date` returns properly inflected Polish date descriptions (1 dzień/2 dni/5 dni, 1 miesiąc/2 miesiące/5 miesięcy, 1 rok/2 lata/5 lat)
- **Response enrichment hints in Polish** - All hint messages translated to Polish

## [2.0.0] - 2026-02-14

### Added

- **Modular architecture** - Refactored from monolithic `app.py` to layered `src/` layout with clear separation of concerns
- **Document Store pattern** - Load legal acts into memory for efficient section-level navigation and search without refetching
- **2 new tools** - `search_in_act` and `track_legal_changes` for enhanced legal research workflows
- **Async HTTP client** - Full async/await with httpx, retry logic (tenacity), timeouts, and connection pooling
- **TTL cache** - Intelligent LRU cache for API responses with configurable TTL per endpoint (metadata, search, browse, details, changes)
- **Content processing pipeline** - Automatic HTML-to-Markdown conversion (markdownify) and PDF-to-text extraction (pdfplumber)
- **Enriched responses** - Every tool response includes contextual hints for suggested next steps and related tools
- **Detail level parameter** - New `detail_level` parameter (minimal/standard/full) for search and browse tools to control response verbosity
- **Configuration via environment variables** - All settings use pydantic-settings with `LAW_MCP_` prefix for easy customization
- **Docker support** - Dockerfile and docker-compose.yml for containerized deployment with HTTP/STDIO transport options
- **Structured logging** - JSON and text log format options (configurable via `LAW_MCP_LOG_FORMAT`) for production observability
- **Comprehensive test suite** - pytest, pytest-asyncio, and respx for unit and integration testing
- **Health check capabilities** - Support for containerized deployments with proper startup/shutdown lifecycle management

### Changed

- **Consolidated 14 tools to 9** - Reduced tool count while improving functionality through parameter expansion
  - 6 separate metadata tools merged into single `get_system_metadata(category)` tool
  - `get_current_date` integrated into `calculate_legal_date()` (call with no parameters for current date)
  - `calculate_date_offset` merged into `calculate_legal_date()` with intuitive sign convention (+future, -past)
  - `get_publisher_year_acts` renamed to `browse_acts` for clarity
  - `get_act_comprehensive_details` renamed to `get_act_details` with added `load_content` parameter
  - `get_act_content` renamed to `read_act_content` and requires Document Store pre-loading
  - `get_act_table_of_contents` merged into `get_act_details` response
  - `get_act_relationships` renamed to `analyze_act_relationships` for clarity
- **Synchronous to asynchronous** - Switched from synchronous `requests` library to async `httpx` throughout
- **Transport layer** - Changed default transport from SSE to STDIO; HTTP via streamable-http on port 7683
- **ELI identifier format** - Single string parameter format `"DU/2024/1"` instead of separate `publisher`/`year`/`pos` parameters
- **Date calculation logic** - Intuitive sign convention (+future, -past) instead of inverted subtraction behavior
- **Response structure** - Added `hints` field to all tool responses for better UX and discoverability
- **Server port** - Default HTTP port remains 7683 for streamable-http transport
- **Configuration format** - Environment variables now use `LAW_MCP_` prefix (e.g., `LAW_MCP_API_TIMEOUT`)

### Removed

- **Monolithic single-file architecture** - `app.py` replaced with modular `src/law_scrapper_mcp/` structure
- **6 separate metadata tools** - Consolidated into single `get_system_metadata(category)` tool
- **`get_current_date` tool** - Use `calculate_legal_date()` with no parameters instead
- **SSE transport** - Replaced with STDIO (default) and streamable-http options
- **`logging` package dependency** - Using Python stdlib logging instead for smaller footprint
- **Python 3.12 support** - Minimum version is now 3.13 (for improved async and type hint features)

### Fixed

- API timeout handling with proper circuit breaker patterns
- Memory leaks in Document Store with TTL-based eviction
- Race conditions in concurrent API requests with asyncio.Semaphore
- PDF content extraction with better encoding detection
- Cache invalidation across service layer

### Documentation

- Complete README rewrite with new architecture and 9 tools
- Migration guide from v1.0.2 to v2.0.0 with old→new tool mapping
- Comprehensive Configuration section with all environment variables
- Document Store workflow explanation with usage patterns
- Docker deployment guide with examples
- Development section with test running instructions

## [1.0.2] - 2025-11-09

### Changed

- **Transport migration** - Migrated from STDIO to Server-Sent Events (SSE) transport for better performance and reliability
- **Configuration updates** - Updated all MCP client configurations to use SSE transport
- **Server configuration** - Server now runs on port 7683 with SSE endpoint at `http://localhost:7683/sse`
- Improved LICENSE and README files

## [1.0.1] - 2025-10-17

### Fixed

- Clarified keyword search logic in documentation - all keywords must be present (AND logic) instead of OR logic
- Added detailed notes about keyword search behavior in tool descriptions and examples
- Improved user guidance for multi-keyword searches

## [1.0.0] - 2025-01-17

### Added

#### Dates and time utilities

- `get_current_date` - Get current date in YYYY-MM-DD format for legal document analysis
- `calculate_date_offset` - Calculate dates in the past or future by adding/subtracting time periods for legal document effective dates and deadlines

#### System metadata access

- `get_legal_keywords` - Retrieve all available keywords for categorizing Polish legal acts
- `get_legal_publishers` - Get list of all legal act publishers (Dziennik Ustaw, Monitor Polski) with metadata and publication years
- `get_publisher_details` - Get detailed information about a specific legal publisher including act counts and publication timeline
- `get_legal_statuses` - Get all possible legal act statuses (active, repealed, consolidated, etc.) for document classification
- `get_legal_types` - Retrieve all document types (laws, regulations, ordinances, etc.) used in Polish legal system
- `get_legal_institutions` - Get list of all institutions involved in Polish legal acts (ministries, authorities, organizations)

#### Acts browsing and search

- `search_legal_acts` - Advanced search for Polish legal acts with multiple filters (date, type, keywords, publisher, status)
- `get_publisher_year_acts` - Get all legal acts published by a specific publisher in a given year

#### Act details and analysis

- `get_act_comprehensive_details` - Get complete detailed information about a specific legal act including metadata, status, dates, and references
- `get_act_content` - Retrieve the actual text content of a legal act in PDF or HTML format
- `get_act_table_of_contents` - Get the hierarchical structure and table of contents of a legal act
- `get_act_relationships` - Analyze legal relationships and references for an act (amendments, references, etc.)

### Features

- **Comprehensive legal act access** - Full access to Polish legal acts from Dziennik Ustaw and Monitor Polski
- **Advanced search and filtering** - Multi-criteria search by date, type, keywords, publisher, and status
- **Detailed document analysis** - Complete metadata, structure, references, and content retrieval
- **Date and time utilities** - Specialized date calculations for legal document analysis
- **System metadata access** - Keywords, statuses, document types, and institution data
- **FastMCP integration** - Built with FastMCP framework following best practices
- **Professional documentation** - Extensive examples and clear parameter descriptions
- **RESTful API integration** - Direct connection to official Sejm API endpoints

### Technical

- Initial release with 14 specialized tools organized in 4 categories
- FastMCP framework implementation
- Comprehensive error handling and logging
- Professional code documentation with detailed docstrings
- MCP server configuration for Cursor IDE, Claude Code, and other MCP-supported applications

### Dependencies

- fastmcp>=2.12.4
- logging>=0.4.9.6
- python-dateutil>=2.9.0
- requests>=2.32.5

### Authors

- [@numikel](https://github.com/numikel)

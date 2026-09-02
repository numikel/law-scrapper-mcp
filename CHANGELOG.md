# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [4.2.0] - 2026-09-02

See [docs/changelogs/v4.2.0.md](docs/changelogs/v4.2.0.md) for details.

Sweep of the deferred review findings from clusters 2–9 (GitHub issues #14, #16, #18–#21, #27,
#31, #32, #34, #38–#41, #45–#47, #49, #50, #52, #54, #55). No MCP tool changes its name,
parameter names or response shape; several fields and validations now behave as documented.

- **Egress and download restraint** — `LAW_MCP_API_MAX_SERVER_PAUSE` replaces a fixed constant, the rate settings are bounded (no more `inf`), the pacing deadline and limiter share one clock, and HTML/PDF downloads are aborted mid-stream once they pass the size limit instead of being materialised first.
- **Truthful results** — `effective_date` is populated from `entryIntoForce` after always being `null`; `track_legal_changes` pages upstream and reports its real corpus size instead of labelling a truncated page `complete`; `search_legal_acts` validates `limit`/`offset` and no longer forwards a negative offset upstream.
- **Hardening and hygiene** — bearer mode rejects inert scopes, `[::1].evil.com` no longer passes as loopback, `/health` is metered for non-loopback peers, `httpx` request URLs stay off INFO, `log_level`/`shutdown_grace` fail loudly on bad values, and CI runs the MCP conformance suite for real on the alpha that supports protocol `2026-07-28`.

### Added

- `LAW_MCP_API_MAX_SERVER_PAUSE` (default `60`, at most `600`): the cap on how long a
  server-sent `Retry-After` may hold all outbound traffic, previously a fixed constant. The
  clamp now lives in the rate limiter itself, so every caller of `pause_for()` inherits it (#46,
  #47).
- Document downloads are budgeted while streaming: the client aborts a HTML or PDF body as
  soon as it passes `LAW_MCP_DOC_STORE_MAX_SIZE_BYTES` (or earlier, from `Content-Length`),
  raising the same Polish `ContentTooLargeError` the conversion step already used. The whole
  body is no longer materialised before the size check (#19).
- `search_in_act` builds only the requested page of match positions (`DocumentStore.scan_page`)
  instead of every match in the document; memory is bounded by `limit`, `total_count` stays
  exact (#16).
- CI builds the Docker image and smoke-tests both transports (`/health` over streamable-http, an
  `initialize` round-trip over STDIO), so the image cannot silently stop building again.
- CI runs the MCP conformance suite for real again, on `@modelcontextprotocol/conformance`
  `0.2.0-alpha.11` — the first line that accepts the SDK's protocol era `2026-07-28` — with the
  baseline in `conformance-baseline.yml` (#14).

### Changed

- `search_legal_acts` validates `year`, `limit` and `offset` like every other listing tool: a
  malformed `year`, a malformed or non-positive `limit`, or a malformed or negative `offset`, is
  a tool error with a Polish message instead of silently yielding page one — or, for `year`, a
  search across the publisher's whole corpus. `limit` still has no upper clamp; its
  description now says why (#18, #19).
- `track_legal_changes` sends `limit` and `offset` upstream and reads `totalCount`, so a date
  range wider than one API page is stored as a `page`-scoped set with a truthful
  `corpus_count` instead of being labelled `complete` after silent upstream truncation (#54).
- `DocumentStore.load` refuses a document over the size limit instead of truncating it (the
  branch was unreachable from production since 4.0.0, and its character slice against a byte
  budget was wrong for Polish text) (#32, #21).
- `LAW_MCP_LOG_LEVEL` accepts exactly `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`
  (case-insensitive); `WARN`-style aliases that used to work on STDIO and crash on
  streamable-http are rejected at startup on both. `LAW_MCP_SHUTDOWN_GRACE` is an integer
  (#31).
- `LAW_MCP_AUTH_REQUIRED_SCOPES` with `LAW_MCP_AUTH_MODE=bearer` is rejected at startup: a
  shared secret has no scope semantics, so the setting could never restrict anything. Remove
  the variable or switch to `oauth` (#38).
- `LAW_MCP_API_RATE_PER_SECOND` is bounded to `0.1`–`100` and rejects non-finite values;
  `LAW_MCP_API_RATE_BURST` to `1`–`1000`; `LAW_MCP_API_MAX_ATTEMPTS` to `1`–`20` (#47, #27).
- `/health` is exempt from the per-client rate limiter only for loopback peers; a probe from
  another host is metered like any other request (#39).
- JWKS discovery no longer runs while holding the verifier's lock, so concurrent requests
  during a slow identity-provider response are not serialised behind it (#39).
- `build_http_app()` derives the auth settings and token verifier from the live `settings`
  object, the same place it already reads host and rate-limit settings (#41).
- `uvicorn` is a declared dependency rather than a transitive one (#31).
- Removed two internal methods without production callers: `DocumentStore.search()` and
  `MetadataService.get_metadata()`; `ContentTooLargeError` is exported from the client
  facade (#20, #31).

### Fixed

- A bracketed IPv6 allowlist entry with a trailing slash (`http://[::1]:8080/`) was classified as
  remote after the `[::1].evil.com` tightening, so a value 4.1.0 accepted refused startup under
  `auth_mode=none`; the host parser now drops any path suffix before classifying, for every form.
- The Docker image did not build: the builder stage ran `uv sync --no-editable`, which builds
  the project wheel, without `README.md` in the build context, and hatchling refuses to build
  a package whose declared readme is missing. Broken since the `readme` field was added on
  2026-08-10; surfaced by the Glama indexer. A test now pins that the builder stage copies
  every file the package metadata names.
- `effective_date` in `search_legal_acts`, `browse_acts` and `track_legal_changes` was always
  `null`: the code read a `dateEffect` key that neither list endpoint returns. It now reads
  `entryIntoForce`, the field the API does return, so the `effective_date` filter and sort in
  `filter_results` operate on data (#52).
- A negative `offset` passed to `search_legal_acts` reached `api.sejm.gov.pl` verbatim (#18).
- `httpx`'s request log line carried the full search URL — keywords and title included — at
  INFO, the path the F13 audit finding never covered. The `httpx` and `httpcore` loggers are now
  held at `WARNING` or above (#34).
- `[::1].evil.com` in `LAW_MCP_ALLOWED_HOSTS` was classified as loopback because the bracket
  parser ignored everything after `]`; trailing content other than a port or `:*` now disables
  the loopback match (#38).
- `backoff()` raised `OverflowError` for a large attempt number and returned sub-`base` delays
  for attempts below 1; the exponent is now bounded and computed in floating point (#27).
- `LAW_MCP_API_RATE_PER_SECOND=inf` was accepted and silently disabled pacing (#47).
- The pacing deadline and the rate limiter measured time on different clocks when a limiter
  was injected, which made the deadline bound fail open; both now share the limiter's clock
  (#45).
- `StaticTokenVerifier` accepts no empty secret, so an empty `Authorization` value can never
  match one (#38).
- A section with `end_pos == 0` was treated as open-ended by `section_for_position`;
  overlapping sections are now rejected when a document is loaded (#20).

## [4.1.0] - 2026-09-02

See [docs/changelogs/v4.1.0.md](docs/changelogs/v4.1.0.md) for details.

Result-set scope as first-class contract — a single new field disambiguates complete result sets from windows into larger match sets, closing two categories of false inference over stored results.

### Added

- Result-set scope (`result_set_scope`, and `scope` in `list_result_sets`) telling the caller
  whether a stored set is the complete answer to its query or a window cut from a larger
  corpus. `filter_results` inherits the reach of the set it filtered rather than deriving a
  new one, and reports `source_scope` alongside it.
- `no_match_is_inconclusive` on `filter_results`: set when a filter over a windowed set
  matched nothing, so an empty result is not read as proof that no such act exists.
- Pagination hints now carry the full next call — the tool that produced them plus every
  source criterion, `limit` and `offset` — instead of an unparameterised suggestion.

### Fixed

- `search_legal_acts` reported the size of the returned page as `total_count`, so a search
  matching 1984 acts answered `20` with `was_truncated=false` and no pagination hint at all.
  It now reads `totalCount` and falls back to `count` for responses that omit it (F31).
  Clients that relied on `total_count` carrying the page size will see a different value.
  This is shipped as a fix in a MINOR release, not as a breaking change in a MAJOR one:
  the field always declared the size of the match set and no documentation ever promised
  the page size, so the previous value was a defect rather than a contract.
- The pagination hint from `browse_acts` pointed at `search_legal_acts`, sending the model to
  a different tool than the one it had called (F48).

### Changed

- `search_legal_acts` now sends `limit` upstream even when the caller does not supply one
  (default 20). Without it the API built a page of its own choosing — a measured 709 437 B
  and 500 records for `DU/2024` — of which twenty-four out of twenty-five records were
  discarded locally. An explicit `limit` is still not clamped.
- Large searches now return `was_truncated=true` together with a pagination hint, which is a
  consequence of the `total_count` fix rather than a separate behaviour change.

## [4.0.2] - 2026-09-01

See [docs/changelogs/v4.0.2.md](docs/changelogs/v4.0.2.md) for details.

Outbound politeness towards `api.sejm.gov.pl` — egress rate limiting and concurrent-request budgeting to respect the public API. No MCP tool changes signature or response shape.

### Added

- **Egress rate limiting** — A token bucket now bounds how fast requests leave for the Sejm
  API, on top of the existing concurrency bound. Configurable with
  `LAW_MCP_API_RATE_PER_SECOND` (default `5.0`) and `LAW_MCP_API_RATE_BURST` (default `10`).
  A `Retry-After` sent by the API now pauses the whole client rather than each failing
  request separately, so honouring it no longer ends in a synchronised retry burst. The pause
  is capped at 60 seconds regardless of the header's requested duration, so a large or
  misconfigured `Retry-After` cannot silently wedge every in-flight tool call. Pacing is also
  bounded by `LAW_MCP_API_RETRY_BUDGET`: a call that cannot be paced within its own time
  budget fails immediately with a message naming the pause, instead of waiting out a window
  it was never going to survive and returning a bare client-side timeout.
- **`Retry-After` is now read in both forms RFC 9110 allows.** Only the delta-seconds form was
  understood before; a date-form header — which the WAF in front of the API may send even
  though the API itself does not — was discarded, which silently left the client-wide pause
  switched off. Repeated headers are resolved to the longest wait.
- **Separate concurrency budget for content downloads** — `LAW_MCP_API_MAX_CONCURRENT_CONTENT`
  (default `2`) governs act HTML and PDF downloads, so a run of document fetches can no longer
  occupy every slot and stall concurrent searches.

### Changed

- **`LAW_MCP_API_MAX_CONCURRENT` default lowered from `10` to `8`,** and its meaning narrowed to
  light JSON requests. Together with the new `LAW_MCP_API_MAX_CONCURRENT_CONTENT` of `2` the peak
  concurrency the API sees is unchanged at ten. Deployments that relied on the default now get
  eight light slots plus two heavy ones; set both variables explicitly to restore any other split.
- **`browse_acts` fetches one page instead of a whole year.** It now queries `acts/search` with
  `limit` and `offset` rather than `acts/{publisher}/{year}`, which ignores both and returns the
  full year every time — 1 093 224 B and 1984 records for `DU/2024`, of which a default page kept
  twenty. Results, ordering and response fields are unchanged. A `browse_acts` call with a
  non-numeric `year` now returns a clean tool error instead of silently querying `year=0`. `limit`
  is now clamped to the same maximum of 100 items every other list tool applies, because it reaches
  the API and decides the page width where the year endpoint used to ignore it; a negative `limit`
  or `offset` returns a clean tool error instead of being silently dropped.
- **`search_legal_acts` and `browse_acts` now query the same `acts/search` endpoint,** which makes
  a pre-existing asymmetry newly visible: `search_legal_acts` still reports `total_count` as the
  size of the current page, while `browse_acts` correctly reports the size of the whole year.

## [4.0.1] - 2026-08-31

See [docs/changelogs/v4.0.1.md](docs/changelogs/v4.0.1.md) for details.

Cleanup release — hardening and test coverage from Klaster 7 review findings, no behavior change for existing correct configurations.

### Fixed

- **`trusted_proxies` CIDR validation** — Moved to startup instead of first request; binary `LAW_MCP_AUTH_TOKEN_FILE` files now produce a clean error instead of `UnicodeDecodeError`.
- **OAuth JWKS discovery** — Rejects non-`https://` discovered JWKS URIs; JWKS/IdP communication failures now log at WARNING instead of INFO (including a fix for an except-clause ordering bug).
- **OAuth issuer/JWKS URI scheme** — `LAW_MCP_AUTH_ISSUER` and a directly configured `LAW_MCP_AUTH_JWKS_URI` now must be `https://`, closing a downgrade path the discovery-time check didn't cover.

### Changed

- **Test coverage** — Added token boundary, rate-limit-zero, and timing-safety tests; fixed `caplog` scoping.
- **Dependency and fixture hygiene** — `httpx2` is now an explicit dev dependency with its test client properly closed; the `bearer_app` test fixture no longer mutates global module state.
- **Config module split** — Security-boundary validation extracted from `config.py` into `config_validation.py` and `config_primitives.py`. No behavior change.

## [4.0.0] - 2026-08-31

See [docs/changelogs/v4.0.0.md](docs/changelogs/v4.0.0.md) for details.

### BREAKING CHANGES

- The default HTTP bind moved from `0.0.0.0` to `127.0.0.1`. A deployment that
  relied on the implicit wildcard bind becomes unreachable until `LAW_MCP_HOST`
  is set explicitly.
- Binding beyond the loopback now requires `LAW_MCP_AUTH_MODE` set to `bearer`
  or `oauth`. A container configured with `0.0.0.0` and no token refuses to
  start instead of exposing an unauthenticated MCP endpoint.

### Added

- **Bearer and OAuth authentication** — Streamable HTTP now supports static bearer tokens and OAuth resource-server token verification.
- **Per-client HTTP rate limiting** — Bounds request throughput per client on the HTTP transport.

## [3.1.2] - 2026-08-25

See [docs/changelogs/v3.1.2.md](docs/changelogs/v3.1.2.md) for details.

- **Log output contract** — Log aggregation pipelines parsing this output absorb one field-shape change rather than three:
  - Every record carries a `request_id` field: `lifespan` outside a tool call, an eight-character hexadecimal id inside one.
  - JSON logs keep Polish diacritics literal instead of escaping them to `\uXXXX`.
  - The JSON `timestamp` field ends in an explicit `+00:00` instead of being a naive UTC value.
- **Query text protection** — Query text no longer reaches INFO-level logs through the result store or tool error path; it moves to DEBUG for development diagnostics only.

## [3.1.1] - 2026-08-24

See [docs/changelogs/v3.1.1.md](docs/changelogs/v3.1.1.md) for details.

- **Graceful Shutdown & Lifecycle Control** — Added configurable graceful shutdown window (`LAW_MCP_SHUTDOWN_GRACE`) with custom uvicorn bootstrap on `streamable_http_app()`, plus upstream circuit breaker observability in `/health`.
- **Off-Loop Document Conversion & Oversized Payload Protection** — Moved CPU-intensive document conversion (HTML/PDF) and section indexing off the event loop via `asyncio.to_thread`, and enforced pre/post-conversion size limit validation to reject oversized documents with explicit errors instead of silent truncation.

## [3.1.0] - 2026-08-22

See [docs/changelogs/v3.1.0.md](docs/changelogs/v3.1.0.md) for details.

- **Resilient API Client Architecture** — Replaced `tenacity` with a multi-layered resilience architecture, proper 5xx retry handling, time-budgeted retry loops, and hardened circuit breaker admission contracts.
- **Structured Pagination Across Document & Result Sets** — Added `PageInfo` pagination to `list_loaded_documents`, `list_result_sets`, `search_legal_acts`, and `browse_acts`.
- **Search & Metadata Performance Optimizations** — Implemented binary search section lookup, lazy hit hydration, and concurrent metadata category retrieval.
- **Reliability & Transport Hardening** — Improved transport error translation, accurate User-Agent reporting, and robust `Retry-After` header validation.

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

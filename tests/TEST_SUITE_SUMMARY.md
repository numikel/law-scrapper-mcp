# Law Scrapper MCP test suite

## Overview

Test suite for Law Scrapper MCP v4.2.0, covering models, services, stores, the Sejm API
client and its egress controls, authentication, the HTTP surface, the 13 tools, pagination
contracts, and the real MCP transports.

## Three test layers

| Layer | Scope | Command |
|-------|-------|---------|
| **1. Unit** | Models, services, stores, client, auth, HTTP middleware, config, pagination helpers, error handling | `uv run pytest tests/unit/ -v` |
| **2. In-memory integration** | All 13 tools via the official `mcp.Client` with the Sejm API mocked by `respx` | `uv run pytest tests/integration/test_tools_e2e.py tests/integration/test_content_pagination.py tests/integration/test_result_pagination.py tests/integration/test_listing_pagination.py tests/integration/test_pagination_contract.py -v -m integration` |
| **3. Transport integration** | Real STDIO subprocess, loopback Streamable HTTP, authenticated HTTP | `uv run pytest tests/integration/test_stdio_transport.py tests/integration/test_http_transport.py tests/integration/test_http_auth.py -v -m integration` |

**Full integration suite:**

```bash
uv run pytest tests/integration/ -v -m integration
```

**Coverage (all tests):**

```bash
uv run pytest --cov=law_scrapper_mcp --cov-report=term-missing
```

**Exclude integration tests:**

```bash
uv run pytest tests/ -m "not integration"
```

## Test structure

Every test file, with what it pins. Regenerate the list with
`find tests -name 'test_*.py' | sort` when adding a file, and add its line here.

```
tests/
├── TEST_SUITE_SUMMARY.md       # This file
├── conftest.py                 # Shared fixtures: fixture loaders, mock_client, stores, mcp_client
├── mcp_helpers.py              # parse_tool_result() for structuredContent assertions
├── fixtures/                   # Recorded and derived Sejm API responses
│   ├── act_detail.json, act_structure.json, act_references.json, publishers.json
│   ├── search_results.json     # Derived search page (entryIntoForce keys)
│   ├── browse_page.json        # Recorded acts/search page; browse_page.provenance.md says how
│   ├── search_default_page.provenance.md   # Measurement behind the default page size
│   └── sample_act.html         # HTML act body for the content processor
├── unit/
│   ├── test_act_service_offload.py     # Size limit on act loading, refusal while streaming, conversion off the event loop
│   ├── test_auth_settings_validation.py# Startup validation binding a remote bind address to an auth mode; bearer scope rejection
│   ├── test_cache.py                   # TTLCache: get/set, TTL expiry, LRU eviction, concurrent access
│   ├── test_config.py                  # Settings defaults, env parsing, validation bounds (rate, pause, log_level, shutdown_grace), pattern limits
│   ├── test_config_helpers.py          # `_host_of` and `is_loopback_entry`: schemes, ports, bracketed IPv6
│   ├── test_content_processor.py       # HTML→Markdown, PDF→text, section indexing
│   ├── test_context.py                 # Typed AppContext: every lifespan dependency declared, frozen, reachable via `get_app_context`
│   ├── test_dependency_policy.py       # Runtime deps: only the official MCP SDK, pyjwt and uvicorn declared directly
│   ├── test_deployment_files.py        # Dockerfile/compose/README never reintroduce an unauthenticated exposure
│   ├── test_document_store.py          # DocumentStore: load, TOC, sections, span scan, TTL, LRU, size limit raises
│   ├── test_env_list_parsing.py        # List settings accept both flat comma-separated and JSON-array spellings
│   ├── test_error_handling.py          # `classify_error` / `@handle_tool_errors`: precondition vs upstream vs internal
│   ├── test_health_endpoint.py         # `/health` reports breaker state (unknown, closed, open, half-open) without lying
│   ├── test_jwt_verifier.py            # OAuth 2.1 resource-server mode: signature, audience, issuer, scopes, JWKS discovery
│   ├── test_logging_config.py          # Log contract: UTF-8 stderr, request-id correlation, timestamps, httpx loggers held at WARNING
│   ├── test_models.py                  # ELI parsing, enums, tool I/O models, PageInfo, API response models
│   ├── test_paginated_tool_outputs.py  # Every paginated output model requires `page_info`
│   ├── test_rate_limit.py              # Inbound per-client token bucket in http/: 429 + Retry-After, refill, loopback-only /health exemption
│   ├── test_release_scripts.py         # scripts/git_release_info.py and scripts/check_release.py
│   ├── test_response_enrichment.py     # Search/browse hint generation, pagination hints naming their tool
│   ├── test_server.py                  # 13 tools registered, concrete output schemas, lifespan wiring and configured limits
│   ├── test_server_bootstrap.py        # `build_http_app()` / `build_uvicorn_config()`: security kwargs, auth derived from live settings, grace window
│   ├── test_server_lifespan.py         # Lifespan cleanup survives cancellation and a client whose `close()` raises
│   ├── test_static_token_verifier.py   # Bearer mode: constant-time compare, empty secret refused, prefix rejected
│   ├── test_tool_architecture.py       # Tools are thin adapters: typed context accessor, one awaited call, no user-facing formatting
│   ├── test_tool_descriptions.py       # MCP-visible descriptions: every parameter described, limit semantics documented, typed outputs
│   ├── test_client/
│   │   ├── conftest.py                 # FakeClock + `clock` and `waits` fixtures shared by the egress suites
│   │   ├── test_circuit_breaker.py     # CLOSED → OPEN → HALF_OPEN transitions and probe races
│   │   ├── test_download_budget.py     # Byte budget enforced while a document body streams (#19)
│   │   ├── test_egress_pacing.py       # Limiter wired into the client: 429 → pause, shared clock, light/heavy lanes, deadline refusal
│   │   ├── test_facade_exports.py      # `law_scrapper_mcp.client` re-exports every public exception
│   │   ├── test_failure_policy.py      # Pure retry/breaker classification, Retry-After parsing, `backoff()` bounds
│   │   ├── test_rate_limiter.py        # Outbound token bucket on a fake clock: burst, deficit at large clock magnitudes, pauses, cap, mid-wait extension, cancellation
│   │   ├── test_sejm_client_resilience.py  # Retry loop, breaker integration, slot release, translation to domain errors
│   │   └── test_user_agent.py          # Identity sent to api.sejm.gov.pl tracks the configured version
│   ├── test_services/
│   │   ├── test_act_service.py         # ActService: details, structure, keywords, HTML/PDF content loading
│   │   ├── test_browse_paging.py       # `browse()` fetches one page, reports the year's count, does not re-slice, reads `effective_date` from entryIntoForce
│   │   ├── test_changes_service.py     # ChangesService: date/keyword/publisher params, paged upstream window
│   │   ├── test_comparison_service.py  # ComparisonService: concurrent fetches, sibling cancellation, comparison mapping
│   │   ├── test_content_service.py     # TOC/section/search pagination, naive-implementation equivalence, atomic against reload
│   │   ├── test_date_service.py        # DateService: accepted formats, offsets, Polish error messages, clock seam
│   │   ├── test_metadata_concurrency.py# Metadata categories fetched concurrently, bounded by the client semaphore, ordered, cached
│   │   ├── test_metadata_service.py    # MetadataService pages per category and for `all`
│   │   ├── test_pagination.py          # Shared paginators: effective limit, negative rejection, page metadata
│   │   ├── test_pattern_matching.py    # RE2 pattern compilation: supported subset, rejections, no stderr pollution
│   │   ├── test_relationship_service.py# RelationshipService: category filtering, response-shape normalisation
│   │   ├── test_result_store.py        # ResultStore: ids, LRU, TTL, filtering, ReDoS regression, record cap, scope
│   │   ├── test_search_corpus_size.py  # `search()` reports the corpus size and pays for one page
│   │   ├── test_search_pagination.py   # PageInfo for search and browse windows
│   │   └── test_search_service.py      # SearchService: keyword/date/title/in-force params, detail levels, browse by publisher/year, query summary, default limit
│   └── test_tools/
│       ├── test_dates_tool.py          # `calculate_legal_date` rejects boolean offsets at the tool boundary
│       └── test_search_tool.py         # `search_legal_acts` validates `limit`/`offset` instead of ignoring bad values (#18)
└── integration/
    ├── test_tools_e2e.py               # In-memory Client, all 13 tools, success and `is_error` paths
    ├── test_content_pagination.py      # Section/TOC/search-in-act pagination over the wire
    ├── test_result_pagination.py       # Search/browse/filter/changes pagination over the wire
    ├── test_listing_pagination.py      # `list_loaded_documents` / `list_result_sets` pages
    ├── test_pagination_contract.py     # Every tool classified against the shared pagination contract
    ├── test_stdio_transport.py         # Real STDIO subprocess
    ├── test_http_transport.py          # ASGI app from `build_http_app()`, DNS-rebinding guards, loopback subprocess, /health under load
    └── test_http_auth.py               # Bearer mode over HTTP: 401 without a token, MCP with one, /health stays open
```

## Layer 1: unit tests

- **Models** — ELI parsing, enums, tool I/O, API responses, `PageInfo` validation
- **Services** — Metadata, search, browse, act, changes, comparison, relationship, date, content, pagination, pattern matching
- **Stores** — TTL cache, document store, result store, content processor
- **Client** — Retry loop, failure policy, circuit breaker, outbound rate limiter, download budget, user agent
- **Auth and HTTP** — Static token and JWT verifiers, settings validation, inbound rate limit, `/health`, HTTP bootstrap
- **Server** — 13 tools registered, output schemas, lifespan `AppContext`, cleanup on unclean exit
- **Policy guards** — Dependency policy, deployment files, tool architecture, tool descriptions, release scripts

## Layer 2: in-memory integration

Uses the official MCP Python SDK `Client` against the in-process server with `respx` mocking Sejm API calls.

**Patterns:**
- `parse_tool_result(result)` asserts `is_error is False` and returns `structured_content`
- Failure paths assert `result.is_error is True`
- Stateful workflows: search → filter → load act → read sections → search in content
- Pagination: `page_info` fields on paginated outputs

**Key files:**
- `tests/integration/test_tools_e2e.py`
- `tests/integration/test_content_pagination.py`
- `tests/integration/test_result_pagination.py`
- `tests/integration/test_listing_pagination.py`
- `tests/integration/test_pagination_contract.py`

## Layer 3: transport integration

**STDIO** (`test_stdio_transport.py`):
- Spawns `python -m law_scrapper_mcp` as a subprocess
- Verifies 13 tools listed, success `structured_content`, and `is_error=True` on invalid ELI

**HTTP** (`test_http_transport.py`):
- ASGI smoke tests for `/health` and `/mcp` JSON-RPC
- Loopback subprocess on an ephemeral port with a real `mcp.Client` over Streamable HTTP; the
  subprocess runs with `LAW_MCP_RATE_LIMIT_ENABLED=false` so the per-client bucket never answers a
  protocol probe with a 429
- Asserts `stateless_http=True`, path `/mcp`, protocol version floor
- DNS-rebinding regression tests: `Host` outside the loopback allowlist → `421`, `Origin` outside
  it → `403`, legitimate loopback `Origin` → `200`. The `asgi_app` fixture is the app exactly as
  `build_http_app()` assembles it, bound to `host="0.0.0.0"` (the production default), so these
  fail if `build_http_app()` ever stops passing the explicit `transport_security`
- `/health` stays answerable while a document is converting off the event loop

**Authenticated HTTP** (`test_http_auth.py`):
- `bearer` mode through `build_http_app()`: `/mcp` is 401 without or with a wrong token, lists 13
  tools with the right one, `/health` stays unauthenticated

## MCP conformance (CI only)

`.github/workflows/ci.yml` starts the server with `LAW_MCP_TRANSPORT=streamable-http` on
`127.0.0.1:7683` and runs `@modelcontextprotocol/conformance` against `http://127.0.0.1:7683/mcp`:

- The runner is pinned exactly to `@modelcontextprotocol/conformance@0.2.0-alpha.11`, the first
  release line that accepts the SDK's `LATEST_PROTOCOL_VERSION` (`2026-07-28`); a floating tag
  would make runs irreproducible.
- `--spec-version` is read from `mcp.types.LATEST_PROTOCOL_VERSION` at run time. A `tools-list`
  probe runs first; if the runner answers "Unknown spec version" the suite is skipped with a
  warning rather than run against an older protocol era — the transport tests remain the gate.
- `LAW_MCP_RATE_LIMIT_ENABLED=false` is exported for the run: every scenario comes from one
  loopback peer, and the inbound bucket would answer the sweep with 429s that read as protocol
  failures. Throttling has its own tests (`tests/unit/test_rate_limit.py`).
- Known gaps (optional surfaces this tools-only server does not implement: resources, prompts,
  sampling, elicitation, multi-modal results) are listed in `conformance-baseline.yml` and passed
  as `--expected-failures`; only the "active" suite is gated, and the baseline header records the
  pending-suite failures that are not.

## Shared fixtures (`tests/conftest.py`)

- `fixtures_dir`, `sample_act_html`, `search_results`, `act_detail`, `act_structure`,
  `act_references`, `publishers_data` — loaders for `tests/fixtures/`
- `cache`, `mock_client` — `TTLCache` and a started `SejmApiClient` for `respx`-mocked calls
- `document_store`, `content_processor`
- `mock_api_responses` — routes every fixture onto the Sejm API URLs
- `anyio_backend`, `mcp_client` — official in-memory `Client` (anyio-backed integration tests)

Package-local fixtures: `tests/unit/test_client/conftest.py` provides `FakeClock`, `clock` and
`waits` for the egress suites — `waits` patches both waiting points (`rate_limiter._wait` and
`sejm_client._delay`), advances the fake clock by each requested wait and yields to the loop once,
so no client test measures real elapsed time.

## Test patterns

### Async testing

`asyncio_mode = "strict"`, so every async test is marked. Unit tests use `pytest.mark.asyncio`
(usually as a module-level `pytestmark`). Tests that drive the in-memory `mcp.Client` use
`@pytest.mark.anyio` — the whole integration suite plus the tool-boundary unit tests in
`tests/unit/test_tools/` and `tests/unit/test_tool_descriptions.py`. Time-dependent code (rate
limiters, breaker, cache) is driven by an injected clock or a patched `monotonic`, never by
sleeping.

### HTTP mocking

```python
@respx.mock
async def test_search_basic(self, service: SearchService):
    respx.get("https://api.sejm.gov.pl/eli/acts/search").mock(
        return_value=Response(200, json=search_results)
    )
```

### Structured content assertions

```python
from mcp_helpers import parse_tool_result

result = await client.call_tool("calculate_legal_date", {"days": 1})
payload = parse_tool_result(result)
assert payload["data"]["calculated_date"] == "2026-01-02"
```

## CI integration

GitHub Actions (`.github/workflows/ci.yml`), `lint` and `test` jobs:

```yaml
- name: Reject legacy framework remnants
- name: Ruff check
- name: Ruff format
- name: Mypy
- name: Unit tests
- name: Integration tests
- name: Protocol transport tests
- name: MCP conformance
```

A `release-prep-check` job requires `docs/changelogs/v<version>.md` whenever `pyproject.toml`'s
version changes, and an `audit` job runs `pip-audit`.
A `docker` job builds the image and smoke-tests it: `/health` over streamable-http with a throwaway
bearer token, one `initialize` round-trip over STDIO (`LAW_MCP_TRANSPORT=stdio` overriding the
image default), and a non-root check.

## Maintenance

- Keep fixtures aligned with Sejm API response formats; recorded fixtures carry a `.provenance.md`
- Add regression tests for protocol and pagination contracts
- Update transport tests and the conformance pin when the MCP SDK or protocol version changes
- Add every new test file to the structure list above

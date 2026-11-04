# Law Scrapper MCP test suite

## Overview

Test suite for Law Scrapper MCP v4.4.2, covering models, services, stores, the Sejm API
client and its egress controls, authentication, the HTTP surface, the 13 tools, pagination
contracts, and the real MCP transports.

## Three test layers

| Layer | Scope | Command |
|-------|-------|---------|
| **1. Unit** | Models, services, stores, client, auth, HTTP middleware, config, pagination helpers, error handling | `uv run pytest tests/unit/ -v` |
| **2. In-memory integration** | All 13 tools via the official `mcp.Client` with the Sejm API mocked by `respx` | `uv run pytest tests/integration/test_tools_e2e.py tests/integration/test_content_failure_contract.py tests/integration/test_content_pagination.py tests/integration/test_result_pagination.py tests/integration/test_listing_pagination.py tests/integration/test_pagination_contract.py tests/integration/test_output_contract.py -v -m integration` |
| **3. Transport integration** | Real STDIO subprocess, loopback Streamable HTTP, authenticated HTTP | `uv run pytest tests/integration/test_stdio_transport.py tests/integration/test_http_transport.py tests/integration/test_http_auth.py -v -m integration` |

**Source modules (outside `tests/`).** Modules live in `modules/<source>/` as uv workspace members;
their tests sit beside them and run against the really installed distribution. A plain
`uv sync --extra dev` does not install a member; `--all-packages` does. The root suite above stays
core-only either way (`LAW_MCP_SOURCE_MODULES=-*`, see `tests/conftest.py`).

```bash
uv sync --extra dev --all-packages
uv run pytest modules/reference/tests -v
uv run pytest modules/voivodeship-journals/tests -v
```

Several members can be installed at once and test directories are collected in one run
(`pytest modules/*/tests`), so test file names are unique across members and the reference tests
derive their tool counts from a run with `reference` switched off instead of hard-coding them.
`tests/unit/test_ci_module_gates.py` also pins the CI step that runs the MCP conformance suite with
a module active in the `modules` job (same pinned command as the core-only one in the `test` job).

| File | What it pins |
|------|--------------|
| `modules/reference/tests/test_install_and_disable.py` | The entry point of the installed distribution, discovery over it, 14 vs 13 tools and the instructions header on a real STDIO server for `unset`, `-reference`, `-*`, `-*,reference`, `reference,-reference`, `reference`; a switched-off module's package is never imported |
| `modules/reference/tests/test_reference_tool.py` | The descriptor, the state accessor, and `reference_ping` through an in-memory client with `outputSchema` validation |
| `modules/voivodeship-journals/tests/journal_support.py`, `conftest.py` | Not tests: the recorded-payload loaders, host constants, a synthetic one-page PDF builder, the in-memory MCP session helper, the `started` fixture (the real start hook over a fresh hub and registry) and the autouse `no_real_network` guard that makes a name lookup for a non-local host fail |
| `modules/voivodeship-journals/tests/test_catalog.py` | 16 journals, 12 served, 4 withheld with a stated reason, hosts agree with the recordings, import-time invariants, the module README table matches the catalogue |
| `modules/voivodeship-journals/tests/test_normalise.py` | Both JSON casings, the date swap between search and details, sentinels, the canonical ELI, relations under the journal's own keys |
| `modules/voivodeship-journals/tests/test_source.py` | Search parameters exactly (no `publisher`, `limit` always sent), each refusal before any request, withheld and unknown codes, 204 as not found, no `/struct`, relations, PDF download and its size budget |
| `modules/voivodeship-journals/tests/test_journals_end_to_end.py` | The tools through an in-memory client over the real server with only this module active and every journal host mocked: browse, filter, load, read, search in an act; search params and ISO dates; a Pascal journal; change tracking by publication window; relations; `compare_acts`; a scan (`unavailable` plus the journal PDF link); a garbled layer that still loads; eight refusals in Polish that cost no request; `outputSchema` validated on every answer; a request to the Sejm fails the test |
| `modules/voivodeship-journals/tests/test_journals_isolation.py` | A 503 storm on one journal opens only its own breaker (read from the list `/health` serves), names that source and says the others are unaffected; another journal and DU still answer; an open breaker sends nothing; no limiter or breaker is shared with any other host |
| `modules/voivodeship-journals/tests/test_journals_install_and_disable.py` | Real STDIO server: the module block is present for `unset`, `-*,voivodeship-journals`, a bare name and `-reference`, and the tool set equals the same configuration with this module off (it adds none); absent for `-voivodeship-journals`, `-*`, `voivodeship-journals,-*`; the package is never imported when off |
| `modules/voivodeship-journals/tests/test_start_and_health.py` | The start hook: clients only through the hub, 12 hosts labelled by code, registration and unregistration (also on failure), a second start refused, module identity in the `User-Agent`, the entry point of the installed distribution, a positive control for the offline guard |

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
├── conftest.py                 # Shared fixtures: fixture loaders, mock_client, stores, mcp_client; sets `LAW_MCP_SOURCE_MODULES=-*` above the first `law_scrapper_mcp` import
├── mcp_helpers.py              # parse_tool_result() for structuredContent assertions; `EXPECTED_TOOLS`; `CORE_ONLY_ENV` (the `-*` value the root suite and its server subprocesses run with)
├── fake_source_modules.py      # In-memory source-module descriptors and spy entry points for the module-mechanism unit tests
├── fake_act_source.py          # In-memory `ActSource` that records its calls; drives the act-source routing tests
├── fixtures/                   # Recorded and derived Sejm API responses
│   ├── act_detail.json, act_structure.json, act_references.json, publishers.json
│   ├── search_results.json     # Derived search page (entryIntoForce keys)
│   ├── browse_page.json        # Recorded acts/search page; browse_page.provenance.md says how
│   ├── search_default_page.provenance.md   # Measurement behind the default page size
│   ├── edziennik/              # 14 recorded e-Dziennik ELI files (JSON, HTML, PDF), three of them the keyword-list probe; one PROVENANCE.md for all sessions
│   ├── saos/                   # 25 recorded SAOS API files (search pages, counts, an error body, the court dictionary) with personal names redacted; one PROVENANCE.md; no full judgment texts
│   └── sample_act.html         # HTML act body for the content processor
├── unit/
│   ├── test_act_service_offload.py     # Size limit on act loading, refusal while streaming, conversion off the event loop
│   ├── test_auth_settings_validation.py# Startup validation binding a remote bind address to an auth mode; bearer scope rejection
│   ├── test_cache.py                   # TTLCache: get/set, TTL expiry, LRU eviction, concurrent access
│   ├── test_ci_module_gates.py         # Module code gets core's gates: the `changes` filter covers `modules/**`, `lint` and `modules` jobs in ci.yml, the `test` job stays core-only, every action SHA-pinned, `check_release.py` mirrors the module commands
│   ├── test_config.py                  # Settings defaults, env parsing, validation bounds (rate, pause, log_level, shutdown_grace), pattern limits
│   ├── test_config_helpers.py          # `_host_of` and `is_loopback_entry`: schemes, ports, bracketed IPv6
│   ├── test_content_processor.py       # HTML→Markdown, PDF→text, section indexing
│   ├── test_context.py                 # Typed AppContext: every lifespan dependency declared, frozen, reachable via `get_app_context`; `module_state` is the last, defaulted field
│   ├── test_dependency_policy.py       # Runtime deps: only the official MCP SDK, pyjwt and uvicorn declared directly
│   ├── test_deployment_files.py        # Dockerfile/compose/README never reintroduce an unauthenticated exposure
│   ├── test_document_store.py          # DocumentStore: load, TOC, sections, span scan, TTL, LRU, size limit raises
│   ├── test_edziennik_fixtures.py      # Facts about the e-Dziennik journals pinned to `fixtures/edziennik/`: casing, dates swapped by search only, sentinels, ignored publisher code, scan vs text PDF, catalogue vs PROVENANCE.md
│   ├── test_env_list_parsing.py        # List settings accept both flat comma-separated and JSON-array spellings
│   ├── test_error_handling.py          # `classify_error` / `@handle_tool_errors`: categories, guidance, truncation keeping a trailing URL, ERROR/DEBUG split incl. the chained cause
│   ├── test_health_endpoint.py         # `/health` reports breaker state (unknown, closed, open, half-open) without lying
│   ├── test_jwt_verifier.py            # OAuth 2.1 resource-server mode: signature, audience, issuer, scopes, JWKS discovery
│   ├── test_logging_config.py          # Log contract: UTF-8 stderr, request-id correlation, timestamps, httpx loggers held at WARNING
│   ├── test_models.py                  # ELI parsing, enums, tool I/O models, PageInfo, API response models
│   ├── test_paginated_tool_outputs.py  # Every paginated output model requires `page_info`
│   ├── test_rate_limit.py              # Inbound per-client token bucket in http/: 429 + Retry-After, refill, loopback-only /health exemption
│   ├── test_readme_tool_table.py       # README tool-properties table matches the live `tools/list` (title, readOnlyHint, openWorldHint)
│   ├── test_release_scripts.py         # scripts/git_release_info.py and scripts/check_release.py
│   ├── test_response_enrichment.py     # Search/browse hint generation, pagination hints naming their tool
│   ├── test_saos_fixtures.py           # Facts about the SAOS case-law API pinned to `fixtures/saos/`: dates that sort first, whole-name division filter, SENTENCE/REASONS pairs, court dictionary codes, 400 for pageSize 101, judge names and names after role cues redacted, catalogue and stored-file hashes vs PROVENANCE.md
│   ├── test_server.py                  # 13 tools registered, concrete output schemas, lifespan wiring and configured limits
│   ├── test_server_bootstrap.py        # `build_http_app()` / `build_uvicorn_config()`: security kwargs, auth derived from live settings, grace window
│   ├── test_server_lifespan.py         # Lifespan cleanup survives cancellation and a client whose `close()` raises
│   ├── test_source_module_activation.py # `LAW_MCP_SOURCE_MODULES`: syntax errors, left-to-right evaluation (the grammar table, order, the bare-name risk pin, the `-*` image-default pin), unknown names refused only while still enabled and reported otherwise
│   ├── test_source_module_discovery.py # `discover()`: only active entry points are imported, descriptor validation, clashes, real distribution metadata via a temporary dist-info, `registered_tool_names` guard
│   ├── test_source_module_instructions.py # Pre-change server instructions pinned byte for byte; modules raise the header count and append one block each
│   ├── test_source_module_isolation.py # The root suite is core-only: `os.environ` holds exactly `-*`, the live `settings` were built with it, and no entry point installed in this environment ends up active
│   ├── test_source_module_server.py    # `build_app` tool-name checks, module start/stop order around the egress hub, `module_state`, startup INFO and WARNING lines
│   ├── test_static_token_verifier.py   # Bearer mode: constant-time compare, empty secret refused, prefix rejected
│   ├── test_tool_architecture.py       # Tools are thin adapters: typed context accessor, one awaited call, no user-facing formatting; every `@mcp.tool` carries a `title=` and an annotation preset; imports point only down the `models` → `client` → `services` → `tools` layers; the same rules run over every `modules/*/src/law_scrapper_mcp_*/tools/` package, module tools may not import `httpx` or the core `client/`, and the core may not import a `law_scrapper_mcp_*` package
│   ├── test_tool_descriptions.py       # MCP-visible descriptions: every parameter described, limit semantics documented, typed outputs
│   ├── test_client/
│   │   ├── conftest.py                 # FakeClock + `clock` and `waits` fixtures shared by the egress suites
│   │   ├── test_circuit_breaker.py     # CLOSED → OPEN → HALF_OPEN transitions and probe races
│   │   ├── test_download_budget.py     # Byte budget enforced while a document body streams (#19)
│   │   ├── test_egress_construction_guard.py # `httpx.AsyncClient` is built only in `client/egress.py` (one listed exception: OIDC discovery in `auth/jwt_verifier.py`); scans `src/law_scrapper_mcp/` and `modules/`, and fails if a root holds no `.py` file
│   │   ├── test_egress_hub.py          # EgressHub: caller identity tokens, wire User-Agent, one owner per origin and name, rate ceiling a module can only lower, no pin opt-out, source states, close error collection
│   │   ├── test_egress_isolation.py    # Per-host controls: cache namespace, origin pin and redirect cap (Sejm characterised as unpinned), host label on errors and logs, two-host isolation of 429 pause, breaker, bucket, headers and cookies
│   │   ├── test_egress_messages.py     # Generic Polish client-side texts of a host with no preset, named after the source
│   │   ├── test_egress_no_content.py   # `get_json(none_on_no_content=True)`: 204 returns None, is never cached, and the default is unchanged
│   │   ├── test_egress_pacing.py       # Limiter wired into the client: 429 → pause, shared clock, light/heavy lanes, deadline refusal
│   │   ├── test_facade_exports.py      # `law_scrapper_mcp.client` re-exports every public exception
│   │   ├── test_failure_policy.py      # Pure retry/breaker classification, Retry-After parsing, `backoff()` bounds
│   │   ├── test_rate_limiter.py        # Outbound token bucket on a fake clock: burst, deficit at large clock magnitudes, pauses, cap, mid-wait extension, cancellation
│   │   ├── test_sejm_client_resilience.py  # Retry loop, breaker integration, slot release, translation to domain errors (transport message names class + endpoint, never httpx text)
│   │   ├── test_sejm_message_texts.py  # Golden literals of every error text the Sejm client raises, pinned before the transport was extracted
│   │   └── test_user_agent.py          # Identity sent to api.sejm.gov.pl tracks the configured version
│   ├── test_services/
│   │   ├── test_act_service.py         # ActService: details, structure, keywords, HTML/PDF content loading
│   │   ├── test_act_sources.py         # ActSourceRegistry: exact code before prefix, reserved DU/MP, conflicts leave nothing half registered, unregister
│   │   ├── test_browse_paging.py       # `browse()` fetches one page, reports the year's count, does not re-slice, reads `effective_date` from entryIntoForce
│   │   ├── test_changes_service.py     # ChangesService: date/keyword/publisher params, paged upstream window
│   │   ├── test_comparison_service.py  # ComparisonService: concurrent fetches, sibling cancellation, comparison mapping
│   │   ├── test_content_load_failures.py # Transient loading failures propagate; permanent absence (no format, 404, empty extraction) is a documented `unavailable`, not a placeholder
│   │   ├── test_content_service.py     # TOC/section/search pagination, naive-implementation equivalence, atomic against reload
│   │   ├── test_date_service.py        # DateService: accepted formats, offsets, Polish error messages, clock seam
│   │   ├── test_metadata_concurrency.py# Metadata categories fetched concurrently, bounded by the client semaphore, ordered, cached
│   │   ├── test_metadata_service.py    # MetadataService pages per category and for `all`
│   │   ├── test_pagination.py          # Shared paginators: effective limit, negative rejection, page metadata
│   │   ├── test_pdf_inspection.py      # `inspect_pdf`: scan vs failed read vs text layer vs `(cid:N)`-dominated layer (flagged, never discarded)
│   │   ├── test_source_dispatch.py     # Empty registry issues the 4.4.2 upstream calls; a registered code never reaches the Sejm: search (limit 101 rejected), browse, change tracking, details, relations, PDF-only loading and its outcomes
│   │   ├── test_pattern_matching.py    # RE2 pattern compilation: supported subset, rejections, no stderr pollution
│   │   ├── test_relationship_service.py# RelationshipService: category filtering, response-shape normalisation
│   │   ├── test_result_store.py        # ResultStore: ids, LRU, TTL, filtering, ReDoS regression, record cap, scope
│   │   ├── test_search_corpus_size.py  # `search()` reports the corpus size and pays for one page
│   │   ├── test_search_pagination.py   # PageInfo for search and browse windows
│   │   └── test_search_service.py      # SearchService: keyword/date/title/in-force params, detail levels, browse by publisher/year, query summary, default limit
│   └── test_tools/
│       ├── test_act_details_tool.py    # `get_act_details` never hints a PDF URL the server's own metadata proves absent
│       ├── test_dates_tool.py          # `calculate_legal_date` rejects boolean offsets at the tool boundary
│       └── test_search_tool.py         # `search_legal_acts` validates `limit`/`offset` instead of ignoring bad values (#18)
└── integration/
    ├── test_tools_e2e.py               # In-memory Client, all 13 tools, success and `is_error` paths
    ├── test_content_failure_contract.py # Transient content-loading failures are protocol errors (`isError=true`), never a success body
    ├── test_content_pagination.py      # Section/TOC/search-in-act pagination over the wire
    ├── test_result_pagination.py       # Search/browse/filter/changes pagination over the wire
    ├── test_listing_pagination.py      # `list_loaded_documents` / `list_result_sets` pages
    ├── test_pagination_contract.py     # Every tool classified against the shared pagination contract
    ├── test_output_contract.py         # structuredContent validates against outputSchema for all 13 tools (jsonschema, Draft 2020-12); the text block is the full JSON copy (spec D6); `openWorldHint` matches upstream traffic on a cold and a warm server
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
- `tests/integration/test_content_failure_contract.py`
- `tests/integration/test_content_pagination.py`
- `tests/integration/test_result_pagination.py`
- `tests/integration/test_listing_pagination.py`
- `tests/integration/test_pagination_contract.py`
- `tests/integration/test_output_contract.py`

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

The `lint` job installs with `--all-packages` and covers `modules/` with ruff and mypy. A `modules`
job installs the workspace members, runs `pytest modules/*/tests` and the root unit suite once with a
member installed; the `test` job stays on plain `uv sync --extra dev`, so the conformance server and
the transport tests see the core only.

A `release-prep-check` job requires `docs/changelogs/v<version>.md` whenever `pyproject.toml`'s
version changes, and an `audit` job runs `pip-audit` from the dev extra (`uv run pip-audit` locally).
A `docker` job builds the image and smoke-tests it: `/health` over streamable-http with a throwaway
bearer token, one `initialize` round-trip over STDIO (`LAW_MCP_TRANSPORT=stdio` overriding the
image default), and a non-root check.

## Maintenance

- Keep fixtures aligned with Sejm API response formats; recorded fixtures carry a `.provenance.md`
  (recordings made in one session may share a single `PROVENANCE.md` per directory, as in `fixtures/edziennik/`)
- Add regression tests for protocol and pagination contracts
- Update transport tests and the conformance pin when the MCP SDK or protocol version changes
- Add every new test file to the structure list above

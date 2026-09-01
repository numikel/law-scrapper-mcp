# Law Scrapper MCP test suite

## Overview

Comprehensive test suite for Law Scrapper MCP v3.0.0, covering models, services, tools, pagination, and real MCP transports.

## Three test layers

| Layer | Scope | Command |
|-------|-------|---------|
| **1. Unit** | Models, services, stores, pagination helpers, error handling | `uv run pytest tests/unit/ -v` |
| **2. In-memory integration** | All 13 tools via official `mcp.Client` with mocked Sejm API (`respx`) | `uv run pytest tests/integration/test_tools_e2e.py tests/integration/test_content_pagination.py tests/integration/test_result_pagination.py -v -m integration` |
| **3. Transport integration** | Real STDIO subprocess and loopback Streamable HTTP | `uv run pytest tests/integration/test_stdio_transport.py tests/integration/test_http_transport.py -v -m integration` |

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

```
tests/
├── fixtures/              # JSON/HTML sample API responses
├── conftest.py            # Shared fixtures (mock client, document store, etc.)
├── mcp_helpers.py         # parse_tool_result() for structuredContent assertions
├── unit/                  # Unit tests
│   ├── test_models.py
│   ├── test_cache.py
│   ├── test_config.py
│   ├── test_context.py
│   ├── test_paginated_tool_outputs.py
│   ├── test_server.py
│   ├── test_dependency_policy.py
│   ├── test_client/
│   │   ├── test_circuit_breaker.py
│   │   ├── test_failure_policy.py
│   │   ├── test_sejm_client_resilience.py
│   │   ├── test_rate_limiter.py    # Outbound token bucket, fake clock
│   │   ├── test_egress_pacing.py   # Limiter + light/heavy lanes in the client
│   │   └── test_user_agent.py      # Identity sent to api.sejm.gov.pl
│   └── test_services/
└── integration/           # MCP protocol integration tests
    ├── test_tools_e2e.py           # In-memory Client, all 13 tools
    ├── test_content_pagination.py  # Section/search pagination
    ├── test_result_pagination.py   # Search/browse/changes pagination
    ├── test_stdio_transport.py     # Real STDIO subprocess
    └── test_http_transport.py      # ASGI + loopback HTTP subprocess
```

## Layer 1: unit tests

- **Models** — ELI parsing, enums, tool I/O, API responses, `PageInfo` validation
- **Services** — Metadata, search, act, changes, comparison, relationship, date, pagination, pattern matching
- **Infrastructure** — TTL cache, document store, result store, content processor, config, error handling
- **Server** — 13 tools registered, lifespan `AppContext` keys
- **Dependency policy** — `mcp[cli]==2.0.0` pinned; no legacy framework in runtime deps

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

## Layer 3: transport integration

**STDIO** (`test_stdio_transport.py`):
- Spawns `python -m law_scrapper_mcp` as a subprocess
- Verifies 13 tools listed, success `structured_content`, and `is_error=True` on invalid ELI

**HTTP** (`test_http_transport.py`):
- ASGI smoke tests for `/health` and `/mcp` JSON-RPC
- Loopback subprocess on ephemeral port with real `mcp.Client` over Streamable HTTP
- Asserts `stateless_http=True`, path `/mcp`, protocol version floor
- DNS-rebinding regression tests: `Host` outside the loopback allowlist → `421`, `Origin` outside it → `403`, legitimate loopback `Origin` → `200` — the `asgi_app` fixture builds with `host="0.0.0.0"` (the production default) and the same explicit `transport_security` `server.main()` passes, so these fail if that wiring is ever dropped

**CI conformance** (`.github/workflows/ci.yml`):
- Starts server with `LAW_MCP_TRANSPORT=streamable-http`
- Runs `@modelcontextprotocol/conformance` against `http://127.0.0.1:7683/mcp`

## Shared fixtures (`tests/conftest.py`)

- `fixtures_dir`, `sample_act_html`, `search_results`, `act_detail`, etc.
- `mock_client` — Mocked `SejmApiClient` with `respx`
- `document_store`, `content_processor`, `cache`
- `mcp_client` — Official in-memory `Client` (anyio-backed integration tests)

## Test patterns

### Async testing

Unit async tests use `pytest.mark.asyncio` (`asyncio_mode = "strict"`). Transport and in-memory MCP client tests use `@pytest.mark.anyio`.

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
from tests.mcp_helpers import parse_tool_result

result = await client.call_tool("calculate_legal_date", {"days": 1})
payload = parse_tool_result(result)
assert payload["data"]["calculated_date"] == "2026-01-02"
```

## CI integration

GitHub Actions (`.github/workflows/ci.yml`):

```yaml
- name: Reject legacy framework remnants
- name: Unit tests
- name: Integration tests
- name: Protocol transport tests
- name: MCP conformance
```

## Maintenance

- Keep fixtures aligned with Sejm API response formats
- Add regression tests for protocol and pagination contracts
- Update transport tests when MCP SDK or protocol version changes

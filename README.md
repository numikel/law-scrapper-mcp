# Law Scrapper MCP

A comprehensive Model Context Protocol (MCP) server for accessing and analyzing Polish legal acts from the Sejm API, enabling AI-powered legal research and document analysis.

![Python version](https://img.shields.io/badge/python-3.13+-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Version](https://img.shields.io/badge/version-4.0.0-orange.svg)

<a href="https://glama.ai/mcp/servers/@numikel/law-scrapper-mcp">
  <img width="380" height="200" src="https://glama.ai/mcp/servers/@numikel/law-scrapper-mcp/badge" alt="Law Scrapper MCP server" />
</a>

## Features

- **Comprehensive legal act access** - Full access to Polish legal acts from Dziennik Ustaw (DU) and Monitor Polski (MP)
- **Advanced search and filtering** - Multi-criteria search by date, type, keywords, publisher, and status
- **Result Store with chained filtering** - Store search results and filter with regex, type/status/year match, date ranges, sorting
- **Document Store pattern** - Load acts into memory for efficient section-level navigation and search
- **Detailed document analysis** - Metadata, structure, references, and content retrieval
- **Content processing** - Automatic PDF-to-text and HTML-to-Markdown conversion
- **Date calculations** - Specialized date utilities for legal document analysis
- **System metadata** - Keywords, statuses, document types, and institution data
- **Official MCP SDK** - Built with `mcp[cli]==2.0.0` and `MCPServer`; STDIO and stateless Streamable HTTP transports
- **Async HTTP client** - Efficient httpx client with retry logic and connection pooling
- **TTL caching** - Intelligent response caching with configurable TTL
- **Structured logging** - JSON and text log formats for easy debugging
- **Docker support** - Containerized deployment with docker-compose
- **Comprehensive documentation** - Examples and clear parameter descriptions

## Requirements

- **Python**: 3.13 or higher
- **Package manager**: uv (recommended) or pip
- **Internet connection**: Required for accessing Sejm API endpoints
- **MCP-compatible tool**: Cursor IDE, Claude Code, or other MCP clients

## Installation

### Using uv (recommended)

```bash
# Clone the repository
git clone https://github.com/numikel/law-scrapper-mcp.git
cd law-scrapper-mcp

# Install dependencies
uv sync

# Install with dev dependencies
uv sync --extra dev
```

### Using pip

```bash
# Clone the repository
git clone https://github.com/numikel/law-scrapper-mcp.git
cd law-scrapper-mcp

# Install dependencies
pip install -e .
```

### Using uvx (no installation required)

For quick testing without cloning the repository:

```bash
# Run the server directly from GitHub
uvx --from git+https://github.com/numikel/law-scrapper-mcp law-scrapper
```

## Quick start

### STDIO transport (default)

STDIO is the default transport for MCP communication. Start the server and connect from your MCP client:

```bash
# Run the server
uv run python -m law_scrapper_mcp

# Or use the installed script
law-scrapper
```

Configure in your MCP client (e.g., Cursor `.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "law-scrapper-mcp": {
      "command": "law-scrapper"
    }
  }
}
```

For Claude Code:

```bash
claude mcp add law-scrapper "uvx --from git+https://github.com/numikel/law-scrapper-mcp law-scrapper"
```

### HTTP transport (streamable-http)

Run the server on HTTP with streamable-http transport:

```bash
# Run with HTTP transport on port 7683
LAW_MCP_TRANSPORT=streamable-http uv run python -m law_scrapper_mcp

# Or specify a custom port (the host stays on loopback unless you configure
# an authentication mode — see "Authenticated remote deployment" below)
LAW_MCP_TRANSPORT=streamable-http LAW_MCP_PORT=8080 uv run python -m law_scrapper_mcp
```

Configure in your MCP client:

```json
{
  "mcpServers": {
    "law-scrapper-mcp": {
      "url": "http://localhost:7683/mcp",
      "transport": "streamable-http"
    }
  }
}
```

**Note:** The URL must include the `/mcp` path. The server exposes stateless Streamable HTTP at `/mcp`, not at the root. Using `http://localhost:7683` without `/mcp` results in 404 (Not Found).

### Docker

Build and run with Docker:

```bash
# Build the image
docker build -t law-scrapper-mcp .

# Run with STDIO transport (default)
docker run -it law-scrapper-mcp

# Run with HTTP transport on port 7683
docker run -it -p 7683:7683 -e LAW_MCP_TRANSPORT=streamable-http law-scrapper-mcp
```

Or use docker-compose:

```bash
# Run with STDIO transport
docker compose up

# Run with HTTP transport (set TRANSPORT=streamable-http in docker-compose.yml)
docker compose -f docker-compose.yml up
```

## Configuration

All settings are configured via environment variables with the `LAW_MCP_` prefix:

The list-valued settings — `LAW_MCP_ALLOWED_HOSTS`, `LAW_MCP_ALLOWED_ORIGINS`,
`LAW_MCP_TRUSTED_PROXIES`, `LAW_MCP_AUTH_REQUIRED_SCOPES` and
`LAW_MCP_AUTH_ALGORITHMS` — accept either a comma-separated value
(`a:*, b:*`, the form used throughout this document) or a JSON array
(`["a:*", "b:*"]`). Both are equivalent; surrounding whitespace is trimmed.

| Variable | Default | Description |
|----------|---------|-------------|
| `LAW_MCP_TRANSPORT` | `stdio` | Transport: `stdio` or `streamable-http` |
| `LAW_MCP_HOST` | `127.0.0.1` | HTTP server host (when using streamable-http). Binding beyond loopback requires `LAW_MCP_AUTH_MODE` — see "Authenticated remote deployment" |
| `LAW_MCP_PORT` | `7683` | HTTP server port (when using streamable-http) |
| `LAW_MCP_SHUTDOWN_GRACE` | `15` | Graceful shutdown window in seconds for the HTTP server. Keep `stop_grace_period` in `docker-compose.yml` at or above twice this value — nothing in the code enforces the relation. |
| `LAW_MCP_API_TIMEOUT` | `30.0` | HTTP request timeout in seconds |
| `LAW_MCP_API_MAX_CONCURRENT` | `10` | Maximum concurrent API requests |
| `LAW_MCP_API_MAX_ATTEMPTS` | `3` | Attempts per operation, retries included |
| `LAW_MCP_API_RETRY_BUDGET` | `45.0` | Seconds the retry sequence of one operation may plan to wait |
| `LAW_MCP_CACHE_METADATA_TTL` | `86400` | Metadata cache TTL (24 hours) |
| `LAW_MCP_CACHE_SEARCH_TTL` | `600` | Search results cache TTL (10 minutes) |
| `LAW_MCP_CACHE_BROWSE_TTL` | `3600` | Browse results cache TTL (1 hour) |
| `LAW_MCP_CACHE_DETAILS_TTL` | `3600` | Act details cache TTL (1 hour) |
| `LAW_MCP_CACHE_CHANGES_TTL` | `300` | Changes tracking cache TTL (5 minutes) |
| `LAW_MCP_CACHE_MAX_ENTRIES` | `1000` | Maximum cache entries |
| `LAW_MCP_DOC_STORE_MAX_DOCUMENTS` | `10` | Maximum documents in Document Store |
| `LAW_MCP_DOC_STORE_MAX_SIZE_BYTES` | `5242880` | Maximum Document Store size (5 MB). Also the conversion threshold: content whose fetched HTML or PDF payload exceeds it is refused before conversion, with an error naming the source PDF URL. |
| `LAW_MCP_DOC_STORE_TTL` | `7200` | Document Store TTL (2 hours) |
| `LAW_MCP_CIRCUIT_BREAKER_THRESHOLD` | `5` | Failures before circuit breaker opens |
| `LAW_MCP_CIRCUIT_BREAKER_RECOVERY_TIMEOUT` | `60.0` | Seconds before trying recovery |
| `LAW_MCP_CIRCUIT_BREAKER_HALF_OPEN_MAX_CALLS` | `3` | Test calls in half-open state |
| `LAW_MCP_MAX_PATTERN_LENGTH` | `512` | Max `filter_results` pattern length, clamped to 64-4096 |
| `LAW_MCP_FILTER_MAX_RECORDS` | `100` | Max records `filter_results` processes per call; floor 1, no ceiling (very high values lengthen the synchronous but linear scan) |
| `LAW_MCP_ALLOWED_HOSTS` | `127.0.0.1:*, localhost:*, [::1]:*` | `Host` header allowlist for streamable-http (DNS-rebinding protection). Widening beyond loopback requires an auth mode — see "Authenticated remote deployment" |
| `LAW_MCP_ALLOWED_ORIGINS` | `http://127.0.0.1:*, http://localhost:*, http://[::1]:*` | `Origin` header allowlist for streamable-http. Same auth-mode requirement as `LAW_MCP_ALLOWED_HOSTS` |
| `LAW_MCP_AUTH_JWKS_URI` | unset | Override the JWKS URI discovered from `LAW_MCP_AUTH_ISSUER`'s OIDC discovery document; needed only when a provider's discovery document omits or misreports it |
| `LAW_MCP_AUTH_REQUIRED_SCOPES` | `[]` (none) | Scopes a presented token must carry, checked by `RequireAuthMiddleware`. Meaningful in `oauth` mode only: under `bearer` the static verifier grants exactly these scopes to every holder of the token, so the check is satisfied by construction and confers no authorization |
| `LAW_MCP_AUTH_ALGORITHMS` | `RS256, ES256` | JWT signature algorithm allowlist passed to the decoder; never read from the token header |
| `LAW_MCP_AUTH_JWKS_CACHE_TTL` | `3600` | Seconds a fetched JWKS key set is cached before re-fetching |
| `LAW_MCP_RATE_LIMIT_ENABLED` | `true` | Whether the per-client rate limiter wraps the HTTP app |
| `LAW_MCP_RATE_LIMIT_REQUESTS` | `60` | Requests allowed per `LAW_MCP_RATE_LIMIT_WINDOW` before throttling |
| `LAW_MCP_RATE_LIMIT_WINDOW` | `60.0` | Rate limit window in seconds |
| `LAW_MCP_RATE_LIMIT_BURST` | `10` | Token bucket capacity — how many requests can arrive back-to-back before `429` |
| `LAW_MCP_LOG_LEVEL` | `INFO` | Log level: DEBUG, INFO, WARNING, ERROR |
| `LAW_MCP_LOG_FORMAT` | `text` | Log format: `text` or `json` |

Logs go to stderr (stdout carries the MCP protocol on the STDIO transport). Every record carries a `request_id` correlating it with a single tool call — `lifespan` for records emitted outside one:

```text
2026-08-24 12:00:00,123 - law_scrapper_mcp.tools.search - [a1b2c3d4] - INFO - Stored result set rs_1: 20 results (total 137)
```

```json
{"timestamp": "2026-08-24T10:00:00.123456+00:00", "level": "INFO", "logger": "law_scrapper_mcp.tools.search", "request_id": "a1b2c3d4", "message": "Stored result set rs_1: 20 results (total 137)"}
```

Set `LAW_MCP_LOG_LEVEL=DEBUG` to recover the detail deliberately kept off INFO: search query text and the exception messages behind `validation` and `upstream` failures.

Example environment configuration:

```bash
export LAW_MCP_TRANSPORT=streamable-http
export LAW_MCP_PORT=7683
export LAW_MCP_LOG_LEVEL=DEBUG
export LAW_MCP_CACHE_METADATA_TTL=86400
```

### Authenticated remote deployment

The HTTP transport binds `127.0.0.1` by default and refuses to start on any
other address unless an authentication mode is configured. Two modes exist and
neither falls back to the other.

**Bearer token** — local and simple deployments:

```bash
export LAW_MCP_AUTH_TOKEN=$(openssl rand -base64 32)   # min. 32 bytes
LAW_MCP_TRANSPORT=streamable-http \
LAW_MCP_HOST=0.0.0.0 \
LAW_MCP_AUTH_MODE=bearer \
  law-scrapper
```

In production prefer `LAW_MCP_AUTH_TOKEN_FILE=/run/secrets/law_mcp_token` —
an environment variable is visible in `docker inspect` and `/proc/<pid>/environ`.
Setting both sources is a startup error, not a precedence rule.

**OAuth 2.1 / OIDC** — corporate deployments. Works with any provider publishing
OIDC discovery and JWKS:

| Provider | `LAW_MCP_AUTH_ISSUER` |
|---|---|
| Microsoft Entra ID | `https://login.microsoftonline.com/<tenant>/v2.0` |
| Google | `https://accounts.google.com` |
| AWS Cognito | `https://cognito-idp.<region>.amazonaws.com/<pool-id>` |
| Okta | `https://<org>.okta.com/oauth2/<server-id>` |
| Auth0 | `https://<tenant>.auth0.com/` |

```bash
LAW_MCP_AUTH_MODE=oauth \
LAW_MCP_AUTH_ISSUER=https://login.microsoftonline.com/<tenant>/v2.0 \
LAW_MCP_AUTH_AUDIENCE=api://law-scrapper \
LAW_MCP_AUTH_RESOURCE_SERVER_URL=https://mcp.example.com/mcp \
  law-scrapper
```

**Reachable through a reverse proxy** — `LAW_MCP_ALLOWED_HOSTS` and
`LAW_MCP_ALLOWED_ORIGINS` default to loopback-only (F18): a request whose
`Host` or `Origin` header doesn't match gets `421`/`403` from the SDK's
DNS-rebinding protection, even with a valid token. This default is
deliberate — widening the allowlist is only permitted once an auth mode is
configured (enforced at startup, D6). A reverse proxy that preserves the
original `Host` (nginx's `proxy_set_header Host $host`, Caddy's default)
needs both variables set to the public name, for either auth mode above:

```bash
LAW_MCP_ALLOWED_HOSTS='mcp.example.com:*' \
LAW_MCP_ALLOWED_ORIGINS='https://mcp.example.com' \
  law-scrapper
```

Providers issuing opaque tokens (GitHub) are not supported — they would require
RFC 7662 introspection. TLS termination stays with the reverse proxy.
`/health` is intentionally unauthenticated so container healthchecks work; it
exposes the server version and circuit-breaker state.

Rate limiting is always on for HTTP: `60` requests per `60 s`, burst `10`.
Behind a proxy, set `LAW_MCP_TRUSTED_PROXIES` (addresses or CIDRs) — otherwise
`X-Forwarded-For` is ignored and every client shares one bucket.

## Tools reference

Law Scrapper MCP provides 13 tools for legal research and analysis:

### 1. get_system_metadata(category)

Retrieve system metadata for filtering and searching legal acts.

**Parameters:**
- `category` (string, default: "all") - Metadata category: "keywords", "publishers", "statuses", "types", "institutions", or "all"

**Returns:** Keywords, publishers, document types, statuses, and institutions available in the system

**Examples:**
```
- Get all available search keywords
- Retrieve all legal document types
- List all publishers (DU, MP)
- Get all document statuses
- Get complete system metadata
```

### 2. search_legal_acts(publisher, year, keywords, detail_level, status, type)

Search for legal acts with advanced filtering options.

**Parameters:**
- `publisher` (string) - Publisher code: "DU" (Dziennik Ustaw) or "MP" (Monitor Polski)
- `year` (integer) - Publication year (e.g., 2024)
- `keywords` (string) - Search keywords (AND logic - use multiple searches for OR)
- `detail_level` (string, default: "standard") - Response detail: "minimal", "standard", or "full"
- `status` (string, optional) - Document status filter
- `type` (string, optional) - Document type filter

**Returns:** List of matching legal acts with metadata

**Search note:** Multiple keywords use AND logic. Search one keyword at a time for OR behavior.

**Examples:**
```
- Search DU 2024 for "environment protection" acts
- Find all MP 2023 acts with status "active"
- Search for COVID-19 related legislation
- Find acts by specific type (e.g., "regulation")
- Get minimal detail results for quick scanning
```

### 3. browse_acts(publisher, year, detail_level)

Browse all legal acts published in a specific year by publisher.

**Parameters:**
- `publisher` (string) - Publisher code: "DU" or "MP"
- `year` (integer) - Publication year
- `detail_level` (string, default: "standard") - Response detail: "minimal", "standard", or "full"

**Returns:** Complete list of acts published in the specified year

**Examples:**
```
- Browse all DU acts from 2024
- Get minimal details of all MP acts from 2023
- Browse full details of DU 2022 legislation
- Get an overview of acts by publisher and year
- Track legislation published in a specific year
```

### 4. filter_results(result_set_id, pattern, field, type_equals, ...)

Filter and narrow down previously retrieved search/browse/changes results.

**Parameters:**
- `result_set_id` (string) - Result set ID from a previous search/browse/changes call (e.g., "rs_1")
- `pattern` (string, optional) - Regex pattern for text search (supports OR: "podatek|VAT|akcyza")
- `field` (string, default: "title") - Field to search: "title", "eli", "status", "type", "publisher"
- `type_equals` (string, optional) - Exact match on document type (e.g., "Ustawa", "Rozporządzenie")
- `status_equals` (string, optional) - Exact match on status (e.g., "akt obowiązujący", "akt uchylony")
- `year_equals` (integer, optional) - Exact match on publication year
- `date_field` (string, optional) - Date field for range filter: "promulgation_date" or "effective_date"
- `date_from` / `date_to` (string, optional) - Date range (YYYY-MM-DD)
- `sort_by` (string, optional) - Sort field: "title", "year", "pos", "promulgation_date", etc.
- `sort_desc` (boolean, default: false) - Sort descending
- `limit` (integer, optional) - Maximum results to return

**Returns:** Filtered results with a new `result_set_id` for chained filtering

**Examples:**
```
- Filter search results to only "Rozporządzenie" type
- Search titles with regex "zdrow|apteka|lekar"
- Filter by date range and sort by promulgation date
- Chain filters: first by type, then by regex pattern
- Get top 10 most recent results
```

### 5. get_act_details(eli, load_content, detail_level)

Retrieve detailed information about a specific legal act and optionally load its content.

**Parameters:**
- `eli` (string) - Act identifier in format "PUBLISHER/YEAR/NUMBER" (e.g., "DU/2024/1")
- `load_content` (boolean, default: false) - Load act content into Document Store for section reading
- `detail_level` (string, default: "standard") - Response detail: "minimal", "standard", or "full"

**Returns:** Act metadata (title, publication date, status, type, etc.), table of contents if load_content=true

**Examples:**
```
- Get metadata for act DU/2024/1
- Load act content for section-level reading
- Get full details including table of contents
- Retrieve act status and publication information
- Load multiple acts for comparison
```

### 6. read_act_content(eli, section)

Read content from a specific section of a loaded legal act.

**Parameters:**
- `eli` (string) - Act identifier (must be loaded first via get_act_details with load_content=true)
- `section` (string) - Section to read (e.g., "Art. 1", "Chapter 2", "Preamble")

**Returns:** Content of the requested section

**Workflow note:** Must call get_act_details(eli="...", load_content=true) first, then use this tool.

**Examples:**
```
- Read Article 1 from loaded act
- Get Chapter 2 content
- Read the Preamble section
- Access specific numbered articles
- Navigate act by chapters
```

### 7. search_in_act(eli, query)

Search for specific terms within a loaded legal act.

**Parameters:**
- `eli` (string) - Act identifier (must be loaded first via get_act_details with load_content=true)
- `query` (string) - Search term or phrase

**Returns:** Matching sections with context and location

**Examples:**
```
- Find all mentions of "penalty" in loaded act
- Search for specific legal terms
- Locate articles containing "fine" or "punishment"
- Find definitional sections
- Search for specific references
```

### 8. analyze_act_relationships(eli, relationship_type)

Analyze legal relationships and references of an act (amendments, references, etc.).

**Parameters:**
- `eli` (string) - Act identifier
- `relationship_type` (string, default: "all") - Type: "amends", "amended_by", "references", "referenced_by", or "all"

**Returns:** List of related acts and their relationships

**Examples:**
```
- Find which acts amend this legislation
- See what acts this legislation amends
- Get all legal references in the act
- Find acts that reference this legislation
- Analyze complete act relationship network
```

### 9. track_legal_changes(date_from, date_to, publisher, keywords)

Track legal changes and new acts within a date range.

**Parameters:**
- `date_from` (string) - Start date (YYYY-MM-DD format)
- `date_to` (string) - End date (YYYY-MM-DD format)
- `publisher` (string, optional) - Filter by publisher: "DU" or "MP"
- `keywords` (string, optional) - Filter by keywords

**Returns:** Legal acts published in the date range

**Examples:**
```
- Track changes from 2024-01-01 to 2024-12-31
- Find new DU acts from last month
- Get changes published in past 7 days
- Track legislation on specific topics over time
- Monitor legal changes by publisher and date range
```

### 10. calculate_legal_date(days, months, years, base_date)

Calculate legal dates with intuitive sign convention.

**Parameters:**
- `days` (integer, default: 0) - Days offset (+future, -past)
- `months` (integer, default: 0) - Months offset (+future, -past)
- `years` (integer, default: 0) - Years offset (+future, -past)
- `base_date` (string, optional) - Base date (YYYY, YYYY-MM, or YYYY-MM-DD format, defaults to today)

**Returns:** Calculated date and relative description

**Sign convention:** Positive = future, Negative = past

**Examples:**
```
- Get current date (call with no parameters)
- Calculate date 30 days in the future (+30)
- Calculate date 6 months in the past (-6 months)
- Calculate date 1 year from a specific date
- Calculate legal deadlines and periods
```

### 11. compare_acts(eli_a, eli_b)

Compare metadata of two legal acts.

**Parameters:**
- `eli_a` (string) - ELI identifier of the first act (e.g., "DU/2024/1692")
- `eli_b` (string) - ELI identifier of the second act (e.g., "DU/2024/1716")

**Returns:** Comparison of titles, types, statuses, dates, keywords overlap and differences

**Examples:**
```
- Compare two acts from the same year
- Compare old and new versions of legislation
- Identify metadata differences between related acts
```

### 12. list_result_sets()

Display active result sets stored in memory.

**Returns:** List of result sets with IDs, query summaries, counts, and creation times

### 13. list_loaded_documents()

Display documents loaded into the Document Store.

**Returns:** List of loaded documents with ELIs, sizes, section counts, and timestamps

## Document Store workflow

The Document Store pattern enables efficient content navigation and search within legal acts:

### Workflow steps

1. **Load an act** - Call `get_act_details(eli="DU/2024/1", load_content=true)` to load the act into the Document Store
2. **Read sections** - Use `read_act_content(eli="DU/2024/1", section="Art. 1")` to read specific sections
3. **Search within act** - Use `search_in_act(eli="DU/2024/1", query="penalty")` to find terms

### Benefits

- Efficient memory usage (configurable max documents and TTL)
- Fast section-level navigation without refetching
- Search within loaded acts without API calls
- Automatic content processing (PDF→text, HTML→Markdown)

### Configuration

- `LAW_MCP_DOC_STORE_MAX_DOCUMENTS` - How many acts to keep in memory (default: 10)
- `LAW_MCP_DOC_STORE_MAX_SIZE_BYTES` - Maximum memory usage (default: 5 MB)
- `LAW_MCP_DOC_STORE_TTL` - How long to keep acts in memory (default: 2 hours)

## Project structure

```
law-scrapper-mcp/
├── src/law_scrapper_mcp/
│   ├── __init__.py
│   ├── __main__.py              # Entry point for python -m
│   ├── server.py                # MCPServer, lifespan, transport config
│   ├── config.py                # Pydantic settings (env vars)
│   ├── logging_config.py        # Structured logging setup
│   ├── models/                  # Pydantic models
│   │   ├── enums.py            # Enumerations
│   │   ├── api_responses.py    # Sejm API response models
│   │   ├── tool_inputs.py      # Tool input models
│   │   └── tool_outputs.py     # Tool output models
│   ├── client/                  # HTTP client
│   │   ├── sejm_client.py      # AsyncClient with retry and circuit breaker
│   │   ├── cache.py            # Async TTL cache implementation
│   │   ├── circuit_breaker.py  # Circuit breaker for API protection
│   │   └── exceptions.py       # Custom exceptions (Polish messages)
│   ├── services/                # Business logic
│   │   ├── metadata_service.py    # Metadata retrieval
│   │   ├── search_service.py      # Search and browse
│   │   ├── act_service.py         # Act details and content
│   │   ├── changes_service.py     # Change tracking
│   │   ├── document_store.py      # In-memory act storage
│   │   ├── result_store.py        # Search result persistence and filtering
│   │   ├── content_processor.py   # PDF/HTML processing
│   │   └── response_enrichment.py # Response hints
│   └── tools/                   # MCP tool definitions
│       ├── metadata.py          # get_system_metadata
│       ├── search.py            # search_legal_acts
│       ├── browse.py            # browse_acts
│       ├── act_details.py       # get_act_details
│       ├── act_content.py       # read_act_content
│       ├── act_search.py        # search_in_act
│       ├── relationships.py     # analyze_act_relationships
│       ├── filter_results.py    # filter_results, list_result_sets
│       ├── changes.py           # track_legal_changes
│       ├── compare.py           # compare_acts
│       ├── dates.py             # calculate_legal_date
│       └── error_handling.py    # Centralized @handle_tool_errors decorator
├── tests/
│   ├── unit/                    # Unit tests
│   └── integration/             # Integration tests with Sejm API
├── Dockerfile                   # Container image definition
├── docker-compose.yml           # Multi-service setup
├── pyproject.toml              # Project metadata and dependencies
├── uv.lock                      # Reproducible dependency lock
└── README.md                    # This file
```

## Docker

### Security and deployment

The server binds `127.0.0.1` by default; binding beyond loopback (as Docker port publishing requires) fails at startup unless an authentication mode is configured — see "Authenticated remote deployment" above. When exposing the HTTP transport (`streamable-http`) to a network, place the server behind a reverse proxy (nginx, Caddy, Traefik) with TLS termination — this project verifies bearer tokens and OAuth 2.1/OIDC access tokens, but does not terminate TLS itself. The `/health` endpoint is unauthenticated and intended for container healthchecks only — do not expose it publicly without access controls. Dependency versions are pinned in `uv.lock` with security overrides in `pyproject.toml` (`cryptography`, `urllib3`, `idna`, `werkzeug`, `requests`).

**Host/Origin allowlist (DNS-rebinding protection):** the official MCP SDK only auto-enables `Host`/`Origin` validation when the server binds to a literal loopback address (`127.0.0.1`, `localhost`, `::1`). `docker-compose.yml` sets `LAW_MCP_HOST=0.0.0.0` so Docker can publish the port, which would otherwise leave that validation disabled. `server.py` passes `transport_security` explicitly (`build_transport_security()`) so requests are still validated against the configured allowlist — `Host` outside it gets `421`, `Origin` outside it gets `403` — independent of the bind address. The allowlist defaults to loopback only (`127.0.0.1:*` / `localhost:*` / `[::1]:*` and the matching `http://` origins) and is widened through `LAW_MCP_ALLOWED_HOSTS` / `LAW_MCP_ALLOWED_ORIGINS`, which startup validation permits only once an authentication mode is configured. This restores the pre-3.0.0 FastMCP posture **for `/mcp`**. It is a defense-in-depth layer, not a substitute for the authentication mode required to bind beyond loopback in the first place.

One deliberate difference from the pre-3.0.0 server: the SDK applies this validation inside the Streamable HTTP app, not as whole-app middleware, so `/health` is **not** covered by the allowlist and answers any `Host`. That is what keeps container healthchecks working when they connect by container name or bridge IP, but it also means `/health` discloses the server name and version — and, since it now also reports the circuit breaker's `circuit_state` and `failure_count`, the health of the Sejm API integration — to anything that can reach the published port. FastMCP guarded `/health` too. Restrict the published port, or front it with a proxy, if that disclosure matters to you.

**Health contract:** the response body carries an `upstream` object. `upstream.circuit_state` is `closed`, `open`, `half_open`, or `unknown` before the lifespan has started (or after it has been torn down) — in that `unknown` case `upstream.failure_count` is omitted entirely; otherwise it sits alongside `circuit_state`. `/health` still answers `200` while the breaker is `open`: restarting the container cannot repair an outage of api.sejm.gov.pl, and under `restart: unless-stopped` a `503` would turn someone else's outage into a restart loop.

### Dockerfile

The included `Dockerfile` builds a containerized Law Scrapper MCP server:

```dockerfile
FROM python:3.13-slim
WORKDIR /app
COPY . .
RUN pip install -e .
EXPOSE 7683
CMD ["law-scrapper"]
```

Build and run:

```bash
# Build the image
docker build -t law-scrapper-mcp .

# Run with STDIO transport
docker run -it law-scrapper-mcp

# Run with HTTP transport, published on the host — the image no longer
# defaults LAW_MCP_HOST to 0.0.0.0, so publishing the port beyond loopback
# requires setting the host explicitly together with an authentication mode
docker run -it -p 7683:7683 \
  -e LAW_MCP_TRANSPORT=streamable-http \
  -e LAW_MCP_HOST=0.0.0.0 \
  -e LAW_MCP_AUTH_MODE=bearer \
  -e LAW_MCP_AUTH_TOKEN="$(openssl rand -base64 32)" \
  -e LAW_MCP_LOG_LEVEL=DEBUG \
  law-scrapper-mcp
```

### docker-compose.yml

Deployment with docker-compose:

```bash
# LAW_MCP_AUTH_TOKEN is required — docker-compose.yml fails fast without it
export LAW_MCP_AUTH_TOKEN=$(openssl rand -base64 32)

# Start service
docker compose up -d

# View logs
docker compose logs -f

# Stop service
docker compose down
```

## Migration guide (v1 to v2)

If upgrading from v1.0.2, note these breaking changes:

| v1.0.2 (old) | v2.0.0 (new) | Notes |
|-------------|------------|-------|
| `get_current_date` | `calculate_legal_date()` | Call with no parameters for current date |
| `calculate_date_offset` | `calculate_legal_date(days/months/years)` | Use intuitive +future/-past sign convention |
| `get_legal_keywords` | `get_system_metadata(category="keywords")` | Consolidated into one tool |
| `get_legal_publishers` | `get_system_metadata(category="publishers")` | Consolidated into one tool |
| `get_legal_statuses` | `get_system_metadata(category="statuses")` | Consolidated into one tool |
| `get_legal_types` | `get_system_metadata(category="types")` | Consolidated into one tool |
| `get_legal_institutions` | `get_system_metadata(category="institutions")` | Consolidated into one tool |
| `get_publisher_details` | N/A | Use `get_system_metadata(category="publishers")` |
| `search_legal_acts` | `search_legal_acts` | Enhanced with `detail_level` parameter |
| `get_publisher_year_acts` | `browse_acts` | Renamed for clarity |
| `get_act_comprehensive_details` | `get_act_details` | Added `load_content` and `detail_level` |
| `get_act_content` | `read_act_content` | Requires pre-loading with `get_act_details` |
| `get_act_table_of_contents` | `get_act_details` | TOC included in details response |
| `get_act_relationships` | `analyze_act_relationships` | Renamed for clarity |
| ELI format | Single string "DU/2024/1" | Changed from separate parameters |
| Legacy event-stream transport | STDIO (default) | STDIO is default, HTTP via streamable-http |
| Port 7683 | Port 7683 | Same default HTTP port |

## What's new in v3.0.0

- **Official Python MCP SDK** — Replaced the prior third-party framework with `mcp[cli]==2.0.0` and `MCPServer[AppContext]`
- **Native structured responses** — Tools return `EnrichedResponse` with `outputSchema` and object `structuredContent` (no JSON strings)
- **Protocol-visible errors** — Tool failures surface as `isError=true` instead of in-body `error` fields
- **Stateless Streamable HTTP** — `/mcp` endpoint only; legacy event-stream transport removed
- **Pagination** — `PageInfo` model exposed as the `page_info` field with `limit`/`offset` on search, browse, metadata, changes, TOC, sections, and in-act search
- **Typed `AppContext`** — Lifespan resources accessed via `ctx.request_context.lifespan_context`
- **Domain services** — `ComparisonService`, `RelationshipService`, and `DateService` extracted from tool adapters
- **Transport tests** — Real STDIO subprocess, loopback HTTP, and MCP conformance in CI
- **Explicit Host/Origin allowlist on HTTP** — `transport_security` is passed explicitly so DNS-rebinding protection applies regardless of the configured bind host; see [Security and deployment](#security-and-deployment)

## Migration guide (v2 to v3)

| v2.x (old) | v3.0.0 (new) | Notes |
|------------|--------------|-------|
| `result.content` JSON string | `structuredContent` object | Parse native MCP structured payload |
| `EnrichedResponse.error` in success body | `isError=true` | Check `result.is_error` before reading content |
| v2.x in-process client | Official `mcp.Client` | In-memory and transport tests use SDK client |
| v2.x direct lifespan access | `ctx.request_context.lifespan_context` | Typed `AppContext` dataclass |
| Legacy event-stream transport | Removed | STDIO or stateless Streamable HTTP only |

## What's new in v2.4.0

- **Security hardening** — Framework upgrade and dependency overrides close 51 Dependabot alerts (cryptography, urllib3, pillow, starlette, and others)
- **Framework 3.x** — Lifespan context API, `app.run()` for HTTP transport, `@custom_route` for `/health`
- **Integration tests** — In-memory client tests for core tools (metadata, search, dates, act details)
- **CI and Dependabot** — Automated quality gates and weekly dependency updates

## What's new in v2.3.1

- **uvx / tool registration fix** — Fixed `NameError: name 'Annotated' is not defined` when running via `uvx --from "git+https://github.com/numikel/law-scrapper-mcp" law-scrapper`. Removed `from __future__ import annotations` from `compare.py` so parameter type hints resolve correctly during tool registration.

## What's new in v2.3.0

- **3 new tools** — `compare_acts`, `list_result_sets`, `list_loaded_documents` (total: 13 tools)
- **Circuit breaker** — Protects against cascading failures when Sejm API is unavailable
- **Centralized error handling** — `@handle_tool_errors` decorator with error classification and full tracebacks
- **asyncio.Lock migration** — All stores use `asyncio.Lock` for proper async compatibility
- **Default search limit** — Search/browse return max 20 results by default to limit token usage
- **Health endpoint** — `/health` for Docker deployments with streamable-http transport
- **Polish error messages** — All exception messages in Polish for consistent user experience
- **Decision tree docstrings** — "When to use" / "When NOT to use" for all tools

## Development

### Setup

```bash
# Install dependencies
uv sync

# Install with dev dependencies
uv sync --extra dev
```

### Running tests

```bash
# Run unit tests
uv run pytest tests/unit/ -v

# Run integration tests (mocked Sejm API via respx; no live network required)
uv run pytest tests/integration/ -v -m integration

# Run all tests with coverage
uv run pytest --cov=law_scrapper_mcp --cov-report=term-missing

# Run with timeout for slow tests
uv run pytest --timeout=10 -v
```

### MCP integration testing

Law Scrapper MCP uses three automated test layers to verify all 13 tools and their MCP protocol interaction:

**Layer 1 — unit tests** (`tests/unit/`):

```bash
uv run pytest tests/unit/ -v
```

**Layer 2 — in-memory integration** (official `mcp.Client`, mocked Sejm API via `respx`):

```bash
uv run pytest tests/integration/test_tools_e2e.py \
  tests/integration/test_content_pagination.py \
  tests/integration/test_result_pagination.py -v -m integration
```

**Layer 3 — transport integration** (real STDIO subprocess and loopback Streamable HTTP):

```bash
uv run pytest tests/integration/test_stdio_transport.py \
  tests/integration/test_http_transport.py -v -m integration
```

**Full integration suite:**

```bash
uv run pytest tests/integration/ -v -m integration
```

**What the tests verify:**
- All 13 tools registered with preserved public arguments
- Native `structuredContent` payloads with `outputSchema` (not JSON strings)
- `isError=true` on tool execution failures
- `page_info` (`PageInfo`) pagination metadata on paginated outputs
- Stateful workflows: search → filter → load act → read sections → search in content
- Real STDIO and HTTP transports against a running server process

**Architecture:**
- **Test framework**: pytest with asyncio (unit) and anyio (MCP client tests)
- **HTTP mocking**: respx intercepts httpx calls in in-memory integration tests
- **MCP protocol**: Official `mcp.Client` from the Python MCP SDK
- **Helpers**: `tests/mcp_helpers.py` (`parse_tool_result` for `structuredContent` assertions)
- **CI**: Legacy dependency gate, protocol transport tests, and MCP conformance against `/mcp`

See `tests/TEST_SUITE_SUMMARY.md` for the full file layout and patterns.

### Code quality

The project follows MCP SDK best practices:
- **Modular architecture** - Separated concerns (models, client, services, tools)
- **Type hints** - Full type annotation with Pydantic models
- **Async throughout** - Async/await for all I/O operations
- **Comprehensive examples** - Minimum 5 examples per tool
- **Tagged tools** - Organized by category for easy discovery
- **Annotated parameters** - Clear descriptions for all inputs
- **Structured logging** - Configurable JSON/text formats

### Running the server

```bash
# STDIO transport (default)
uv run python -m law_scrapper_mcp

# HTTP transport
LAW_MCP_TRANSPORT=streamable-http uv run python -m law_scrapper_mcp

# With debug logging
LAW_MCP_LOG_LEVEL=DEBUG uv run python -m law_scrapper_mcp
```

## Contributing

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes using Conventional Commits format
4. Add tests for new functionality
5. Ensure all tests pass and coverage is maintained
6. Push to the branch (`git push origin feature/amazing-feature`)
7. Open a Pull Request

### Development guidelines

- Follow MCP SDK patterns for tool definitions and structured output
- Include comprehensive examples and parameter descriptions
- Add appropriate tags for tool categorization
- Write async code throughout
- Add tests for all new functionality
- Update CHANGELOG.md with your changes
- Use English for all code comments and documentation

## License

This project is licensed under the MIT License. See the LICENSE file for details.

## Author

**[@numikel](https://github.com/numikel)**

Developed with help from:

![Cursor](https://img.shields.io/badge/Cursor-2.4.37+-black.svg)
![Claude Code](https://img.shields.io/badge/Claude_Code-2.1.42+-orange.svg)

And with models:

![Claude Opus 4.6](https://img.shields.io/badge/Claude_Opus-4.6-orange.svg)
![Claude Opus 4.5](https://img.shields.io/badge/Claude_Opus-4.5-orange.svg)
![Claude Sonnet 4.5](https://img.shields.io/badge/Claude_Sonnet-4.5-orange.svg)
![Claude Haiku 4.5](https://img.shields.io/badge/Claude_Haiku-4.5-orange.svg)

---

**Legal disclaimer**: This tool provides access to Polish legal documents for research purposes. Always consult with qualified legal professionals for legal advice and interpretation of laws.
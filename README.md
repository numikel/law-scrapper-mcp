# Law Scrapper MCP

A comprehensive Model Context Protocol (MCP) server for accessing and analyzing Polish legal acts from the Sejm API, enabling AI-powered legal research and document analysis.

![Python version](https://img.shields.io/badge/python-3.13+-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Version](https://img.shields.io/badge/version-2.3.1-orange.svg)

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
- **FastMCP integration** - Built with FastMCP framework, flexible transport options
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

# Or specify custom host and port
LAW_MCP_TRANSPORT=streamable-http LAW_MCP_HOST=0.0.0.0 LAW_MCP_PORT=8080 uv run python -m law_scrapper_mcp
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

**Note:** The URL must include the `/mcp` path. FastMCP exposes the streamable-http endpoint at `/mcp`, not at the root. Using `http://localhost:7683` without `/mcp` results in 404 (Not Found).

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

| Variable | Default | Description |
|----------|---------|-------------|
| `LAW_MCP_TRANSPORT` | `stdio` | Transport: `stdio` or `streamable-http` |
| `LAW_MCP_HOST` | `0.0.0.0` | HTTP server host (when using streamable-http) |
| `LAW_MCP_PORT` | `7683` | HTTP server port (when using streamable-http) |
| `LAW_MCP_API_TIMEOUT` | `30.0` | HTTP request timeout in seconds |
| `LAW_MCP_API_MAX_CONCURRENT` | `10` | Maximum concurrent API requests |
| `LAW_MCP_API_MAX_RETRIES` | `3` | Maximum API request retries |
| `LAW_MCP_CACHE_METADATA_TTL` | `86400` | Metadata cache TTL (24 hours) |
| `LAW_MCP_CACHE_SEARCH_TTL` | `600` | Search results cache TTL (10 minutes) |
| `LAW_MCP_CACHE_BROWSE_TTL` | `3600` | Browse results cache TTL (1 hour) |
| `LAW_MCP_CACHE_DETAILS_TTL` | `3600` | Act details cache TTL (1 hour) |
| `LAW_MCP_CACHE_CHANGES_TTL` | `300` | Changes tracking cache TTL (5 minutes) |
| `LAW_MCP_CACHE_MAX_ENTRIES` | `1000` | Maximum cache entries |
| `LAW_MCP_DOC_STORE_MAX_DOCUMENTS` | `10` | Maximum documents in Document Store |
| `LAW_MCP_DOC_STORE_MAX_SIZE_BYTES` | `5242880` | Maximum Document Store size (5 MB) |
| `LAW_MCP_DOC_STORE_TTL` | `7200` | Document Store TTL (2 hours) |
| `LAW_MCP_CIRCUIT_BREAKER_THRESHOLD` | `5` | Failures before circuit breaker opens |
| `LAW_MCP_CIRCUIT_BREAKER_RECOVERY_TIMEOUT` | `60.0` | Seconds before trying recovery |
| `LAW_MCP_CIRCUIT_BREAKER_HALF_OPEN_MAX_CALLS` | `3` | Test calls in half-open state |
| `LAW_MCP_LOG_LEVEL` | `INFO` | Log level: DEBUG, INFO, WARNING, ERROR |
| `LAW_MCP_LOG_FORMAT` | `text` | Log format: `text` or `json` |

Example environment configuration:

```bash
export LAW_MCP_TRANSPORT=streamable-http
export LAW_MCP_PORT=7683
export LAW_MCP_LOG_LEVEL=DEBUG
export LAW_MCP_CACHE_METADATA_TTL=86400
```

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
│   ├── server.py                # FastMCP app, lifespan, transport config
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

# Run with HTTP transport
docker run -it -p 7683:7683 -e LAW_MCP_TRANSPORT=streamable-http law-scrapper-mcp

# With custom settings
docker run -it -p 7683:7683 \
  -e LAW_MCP_TRANSPORT=streamable-http \
  -e LAW_MCP_LOG_LEVEL=DEBUG \
  law-scrapper-mcp
```

### docker-compose.yml

Deployment with docker-compose:

```bash
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
| SSE transport | STDIO (default) | STDIO is default, HTTP via streamable-http |
| Port 7683 | Port 7683 | Same default HTTP port |

## What's new in v2.3.1

- **uvx / FastMCP fix** — Fixed `NameError: name 'Annotated' is not defined` when running via `uvx --from "git+https://github.com/numikel/law-scrapper-mcp" law-scrapper`. Removed `from __future__ import annotations` from `compare.py` so parameter type hints resolve correctly during tool registration.

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

# Run integration tests (requires internet)
uv run pytest tests/integration/ -v -m integration

# Run all tests with coverage
uv run pytest --cov=law_scrapper_mcp --cov-report=term-missing

# Run with timeout for slow tests
uv run pytest --timeout=10 -v
```

### Code quality

The project follows FastMCP best practices:
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

- Follow FastMCP best practices for tool definitions
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
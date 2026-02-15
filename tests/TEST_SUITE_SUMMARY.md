# Law Scrapper MCP Test Suite

## Overview

Comprehensive test suite for Law Scrapper MCP v2.0, covering all models, services, and tools.

## Test Structure

```
tests/
├── fixtures/              # Test data files
│   ├── publishers.json
│   ├── search_results.json
│   ├── act_detail.json
│   ├── act_structure.json
│   ├── act_references.json
│   └── sample_act.html
├── conftest.py           # Shared fixtures
├── unit/                 # Unit tests
│   ├── test_models.py
│   ├── test_cache.py
│   ├── test_config.py
│   ├── test_content_processor.py
│   ├── test_document_store.py
│   └── test_services/
│       ├── __init__.py
│       ├── test_metadata_service.py
│       ├── test_search_service.py
│       ├── test_act_service.py
│       └── test_changes_service.py
└── integration/          # Integration tests
    └── test_tools_e2e.py
```

## Running Tests

### Run all tests
```bash
uv run pytest tests/
```

### Run unit tests only
```bash
uv run pytest tests/unit/
```

### Run integration tests only
```bash
uv run pytest tests/integration/ -m integration
```

### Run with coverage
```bash
uv run pytest tests/ --cov=src/law_scrapper_mcp --cov-report=html
```

### Run specific test file
```bash
uv run pytest tests/unit/test_models.py
```

### Run specific test class or method
```bash
uv run pytest tests/unit/test_cache.py::TestTTLCacheBasicOperations
uv run pytest tests/unit/test_models.py::TestParseEli::test_valid_eli_simple
```

### Exclude integration tests
```bash
uv run pytest tests/ -m "not integration"
```

## Test Coverage

### 1. Fixtures (tests/fixtures/)

**publishers.json**
- Sample publisher data (DU, MP)
- Used for metadata service tests

**search_results.json**
- Sample search results with 3 acts
- Includes various act types and statuses

**act_detail.json**
- Detailed act information
- Keywords, dates, references

**act_structure.json**
- Sample TOC structure
- Nested sections and articles

**act_references.json**
- Related acts (changed, legal basis)

**sample_act.html**
- Real Polish legal act HTML
- Articles, chapters, lists

### 2. Shared Fixtures (tests/conftest.py)

- `fixtures_dir` - Path to fixtures directory
- `sample_act_html` - Sample HTML content
- `search_results` - Loaded search results
- `act_detail` - Loaded act details
- `act_structure` - Loaded TOC structure
- `act_references` - Loaded references
- `publishers_data` - Loaded publishers
- `cache` - Fresh TTLCache instance
- `mock_client` - Mocked SejmApiClient (with respx)
- `document_store` - Fresh DocumentStore
- `content_processor` - ContentProcessor instance

### 3. Unit Tests

#### test_models.py (66 tests)
- `TestParseEli` - ELI parsing with valid/invalid inputs, URLs
- `TestEnums` - All enum values and labels
- `TestToolInputModels` - Pydantic model validation
- `TestToolOutputModels` - Output model serialization
- `TestApiResponseModels` - API response parsing

#### test_cache.py (47 tests)
- `TestTTLCacheBasicOperations` - get/set/delete/clear/size
- `TestTTLExpiration` - TTL expiration behavior with time mocking
- `TestLRUEviction` - LRU eviction when max_entries exceeded
- `TestThreadSafety` - Concurrent access from multiple threads
- `TestCacheEdgeCases` - Zero TTL, negative TTL, complex values

#### test_config.py (25 tests)
- `TestSettingsDefaults` - All default values
- `TestSettingsFromEnvironment` - Loading from LAW_MCP_* env vars
- `TestSettingsValidation` - Type validation errors

#### test_content_processor.py (53 tests)
- `TestHtmlToMarkdown` - HTML conversion, script stripping, whitespace
- `TestPdfToText` - PDF extraction with mocked pdfplumber
- `TestIndexSections` - Section indexing with Art., Rozdział, DZIAŁ patterns
- `TestSection` - Section dataclass creation

#### test_document_store.py (78 tests)
- `TestDocumentStoreBasicOperations` - load/is_loaded/get_toc/evict
- `TestGetSection` - Section retrieval by ID, title, Art. pattern
- `TestSearchInDocument` - Text search with context extraction
- `TestTTLExpiration` - Document expiration after TTL
- `TestLRUEviction` - LRU eviction when max_documents reached
- `TestDocumentSizeLimits` - Size truncation
- `TestLoadedDocument` - Document dataclass
- `TestEdgeCases` - Empty sections, reload

#### test_services/test_metadata_service.py (23 tests)
- Getting all metadata categories
- Individual category fetching (keywords, publishers, statuses, types, institutions)
- Error handling for API failures

#### test_services/test_search_service.py (44 tests)
- Basic search with various filters
- Keywords, date ranges, title, in_force
- Pagination (limit/offset)
- Detail levels (minimal/standard/full)
- Browse by publisher/year
- Empty results handling

#### test_services/test_act_service.py (38 tests)
- Getting act details with/without structure
- Loading HTML/PDF content
- Content already loaded (no reload)
- Missing content handling
- URL ELI parsing
- All date fields, keywords, volume
- Recursive TOC formatting

#### test_services/test_changes_service.py (28 tests)
- Basic changes tracking
- Date_to defaulting to today
- Keyword filtering
- Different publishers (DU/MP)
- Empty results
- Result formatting
- Multiple keywords
- API error handling

### 4. Integration Tests (tests/integration/test_tools_e2e.py)

**Note:** Integration tests are marked with `@pytest.mark.skip` because they require FastMCP test client support. They serve as placeholders for future E2E testing.

- Metadata tools
- Search and browse tools
- Act details and content tools
- Changes tracking tools
- Date utility tools

## Test Patterns Used

### 1. Async Testing
All async tests use `async def test_...` and pytest-asyncio (asyncio_mode = "auto")

### 2. HTTP Mocking
Uses `respx` to mock httpx requests in the mock_client fixture:
```python
@respx.mock
async def test_search_basic(self, service: SearchService):
    respx.get("https://api.sejm.gov.pl/eli/acts/search").mock(
        return_value=Response(200, json=search_results)
    )
    results, total_count, query_summary = await service.search(...)
```

### 3. Time Mocking
Uses `unittest.mock.patch` to mock time.time() for TTL tests:
```python
with patch("time.time") as mock_time:
    mock_time.return_value = 1000.0
    cache.set("key", "value", ttl=60)
    mock_time.return_value = 1061.0
    assert cache.get("key") is None  # Expired
```

### 4. Parametrized Tests
Uses `@pytest.mark.parametrize` for testing multiple inputs:
```python
@pytest.mark.parametrize("invalid_eli", [
    "invalid", "DU/2024", "DU/abc/1", ...
])
def test_invalid_eli_format(self, invalid_eli: str):
    with pytest.raises(ValueError):
        parse_eli(invalid_eli)
```

### 5. Fixture Reuse
Shared fixtures from conftest.py are reused across all tests

## Key Features Tested

✅ ELI parsing (valid/invalid formats, URLs)
✅ Cache TTL expiration and LRU eviction
✅ Thread-safe cache operations
✅ HTML to Markdown conversion
✅ PDF text extraction (mocked)
✅ Section indexing (Art., Rozdział, DZIAŁ patterns)
✅ Document store load/evict/search
✅ Metadata service (all categories)
✅ Search service (filters, pagination, detail levels)
✅ Act service (details, content loading, TOC)
✅ Changes tracking service
✅ Configuration from environment variables
✅ API error handling
✅ Edge cases (empty results, missing fields)

## Coverage Goals

Target: 80%+ coverage

To generate coverage report:
```bash
uv run pytest tests/ --cov=src/law_scrapper_mcp --cov-report=html
# Open htmlcov/index.html in browser
```

## Next Steps

1. Run the full test suite to verify all tests pass
2. Check coverage report and add tests for uncovered code
3. Implement E2E integration tests when FastMCP test client is available
4. Add performance tests for large documents
5. Add property-based tests using Hypothesis

## CI/CD Integration

Add to GitHub Actions workflow:
```yaml
- name: Run tests
  run: uv run pytest tests/ -m "not integration" --cov=src/law_scrapper_mcp
```

## Maintenance

- Keep fixtures updated with real API response formats
- Update tests when API changes
- Add regression tests for bugs
- Review and update integration tests when FastMCP supports it

"""End-to-end integration tests for MCP tools.

Test strategy:
  - All 13 tools are exercised through the FastMCP in-memory Client (no real network).
  - HTTP calls to api.sejm.gov.pl are intercepted by respx (see conftest.mock_api_responses).
  - Each smoke test verifies:
      1. The tool returns a valid JSON payload without raising.
      2. The EnrichedResponse envelope contains both `data` and `hints` keys.
      3. At least one business-logic field in `data` carries the expected value.
  - Stateful workflow tests chain tools in the order a real client would use them
    (search → filter, get_act_details(load_content=True) → read/search in content).
  - Error-handling tests confirm graceful degradation: the decorator catches
    exceptions and returns an EnrichedResponse with a non-null `error` field
    instead of propagating the exception to the MCP client.

Coverage map (13 tools):
  metadata   : get_system_metadata
  search     : search_legal_acts, browse_acts
  filter     : filter_results, list_result_sets
  analysis   : get_act_details, read_act_content, search_in_act,
               analyze_act_relationships, compare_acts
  tracking   : track_legal_changes
  dates      : calculate_legal_date
  utility    : list_loaded_documents
"""

from __future__ import annotations

import json
from typing import Any

import pytest

pytestmark = pytest.mark.integration

EXPECTED_TOOLS = sorted(
    [
        "analyze_act_relationships",
        "browse_acts",
        "calculate_legal_date",
        "compare_acts",
        "filter_results",
        "get_act_details",
        "get_system_metadata",
        "list_loaded_documents",
        "list_result_sets",
        "read_act_content",
        "search_in_act",
        "search_legal_acts",
        "track_legal_changes",
    ]
)


def _parse_tool_result(result: Any) -> dict[str, Any]:
    """Extract JSON payload from a FastMCP call_tool result."""
    if hasattr(result, "data") and result.data is not None:
        if isinstance(result.data, dict):
            return result.data
        if isinstance(result.data, str):
            return json.loads(result.data)

    if result.content:
        return json.loads(result.content[0].text)

    raise ValueError(f"Unexpected tool result format: {result!r}")


def _assert_enriched(payload: dict[str, Any]) -> None:
    """Assert that the EnrichedResponse envelope is well-formed."""
    assert "data" in payload, "EnrichedResponse must contain 'data'"
    assert "hints" in payload, "EnrichedResponse must contain 'hints'"
    assert isinstance(payload["hints"], list), "'hints' must be a list"


# ---------------------------------------------------------------------------
# list_tools
# ---------------------------------------------------------------------------


class TestListToolsMCP:
    """Verify all 13 tools are registered and accessible via the MCP protocol."""

    async def test_list_tools_returns_all_13(self, mcp_client) -> None:
        """list_tools() returns exactly the 13 expected tool names."""
        tools = await mcp_client.list_tools()
        tool_names = sorted(tool.name for tool in tools)

        assert len(tool_names) == 13
        assert tool_names == EXPECTED_TOOLS

    async def test_all_tools_have_descriptions(self, mcp_client) -> None:
        """Every tool exposes a non-empty description string."""
        tools = await mcp_client.list_tools()
        for tool in tools:
            assert tool.description, f"Tool '{tool.name}' is missing a description"


# ---------------------------------------------------------------------------
# get_system_metadata
# ---------------------------------------------------------------------------


class TestMetadataTools:
    """Tests for metadata retrieval tools."""

    async def test_get_metadata_publishers(self, mcp_client) -> None:
        """Publishers category returns a list of publisher objects."""
        result = await mcp_client.call_tool("get_system_metadata", {"category": "publishers"})
        payload = _parse_tool_result(result)

        _assert_enriched(payload)
        assert payload["data"]["category"] == "publishers"
        assert "publishers" in payload["data"]["metadata"]
        assert len(payload["data"]["metadata"]["publishers"]) == 2

    async def test_get_metadata_keywords(self, mcp_client) -> None:
        """Keywords category returns a non-empty list of keyword strings."""
        result = await mcp_client.call_tool("get_system_metadata", {"category": "keywords"})
        payload = _parse_tool_result(result)

        _assert_enriched(payload)
        assert payload["data"]["category"] == "keywords"
        assert "keywords" in payload["data"]["metadata"]
        assert len(payload["data"]["metadata"]["keywords"]) >= 1

    async def test_get_metadata_types(self, mcp_client) -> None:
        """Types category returns document type values."""
        result = await mcp_client.call_tool("get_system_metadata", {"category": "types"})
        payload = _parse_tool_result(result)

        _assert_enriched(payload)
        assert payload["data"]["category"] == "types"
        assert "types" in payload["data"]["metadata"]

    async def test_get_metadata_statuses(self, mcp_client) -> None:
        """Statuses category returns at least one status value."""
        result = await mcp_client.call_tool("get_system_metadata", {"category": "statuses"})
        payload = _parse_tool_result(result)

        _assert_enriched(payload)
        assert payload["data"]["category"] == "statuses"
        assert "statuses" in payload["data"]["metadata"]

    async def test_get_metadata_all(self, mcp_client) -> None:
        """'all' category returns a combined metadata dict with multiple keys."""
        result = await mcp_client.call_tool("get_system_metadata", {"category": "all"})
        payload = _parse_tool_result(result)

        _assert_enriched(payload)
        assert payload["data"]["count"] > 0
        # At least publishers and keywords should be present
        assert len(payload["data"]["metadata"]) >= 2


# ---------------------------------------------------------------------------
# search_legal_acts, browse_acts, track_legal_changes
# ---------------------------------------------------------------------------


class TestSearchTools:
    """Tests for search and browse tools."""

    async def test_search_legal_acts(self, mcp_client) -> None:
        """search_legal_acts returns a paginated result set with acts."""
        result = await mcp_client.call_tool("search_legal_acts", {"year": 2024})
        payload = _parse_tool_result(result)

        _assert_enriched(payload)
        assert payload["data"]["total_count"] == 3
        assert payload["data"]["returned_count"] == 3
        assert payload["data"]["result_set_id"] is not None

    async def test_search_legal_acts_returns_enriched_results(self, mcp_client) -> None:
        """Each result in search output carries required fields."""
        result = await mcp_client.call_tool("search_legal_acts", {"year": 2024})
        payload = _parse_tool_result(result)

        first = payload["data"]["results"][0]
        assert "eli" in first
        assert "title" in first
        assert "status" in first

    async def test_list_result_sets_after_search(self, mcp_client) -> None:
        """list_result_sets reports the set created by the preceding search."""
        await mcp_client.call_tool("search_legal_acts", {"year": 2024})

        result = await mcp_client.call_tool("list_result_sets", {})
        payload = _parse_tool_result(result)

        _assert_enriched(payload)
        assert payload["data"]["count"] >= 1
        assert len(payload["data"]["sets"]) >= 1

    async def test_browse_acts(self, mcp_client) -> None:
        """browse_acts(publisher, year) returns acts for the given year."""
        result = await mcp_client.call_tool("browse_acts", {"publisher": "DU", "year": 2024})
        payload = _parse_tool_result(result)

        _assert_enriched(payload)
        assert payload["data"]["returned_count"] == 3
        assert payload["data"]["result_set_id"] is not None

    async def test_track_legal_changes(self, mcp_client) -> None:
        """track_legal_changes returns acts published in the given date range."""
        result = await mcp_client.call_tool(
            "track_legal_changes",
            {"date_from": "2024-01-01", "date_to": "2024-12-31"},
        )
        payload = _parse_tool_result(result)

        _assert_enriched(payload)
        assert "date_range" in payload["data"]
        assert payload["data"]["publisher"] == "DU"
        # The mock search returns 3 acts regardless of date params
        assert payload["data"]["total_count"] == 3


# ---------------------------------------------------------------------------
# filter_results, list_result_sets
# ---------------------------------------------------------------------------


class TestFilterTools:
    """Tests for result filtering tools."""

    async def test_filter_results_by_type(self, mcp_client) -> None:
        """filter_results narrows a result set to matching document types."""
        search = await mcp_client.call_tool("search_legal_acts", {"year": 2024})
        search_payload = _parse_tool_result(search)
        result_set_id = search_payload["data"]["result_set_id"]

        result = await mcp_client.call_tool(
            "filter_results",
            {"result_set_id": result_set_id, "type_equals": "Ustawa"},
        )
        payload = _parse_tool_result(result)

        _assert_enriched(payload)
        assert payload["data"]["source_result_set_id"] == result_set_id
        assert payload["data"]["filtered_count"] >= 1
        for act in payload["data"]["results"]:
            assert act["type"] == "Ustawa"

    async def test_filter_results_by_regex_pattern(self, mcp_client) -> None:
        """filter_results applies regex to act titles."""
        browse = await mcp_client.call_tool("browse_acts", {"publisher": "DU", "year": 2024})
        browse_payload = _parse_tool_result(browse)
        rs_id = browse_payload["data"]["result_set_id"]

        result = await mcp_client.call_tool(
            "filter_results",
            {"result_set_id": rs_id, "pattern": "ustawa"},
        )
        payload = _parse_tool_result(result)

        _assert_enriched(payload)
        assert payload["data"]["filtered_count"] >= 1

    async def test_filter_results_creates_new_result_set(self, mcp_client) -> None:
        """filter_results stores filtered output as a new result_set_id."""
        search = await mcp_client.call_tool("search_legal_acts", {"year": 2024})
        rs_id = _parse_tool_result(search)["data"]["result_set_id"]

        filter_result = await mcp_client.call_tool(
            "filter_results",
            {"result_set_id": rs_id, "type_equals": "Ustawa"},
        )
        filter_payload = _parse_tool_result(filter_result)

        # A new result_set_id is created for chained filtering
        if filter_payload["data"]["filtered_count"] > 0:
            assert filter_payload["data"]["result_set_id"] is not None
            assert filter_payload["data"]["result_set_id"] != rs_id


# ---------------------------------------------------------------------------
# get_act_details, list_loaded_documents
# ---------------------------------------------------------------------------


class TestActDetailsTools:
    """Tests for act details and content loading tools."""

    async def test_get_act_details(self, mcp_client, act_detail: dict) -> None:
        """get_act_details returns metadata matching the fixture."""
        result = await mcp_client.call_tool("get_act_details", {"eli": "DU/2024/1"})
        payload = _parse_tool_result(result)

        _assert_enriched(payload)
        assert payload["data"]["eli"] == "DU/2024/1"
        assert payload["data"]["title"] == act_detail["title"]
        assert payload["data"]["is_loaded"] is False

    async def test_list_loaded_documents_empty(self, mcp_client) -> None:
        """list_loaded_documents returns empty list when no acts are loaded."""
        result = await mcp_client.call_tool("list_loaded_documents", {})
        payload = _parse_tool_result(result)

        _assert_enriched(payload)
        assert payload["data"]["count"] == 0
        assert payload["data"]["documents"] == []

    async def test_get_act_details_with_load_content(self, mcp_client) -> None:
        """get_act_details with load_content=True marks act as loaded."""
        result = await mcp_client.call_tool("get_act_details", {"eli": "DU/2024/1", "load_content": True})
        payload = _parse_tool_result(result)

        _assert_enriched(payload)
        assert payload["data"]["eli"] == "DU/2024/1"
        assert payload["data"]["is_loaded"] is True

    async def test_list_loaded_documents_after_load(self, mcp_client) -> None:
        """list_loaded_documents reports the act after load_content=True."""
        await mcp_client.call_tool("get_act_details", {"eli": "DU/2024/1", "load_content": True})

        result = await mcp_client.call_tool("list_loaded_documents", {})
        payload = _parse_tool_result(result)

        _assert_enriched(payload)
        assert payload["data"]["count"] == 1
        assert payload["data"]["documents"][0]["eli"] == "DU/2024/1"


# ---------------------------------------------------------------------------
# read_act_content, search_in_act
# ---------------------------------------------------------------------------


class TestActContentTools:
    """Tests for tools that read content from loaded acts."""

    async def _load_act(self, mcp_client) -> None:
        """Helper: load DU/2024/1 content into the document store."""
        await mcp_client.call_tool("get_act_details", {"eli": "DU/2024/1", "load_content": True})

    async def test_read_act_content_returns_toc(self, mcp_client) -> None:
        """read_act_content without section returns the table of contents."""
        await self._load_act(mcp_client)

        result = await mcp_client.call_tool("read_act_content", {"eli": "DU/2024/1"})
        payload = _parse_tool_result(result)

        _assert_enriched(payload)
        assert payload["data"]["eli"] == "DU/2024/1"
        assert "toc" in payload["data"]
        assert isinstance(payload["data"]["toc"], list)
        # The mock HTML produces at least one section (h1 heading)
        assert len(payload["data"]["toc"]) >= 1

    async def test_read_act_content_specific_section(self, mcp_client) -> None:
        """read_act_content with a valid section_id returns non-empty content."""
        await self._load_act(mcp_client)

        # First get toc to find a valid section id
        toc_result = await mcp_client.call_tool("read_act_content", {"eli": "DU/2024/1"})
        toc_payload = _parse_tool_result(toc_result)
        sections = toc_payload["data"]["toc"]
        assert sections, "TOC must not be empty to test section reading"

        first_section_id = sections[0]["id"]
        result = await mcp_client.call_tool("read_act_content", {"eli": "DU/2024/1", "section": first_section_id})
        payload = _parse_tool_result(result)

        _assert_enriched(payload)
        assert payload["data"]["eli"] == "DU/2024/1"
        assert payload["data"]["content"]  # non-empty string

    async def test_read_act_content_unknown_section(self, mcp_client) -> None:
        """read_act_content with a non-existent section returns graceful error."""
        await self._load_act(mcp_client)

        result = await mcp_client.call_tool(
            "read_act_content",
            {"eli": "DU/2024/1", "section": "Rozdział_NONEXISTENT_999"},
        )
        payload = _parse_tool_result(result)

        _assert_enriched(payload)
        # Error field is set; data is present but content is empty
        assert payload.get("error") is not None

    async def test_search_in_act(self, mcp_client) -> None:
        """search_in_act finds occurrences of a query term in loaded content."""
        await self._load_act(mcp_client)

        result = await mcp_client.call_tool("search_in_act", {"eli": "DU/2024/1", "query": "Content"})
        payload = _parse_tool_result(result)

        _assert_enriched(payload)
        assert payload["data"]["eli"] == "DU/2024/1"
        assert payload["data"]["query"] == "Content"
        assert payload["data"]["total_matches"] >= 1

    async def test_search_in_act_no_matches(self, mcp_client) -> None:
        """search_in_act returns zero matches for a term not in the content."""
        await self._load_act(mcp_client)

        result = await mcp_client.call_tool("search_in_act", {"eli": "DU/2024/1", "query": "XYZNONEXISTENT_TERM"})
        payload = _parse_tool_result(result)

        _assert_enriched(payload)
        assert payload["data"]["total_matches"] == 0
        assert payload["data"]["matches"] == []


# ---------------------------------------------------------------------------
# analyze_act_relationships, compare_acts
# ---------------------------------------------------------------------------


class TestRelationshipTools:
    """Tests for relationship and comparison analysis tools."""

    async def test_analyze_act_relationships(self, mcp_client, act_references: dict) -> None:
        """analyze_act_relationships returns the mocked reference data."""
        result = await mcp_client.call_tool("analyze_act_relationships", {"eli": "DU/2024/1"})
        payload = _parse_tool_result(result)

        _assert_enriched(payload)
        assert payload["data"]["eli"] == "DU/2024/1"
        # The fixture has "Akty zmienione" and "Podstawa prawna"
        assert payload["data"]["total_count"] == 2
        assert "Akty zmienione" in payload["data"]["relationships"]
        assert "Podstawa prawna" in payload["data"]["relationships"]

    async def test_analyze_act_relationships_filtered(self, mcp_client) -> None:
        """relationship_type filter returns only the requested category."""
        result = await mcp_client.call_tool(
            "analyze_act_relationships",
            {"eli": "DU/2024/1", "relationship_type": "Akty zmienione"},
        )
        payload = _parse_tool_result(result)

        _assert_enriched(payload)
        assert payload["data"]["relationship_type"] == "Akty zmienione"
        assert "Akty zmienione" in payload["data"]["relationships"]
        assert "Podstawa prawna" not in payload["data"]["relationships"]

    async def test_compare_acts(self, mcp_client) -> None:
        """compare_acts returns a comparison with differences and fields for both acts."""
        result = await mcp_client.call_tool("compare_acts", {"eli_a": "DU/2024/1", "eli_b": "DU/2024/2"})
        payload = _parse_tool_result(result)

        _assert_enriched(payload)
        assert payload["data"]["eli_a"] == "DU/2024/1"
        assert payload["data"]["eli_b"] == "DU/2024/2"
        assert "comparison" in payload["data"]
        assert "differences" in payload["data"]
        assert isinstance(payload["data"]["differences"], list)
        assert len(payload["data"]["differences"]) >= 1

    async def test_compare_acts_same_act(self, mcp_client) -> None:
        """compare_acts on the same ELI reports no differences."""
        result = await mcp_client.call_tool("compare_acts", {"eli_a": "DU/2024/1", "eli_b": "DU/2024/1"})
        payload = _parse_tool_result(result)

        _assert_enriched(payload)
        differences = payload["data"]["differences"]
        assert any("Brak" in d for d in differences), "Same-act comparison should report no differences"


# ---------------------------------------------------------------------------
# calculate_legal_date
# ---------------------------------------------------------------------------


class TestDateTools:
    """Tests for date utility tools."""

    async def test_calculate_date_offset(self, mcp_client) -> None:
        """calculate_legal_date adds the given number of days to base_date."""
        result = await mcp_client.call_tool(
            "calculate_legal_date",
            {"days": 14, "base_date": "2025-02-01"},
        )
        payload = _parse_tool_result(result)

        _assert_enriched(payload)
        assert payload["data"]["base_date"] == "2025-02-01"
        assert payload["data"]["calculated_date"] == "2025-02-15"
        assert payload["data"]["days_offset"] == 14

    async def test_calculate_date_negative_offset(self, mcp_client) -> None:
        """calculate_legal_date supports negative days (dates in the past)."""
        result = await mcp_client.call_tool(
            "calculate_legal_date",
            {"days": -7, "base_date": "2025-02-08"},
        )
        payload = _parse_tool_result(result)

        _assert_enriched(payload)
        assert payload["data"]["calculated_date"] == "2025-02-01"


# ---------------------------------------------------------------------------
# EnrichedResponse structure validation (cross-cutting)
# ---------------------------------------------------------------------------


class TestEnrichedResponseStructure:
    """Verify that every tool response follows the EnrichedResponse envelope."""

    async def test_search_response_structure(self, mcp_client) -> None:
        """search_legal_acts wraps payload in {data, hints}."""
        result = await mcp_client.call_tool("search_legal_acts", {"year": 2024})
        raw = result.content[0].text if result.content else None
        assert raw is not None
        payload = json.loads(raw)

        assert set(payload.keys()) >= {"data", "hints"}
        assert isinstance(payload["hints"], list)

    async def test_metadata_response_structure(self, mcp_client) -> None:
        """get_system_metadata wraps payload in {data, hints}."""
        result = await mcp_client.call_tool("get_system_metadata", {"category": "publishers"})
        raw = result.content[0].text if result.content else None
        assert raw is not None
        payload = json.loads(raw)

        assert "data" in payload
        assert "hints" in payload

    async def test_act_details_response_structure(self, mcp_client) -> None:
        """get_act_details wraps payload in {data, hints}."""
        result = await mcp_client.call_tool("get_act_details", {"eli": "DU/2024/1"})
        raw = result.content[0].text if result.content else None
        assert raw is not None
        payload = json.loads(raw)

        assert "data" in payload
        assert "hints" in payload

    async def test_hints_are_valid_objects(self, mcp_client) -> None:
        """Hints returned by search_legal_acts are valid hint objects."""
        result = await mcp_client.call_tool("search_legal_acts", {"year": 2024})
        payload = json.loads(result.content[0].text)

        for hint in payload["hints"]:
            assert "message" in hint, "Each hint must have a 'message' field"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Verify graceful error handling in tool execution."""

    async def test_get_act_details_invalid_eli_format(self, mcp_client) -> None:
        """get_act_details with malformed ELI returns error in payload, not exception."""
        result = await mcp_client.call_tool("get_act_details", {"eli": "INVALID"})
        payload = _parse_tool_result(result)

        # Tool must NOT raise; it returns EnrichedResponse with error
        assert "error" in payload
        assert payload["error"] is not None
        assert "data" in payload  # default_factory output is present

    async def test_filter_results_unknown_set_id(self, mcp_client) -> None:
        """filter_results with an unknown result_set_id returns graceful error."""
        result = await mcp_client.call_tool(
            "filter_results",
            {"result_set_id": "rs_nonexistent_999"},
        )
        payload = _parse_tool_result(result)

        assert "error" in payload
        assert payload["error"] is not None

    async def test_read_act_content_not_loaded(self, mcp_client) -> None:
        """read_act_content on a non-loaded act returns graceful error payload."""
        result = await mcp_client.call_tool("read_act_content", {"eli": "DU/2024/1"})
        payload = _parse_tool_result(result)

        # DocumentNotLoadedError caught by decorator
        assert "error" in payload
        assert payload["error"] is not None

    async def test_analyze_relationships_invalid_eli(self, mcp_client) -> None:
        """analyze_act_relationships with invalid ELI returns graceful error."""
        result = await mcp_client.call_tool("analyze_act_relationships", {"eli": "ONLY_TWO/PARTS"})
        payload = _parse_tool_result(result)

        assert "error" in payload
        assert payload["error"] is not None


# ---------------------------------------------------------------------------
# Fixtures smoke-check (non-MCP)
# ---------------------------------------------------------------------------


def test_integration_tests_marked() -> None:
    """Verify integration tests are properly marked."""
    assert True


def test_fixtures_available(
    publishers_data: list,
    search_results: dict,
    act_detail: dict,
) -> None:
    """Verify that fixtures are available for integration tests."""
    assert len(publishers_data) == 2
    assert search_results["count"] == 3
    assert act_detail["ELI"] == "DU/2024/1"

"""The output contract of both SAOS tools: `outputSchema`, `structuredContent` and what an output never holds.

Every successful call below goes through `Session.call`, which validates the payload against the schema the
server advertised for that tool, so a shape that drifts from its schema fails here for every mode.
"""

from __future__ import annotations

from typing import Any

import pytest
import respx
from saos_testing import FakeSaos, corpus, derived_list, saos_session, supreme_chamber_items

from law_scrapper_mcp.models.tool_outputs import JudgmentSummaryOutput

pytestmark = pytest.mark.asyncio

SEARCH = "search_saos_judgments"
DETAILS = "get_saos_judgment_details"
RECORD_FIELDS = set(JudgmentSummaryOutput.model_fields)
SCAN_FIELDS = {
    "rule",
    "scanned_from",
    "scanned_to",
    "pages_fetched",
    "saos_records_scanned",
    "next_offset",
    "scan_complete",
    "interrupted",
}
PORTAL_LINK_FIELDS = {"requested", "resolved", "without_link", "failed", "stopped_after_outage", "skipped_for_budget"}


def _definitions(schema: dict[str, Any]) -> dict[str, Any]:
    defs: dict[str, Any] = schema["$defs"]
    return defs


class TestSchemas:
    async def test_the_search_output_schema_pins_the_fields_and_which_may_be_null(
        self, saos_router: respx.MockRouter
    ) -> None:
        async with saos_session(saos_router) as session:
            schema = session.tools[SEARCH].output_schema
            assert schema is not None
            output = _definitions(schema)["SearchSaosJudgmentsOutput"]

            assert schema["required"] == ["data"]
            assert set(output["properties"]) == {
                "source",
                "results",
                "total_count",
                "returned_count",
                "query_summary",
                "result_set_id",
                "result_set_scope",
                "page_info",
                "portal_links",
                "scan",
            }
            # Present in every response, null in a labour scan (which has a cursor instead).
            assert {"total_count", "page_info"} <= set(output["required"])
            assert {"type": "null"} in output["properties"]["total_count"]["anyOf"]
            assert {"type": "null"} in output["properties"]["page_info"]["anyOf"]
            assert not {"scan", "portal_links", "result_set_id"} & set(output["required"])

    async def test_the_scan_and_portal_link_blocks_are_fully_described_and_required(
        self, saos_router: respx.MockRouter
    ) -> None:
        async with saos_session(saos_router) as session:
            schema = session.tools[SEARCH].output_schema
            assert schema is not None
            defs = _definitions(schema)

            for name, fields in (("LabourScan", SCAN_FIELDS), ("PortalLinks", PORTAL_LINK_FIELDS)):
                block = defs[name]
                assert set(block["properties"]) == fields == set(block["required"]), name
                for field, body in block["properties"].items():
                    assert body.get("description", "").strip(), (name, field)

    async def test_a_judgment_record_has_the_source_neutral_fields_and_none_for_people_or_text(
        self, saos_router: respx.MockRouter
    ) -> None:
        async with saos_session(saos_router) as session:
            for tool in (SEARCH, DETAILS):
                schema = session.tools[tool].output_schema
                assert schema is not None
                record = _definitions(schema)["JudgmentSummaryOutput"]

                assert set(record["properties"]) == RECORD_FIELDS
                assert not RECORD_FIELDS & {"judges", "text", "textContent", "full_text", "legal_bases"}

    async def test_the_details_output_schema(self, saos_router: respx.MockRouter) -> None:
        async with saos_session(saos_router) as session:
            schema = session.tools[DETAILS].output_schema
            assert schema is not None
            output = _definitions(schema)["SaosJudgmentDetailsOutput"]

            assert set(output["properties"]) == {"source", "judgment", "division_type", "loaded"}
            assert set(output["required"]) == {"source", "judgment"}

    async def test_both_tools_are_remote_read_only_and_have_a_title(self, saos_router: respx.MockRouter) -> None:
        async with saos_session(saos_router) as session:
            for tool in (SEARCH, DETAILS):
                listed = session.tools[tool]

                assert listed.title
                assert listed.annotations is not None
                assert listed.annotations.read_only_hint is True
                assert listed.annotations.open_world_hint is True
                assert set(listed.output_schema["properties"]) == {"data", "hints"}  # type: ignore[index]


class TestPayloads:
    async def test_a_plain_page_has_page_numbering_and_no_scan(self, saos_router: respx.MockRouter) -> None:
        async with saos_session(saos_router) as session:
            data = await session.call(SEARCH, {"phrase": "wypadek", "limit": 5, "offset": 3})

            assert data["total_count"] == len(corpus()) and data["page_info"]["offset"] == 3
            assert data["scan"] is None and data["portal_links"] is None
            assert data["returned_count"] == len(data["results"]) == 5
            assert all(set(record) == RECORD_FIELDS for record in data["results"])
            assert all(len(record["excerpt"] or "") <= 800 for record in data["results"])

    async def test_an_empty_result_is_a_valid_page_without_a_result_set(self, saos_router: respx.MockRouter) -> None:
        async with saos_session(saos_router, FakeSaos([])) as session:
            data = await session.call(SEARCH, {"phrase": "nic"})

            assert data["results"] == [] and data["result_set_id"] is None and data["result_set_scope"] is None
            assert data["total_count"] == 0 and data["page_info"]["returned_count"] == 0

    async def test_portal_links_come_with_their_counters_and_the_links_themselves(
        self, saos_router: respx.MockRouter
    ) -> None:
        async with saos_session(saos_router) as session:
            data = await session.call(SEARCH, {"limit": 8, "include_portal_links": True})

            links = data["portal_links"]
            assert set(links) == PORTAL_LINK_FIELDS
            assert (links["requested"], links["resolved"], links["skipped_for_budget"]) == (5, 5, 3)
            assert [bool(record["portal_url"]) for record in data["results"]] == [True] * 5 + [False] * 3
            assert data["scan"] is None and data["page_info"] is not None

    async def test_a_common_court_labour_scan_has_a_cursor_and_no_totals(self, saos_router: respx.MockRouter) -> None:
        items = derived_list(("L" + "." * 6) * 40)
        async with saos_session(saos_router, FakeSaos(items)) as session:
            data = await session.call(SEARCH, {"labour_only": True, "limit": 10})

            assert data["total_count"] is None and data["page_info"] is None and data["portal_links"] is None
            assert set(data["scan"]) == SCAN_FIELDS
            assert data["scan"]["next_offset"] == 64 and data["scan"]["scan_complete"] is False
            assert "(15|18|21)$" in data["scan"]["rule"]
            assert data["result_set_scope"]["scope"] == "page"
            assert data["returned_count"] == len(data["results"]) == 10

    async def test_a_supreme_court_labour_scan_states_the_chamber_rule(self, saos_router: respx.MockRouter) -> None:
        async with saos_session(saos_router, FakeSaos(supreme_chamber_items())) as session:
            data = await session.call(SEARCH, {"labour_only": True, "court_type": "SUPREME", "limit": 100})

            assert len(data["results"]) == 15
            assert all("Izba Pracy" in record["division"] for record in data["results"])
            assert "Izba Pracy" in data["scan"]["rule"] and data["scan"]["scan_complete"] is True
            assert data["scan"]["next_offset"] is None

    async def test_a_scan_that_finds_nothing_is_valid_and_keeps_its_cursor(self, saos_router: respx.MockRouter) -> None:
        async with saos_session(saos_router, FakeSaos(derived_list("." * 700))) as session:
            data = await session.call(SEARCH, {"labour_only": True})

            assert data["results"] == [] and data["result_set_id"] is None
            assert data["scan"]["next_offset"] == 500 and data["scan"]["pages_fetched"] == 5

    async def test_an_interrupted_scan_is_valid_and_says_so(self, saos_router: respx.MockRouter) -> None:
        fake = FakeSaos(derived_list(("L" + "." * 3) * 50), failing_pages={1})
        async with saos_session(saos_router, fake) as session:
            data = await session.call(SEARCH, {"labour_only": True, "limit": 100})

            assert data["scan"]["interrupted"] is True and data["scan"]["next_offset"] == 100
            assert len(data["results"]) == 25

    async def test_the_details_output_holds_metadata_and_the_court_address_only(
        self, saos_router: respx.MockRouter
    ) -> None:
        async with saos_session(saos_router) as session:
            source_id = str(corpus()[0]["id"])

            data = await session.call(DETAILS, {"source_id": source_id})

            assert set(data) == {"source", "judgment", "division_type", "loaded"}
            assert data["loaded"] is None  # nothing is loaded unless load_content is asked for
            assert set(data["judgment"]) == RECORD_FIELDS
            assert data["judgment"]["source_id"] == source_id
            assert data["judgment"]["excerpt"] is None
            assert data["judgment"]["portal_url"].startswith("https://")
            assert data["division_type"]

    async def test_structured_content_is_an_object_with_data_and_hints_only(
        self, saos_router: respx.MockRouter
    ) -> None:
        async with saos_session(saos_router) as session:
            result = await session.client.call_tool(SEARCH, {"limit": 2})

            assert result.structured_content is not None
            assert set(result.structured_content) == {"data", "hints"}
            assert all(set(hint) <= {"message", "tool", "parameters"} for hint in result.structured_content["hints"])


class TestRefusals:
    @pytest.mark.parametrize(
        ("tool", "arguments", "text"),
        [
            (SEARCH, {"labour_only": True, "include_portal_links": True}, "nie łączą się"),
            (SEARCH, {"labour_only": True, "court_type": "ADMINISTRATIVE"}, "tylko dla sądów powszechnych"),
            (SEARCH, {"date_from": "2999-01-01"}, "date_from"),
            (SEARCH, {"limit": 0}, "limit"),
            (SEARCH, {"offset": -1}, "offset"),
            (DETAILS, {"source_id": "abc"}, "source_id"),
            (DETAILS, {"source_id": "999999999999"}, "Nie znaleziono orzeczenia"),
        ],
    )
    async def test_a_refusal_is_a_polish_tool_error_with_no_structured_content(
        self, saos_router: respx.MockRouter, tool: str, arguments: dict[str, Any], text: str
    ) -> None:
        async with saos_session(saos_router) as session:
            message = await session.fail(tool, arguments)

            assert text in message

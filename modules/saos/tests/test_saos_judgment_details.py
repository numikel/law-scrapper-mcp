"""`get_saos_judgment_details`: parsing of the recorded detail, URL validation, errors, nothing but metadata."""

from __future__ import annotations

import re
from typing import Any
from unittest.mock import Mock

import jsonschema
import pytest
import respx
from saos_testing import (
    DETAIL_FIXTURE,
    FakeSaos,
    HarnessFactory,
    connected,
    corpus,
    detail_body,
    portal_url_for,
    recorded_portal_url,
    recording,
)

from law_scrapper_mcp.client.exceptions import ApiUnavailableError, SejmApiError
from law_scrapper_mcp.context import AppContext
from law_scrapper_mcp.services.result_store import ResultStore
from law_scrapper_mcp_saos.mapping import PORTAL_URL_LIMIT, portal_url_from, to_detail
from law_scrapper_mcp_saos.models import SaosDetail
from law_scrapper_mcp_saos.service import TOOL_SEARCH, get_saos_judgment_details
from law_scrapper_mcp_saos.state import MODULE_NAME, SaosState

CORPUS = corpus()
LABOUR_ID = CORPUS[0]["id"]
TOOL = "get_saos_judgment_details"


def _context(harness_client: Any) -> AppContext:
    state = SaosState(client=harness_client, module="law-scrapper-mcp-saos", version="0.1.0")
    return Mock(spec=AppContext, module_state={MODULE_NAME: state}, result_store=ResultStore())


class TestRecordedDetail:
    def test_the_stored_reduction_parses_into_the_models(self) -> None:
        detail = SaosDetail.model_validate(recording(DETAIL_FIXTURE))
        record, division_type = to_detail(detail.data)

        assert detail.data.id == 554697
        assert record.source_id == "554697"
        assert record.portal_url == recorded_portal_url()
        assert record.portal_url is not None and record.portal_url.startswith("https://")
        assert division_type == "Pracy i Ubezpieczeń Społecznych"
        assert record.excerpt is None

    def test_a_detail_derived_from_a_search_item_maps_to_the_same_record_fields(self) -> None:
        item = CORPUS[0]
        record, division_type = to_detail(SaosDetail.model_validate(detail_body(item)).data)

        assert record.signature == item["courtCases"][0]["caseNumber"]
        assert record.court == item["division"]["court"]["name"]
        assert record.division == item["division"]["name"]
        assert record.division_code == item["division"]["code"]
        assert record.judgment_date == item["judgmentDate"]
        assert record.portal_url == portal_url_for(item["id"])
        assert division_type == "Pracy i Ubezpieczeń Społecznych"

    def test_a_detail_without_source_or_division_type_has_neither(self) -> None:
        body = detail_body(CORPUS[0], with_division_type=False)
        del body["data"]["source"]
        record, division_type = to_detail(SaosDetail.model_validate(body).data)

        assert record.portal_url is None
        assert division_type is None

    def test_a_detail_of_a_court_without_division_still_maps(self) -> None:
        tribunal = next(item for item in CORPUS if "division" not in item)
        record, division_type = to_detail(SaosDetail.model_validate(detail_body(tribunal)).data)

        assert record.division is None
        assert division_type is None
        assert record.portal_url == portal_url_for(tribunal["id"])

    @pytest.mark.parametrize("body", [{}, {"links": []}, {"data": {}}, {"data": {"id": "abc"}}, []])
    def test_a_body_that_is_not_a_detail_is_refused(self, body: object) -> None:
        with pytest.raises(ValueError):
            SaosDetail.model_validate(body)


class TestPortalUrl:
    @pytest.mark.parametrize(
        "address",
        [
            "https://apiorzeczenia.example.test/details?id=1",
            "http://portal.example.test/orzeczenie/1",
            "https://portal.example.test:8443/a/b?c=d#e",
            "HTTPS://PORTAL.EXAMPLE.TEST/x",
            "https://" + "a" * 20 + ".test/" + "p" * (PORTAL_URL_LIMIT - 40),
        ],
    )
    def test_http_and_https_addresses_are_passed_on_unchanged(self, address: str) -> None:
        assert len(address) <= PORTAL_URL_LIMIT
        assert portal_url_from(address) == address

    @pytest.mark.parametrize(
        "address",
        [
            None,
            "",
            "ftp://portal.example.test/1",
            "javascript:alert(1)",
            "data:text/html,<script>1</script>",
            "file:///etc/passwd",
            "//portal.example.test/1",
            "/relative/path",
            "portal.example.test/1",
            "https://",
            "https:///path",
            "https://portal.example.test/a b",
            "https://portal.example.test/\r\nX-Evil: 1",
            "https://portal.example.test/\x00",
            "https://portal.example.test/\x7f",
            " https://portal.example.test/1",
            "https://portal.example.test:notaport/1",
        ],
    )
    def test_anything_else_becomes_none(self, address: str | None) -> None:
        assert portal_url_from(address) is None

    def test_the_length_limit_is_inclusive(self) -> None:
        prefix = "https://portal.example.test/"
        at_limit = prefix + "x" * (PORTAL_URL_LIMIT - len(prefix))

        assert len(at_limit) == PORTAL_URL_LIMIT == 2048
        assert portal_url_from(at_limit) == at_limit
        assert portal_url_from(at_limit + "x") is None


class TestService:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("given", ["554697", 554697, " 554697 "])
    async def test_one_request_and_metadata_only(self, make_harness: HarnessFactory, given: str | int) -> None:
        fake = FakeSaos(details={554697: recording(DETAIL_FIXTURE)})
        harness = make_harness(fake)

        response = await get_saos_judgment_details(_context(harness.client), source_id=given)

        assert fake.detail_urls == ["https://www.saos.org.pl/api/judgments/554697"]
        assert fake.calls == []
        assert response.data.source == "saos"
        assert response.data.judgment.source_id == "554697"
        assert response.data.judgment.portal_url == recorded_portal_url()
        assert response.data.division_type == "Pracy i Ubezpieczeń Społecznych"

    @pytest.mark.asyncio
    async def test_the_output_holds_no_text_judge_or_staff_even_when_the_detail_does(
        self, make_harness: HarnessFactory
    ) -> None:
        item = next(record for record in CORPUS if record.get("judges"))
        body = detail_body(item)
        assert len(body["data"]["textContent"]) > 10_000
        assert body["data"]["judges"]
        body["data"]["courtReporters"] = [{"name": "Imię Nazwisko"}]
        harness = make_harness(FakeSaos(details={item["id"]: body}))

        response = await get_saos_judgment_details(_context(harness.client), source_id=item["id"])
        dumped = response.model_dump_json()

        assert response.data.judgment.excerpt is None
        assert item["textContent"][:60] not in dumped
        assert "[redacted]" not in dumped
        assert "PRESIDING_JUDGE" not in dumped
        assert "Imię Nazwisko" not in dumped
        assert "judges" not in dumped
        assert "courtReporters" not in dumped

    @pytest.mark.asyncio
    async def test_a_detail_is_never_cached(self, make_harness: HarnessFactory) -> None:
        harness = make_harness()
        context = _context(harness.client)

        await get_saos_judgment_details(context, source_id=LABOUR_ID)
        await get_saos_judgment_details(context, source_id=LABOUR_ID)

        assert harness.fake.detail_ids == [LABOUR_ID, LABOUR_ID]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "bad", ["", "abc", "12 3", "../x", "554697/text", "-1", "1.5", "0x10", "5" * 13, "٣٤", "554697?x=1", "\n"]
    )
    async def test_an_identifier_that_is_not_a_number_never_reaches_the_wire(
        self, make_harness: HarnessFactory, bad: str
    ) -> None:
        harness = make_harness()

        with pytest.raises(ValueError, match="source_id"):
            await get_saos_judgment_details(_context(harness.client), source_id=bad)

        assert harness.fake.detail_urls == []

    @pytest.mark.asyncio
    async def test_an_unknown_judgment_is_a_polish_not_found_message(self, make_harness: HarnessFactory) -> None:
        harness = make_harness(FakeSaos(missing_details={LABOUR_ID}))

        with pytest.raises(ValueError, match=rf"Nie znaleziono orzeczenia o identyfikatorze {LABOUR_ID}") as excinfo:
            await get_saos_judgment_details(_context(harness.client), source_id=LABOUR_ID)

        assert "aktu" not in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_an_outage_is_an_unavailable_error_naming_the_source(self, make_harness: HarnessFactory) -> None:
        harness = make_harness(FakeSaos(failing_details={LABOUR_ID}))

        with pytest.raises(ApiUnavailableError) as excinfo:
            await get_saos_judgment_details(_context(harness.client), source_id=LABOUR_ID)

        assert excinfo.value.source == "SAOS"

    @pytest.mark.asyncio
    async def test_an_answer_that_is_not_a_detail_is_an_upstream_error(self, make_harness: HarnessFactory) -> None:
        harness = make_harness(FakeSaos(details={LABOUR_ID: {"links": []}}))

        with pytest.raises(SejmApiError) as excinfo:
            await get_saos_judgment_details(_context(harness.client), source_id=LABOUR_ID)

        assert excinfo.value.source == "SAOS"

    @pytest.mark.asyncio
    async def test_hints_give_the_portal_address_with_its_caveat_and_the_other_record(
        self, make_harness: HarnessFactory
    ) -> None:
        harness = make_harness()

        response = await get_saos_judgment_details(_context(harness.client), source_id=LABOUR_ID)

        first, second = response.hints
        assert portal_url_for(LABOUR_ID) in first.message
        assert "serwer go nie pobiera" in first.message
        assert second.tool == TOOL_SEARCH
        assert second.parameters == {"signature": response.data.judgment.signature}

    @pytest.mark.asyncio
    async def test_hints_say_when_there_is_no_usable_address(self, make_harness: HarnessFactory) -> None:
        body = detail_body(CORPUS[0], portal_url="javascript:alert(1)")
        harness = make_harness(FakeSaos(details={LABOUR_ID: body}))

        response = await get_saos_judgment_details(_context(harness.client), source_id=LABOUR_ID)

        assert response.data.judgment.portal_url is None
        assert "nie podaje" in response.hints[0].message


class TestTool:
    @pytest.mark.asyncio
    async def test_registration_schema_and_description(self, saos_router: respx.MockRouter) -> None:
        async with connected(saos_router) as client:
            tool = next(t for t in (await client.list_tools()).tools if t.name == TOOL)

        assert tool.title == "Szczegóły orzeczenia z SAOS"
        assert tool.annotations is not None
        assert (tool.annotations.read_only_hint, tool.annotations.open_world_hint) == (True, True)
        assert set(tool.input_schema["properties"]) == {"source_id"}
        assert tool.input_schema["required"] == ["source_id"]
        assert tool.input_schema["properties"]["source_id"]["description"].strip()
        assert set(tool.output_schema["properties"]) == {"data", "hints"}  # type: ignore[index]
        description = tool.description or ""
        assert "Kiedy użyć:" in description and "Kiedy NIE używać:" in description
        assert len(re.findall(rf"^\s*- {TOOL}\(", description, flags=re.MULTILINE)) >= 5
        assert "include_portal_links" in description

    @pytest.mark.asyncio
    async def test_a_call_returns_output_that_validates_against_the_schema(self, saos_router: respx.MockRouter) -> None:
        async with connected(saos_router) as client:
            tool = next(t for t in (await client.list_tools()).tools if t.name == TOOL)
            result = await client.call_tool(TOOL, {"source_id": str(LABOUR_ID)})

        assert result.is_error is False
        payload = result.structured_content
        assert payload is not None
        jsonschema.validate(payload, tool.output_schema)
        judgment = payload["data"]["judgment"]
        assert judgment["portal_url"] == portal_url_for(LABOUR_ID)
        assert judgment["source_url"] == f"https://www.saos.org.pl/judgments/{LABOUR_ID}"
        assert judgment["excerpt"] is None
        assert payload["data"]["division_type"] == "Pracy i Ubezpieczeń Społecznych"
        assert payload["hints"]

    @pytest.mark.asyncio
    async def test_errors_are_polish_tool_errors(self, saos_router: respx.MockRouter) -> None:
        async with connected(saos_router, FakeSaos(missing_details={LABOUR_ID})) as client:
            bad_id = await client.call_tool(TOOL, {"source_id": "abc"})
            missing = await client.call_tool(TOOL, {"source_id": str(LABOUR_ID)})

        assert bad_id.is_error is True and missing.is_error is True
        assert "source_id" in bad_id.content[0].text  # type: ignore[union-attr]
        assert "Nie znaleziono orzeczenia" in missing.content[0].text  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_an_outage_names_the_source(self, saos_router: respx.MockRouter) -> None:
        async with connected(saos_router, FakeSaos(failing_details={LABOUR_ID})) as client:
            result = await client.call_tool(TOOL, {"source_id": str(LABOUR_ID)})

        assert result.is_error is True
        text = result.content[0].text  # type: ignore[union-attr]
        assert "SAOS" in text
        assert "Pozostałe źródła nie są objęte tym błędem" in text

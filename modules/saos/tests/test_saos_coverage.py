"""SAOS coverage: the provider on the recordings, the fallback constants and their registration.

Every SAOS answer is mocked by `respx` from the recordings of 2026-11-02 and the network guard of
`conftest.py` stays on. The fallback constants in `src/` are tied here, and only here, to the recordings they
came from; the fallback itself reads no file.
"""

from __future__ import annotations

import builtins
import re
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
import respx
from saos_testing import IDENTITY, RECORDINGS, SEARCH_URL, make_saos_client, recording

from law_scrapper_mcp.client.egress import EgressHub
from law_scrapper_mcp.client.exceptions import SejmApiError
from law_scrapper_mcp.config import Settings
from law_scrapper_mcp.services.act_sources import ActSourceRegistry
from law_scrapper_mcp.services.coverage import CoverageRegistry
from law_scrapper_mcp.source_modules.contract import ModuleHost
from law_scrapper_mcp_saos import module, start
from law_scrapper_mcp_saos.coverage import (
    COURT_LABELS,
    COVERAGE_KEY,
    FALLBACK_MEASURED_ON,
    SaosCoverageProvider,
    court_gap_message,
)
from law_scrapper_mcp_saos.mapping import COURT_TYPES

# Court type -> the recording of its newest judgments (`info.totalResults` and the first item).
RECORDED = {
    "COMMON": "search_common_newest_le_today.json",
    "SUPREME": "search_supreme_newest_le_today.json",
    "CONSTITUTIONAL_TRIBUNAL": "search_newest_constitutional_tribunal.json",
    "NATIONAL_APPEAL_CHAMBER": "search_newest_national_appeal_chamber.json",
    "ADMINISTRATIVE": "search_newest_administrative.json",
}
MEASUREMENT_DAY = date(2026, 11, 2)


def _serve_recordings(router: respx.MockRouter) -> respx.Route:
    """Answer every measurement request with the recording of the court type it asks for."""

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=recording(RECORDED[request.url.params["courtType"]]))

    return router.get(SEARCH_URL).mock(side_effect=answer)


@pytest_asyncio.fixture
async def provider() -> AsyncIterator[SaosCoverageProvider]:
    client, hub = make_saos_client()
    yield SaosCoverageProvider(client)
    await hub.close()


# --- the provider on the recordings ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_measurement_is_five_requests_with_the_mandatory_upper_bound_and_newest_first(
    saos_router: respx.MockRouter, provider: SaosCoverageProvider
) -> None:
    route = _serve_recordings(saos_router)

    coverage = await provider.measure(MEASUREMENT_DAY)

    assert route.call_count == 5
    assert [dict(call.request.url.params) for call in route.calls] == [
        {
            "pageSize": "1",
            "courtType": court_type,
            "judgmentDateTo": "2026-11-02",
            "sortingField": "JUDGMENT_DATE",
            "sortingDirection": "DESC",
        }
        for court_type in COURT_TYPES
    ]
    assert coverage.source == "saos" and coverage.origin == "measured" and coverage.measured_on == MEASUREMENT_DAY


@pytest.mark.asyncio
async def test_every_segment_equals_its_recording(
    saos_router: respx.MockRouter, provider: SaosCoverageProvider
) -> None:
    _serve_recordings(saos_router)

    coverage = await provider.measure(MEASUREMENT_DAY)

    assert [segment.key for segment in coverage.segments] == list(COURT_TYPES)
    for segment in coverage.segments:
        page = recording(RECORDED[segment.key])
        assert segment.label == COURT_LABELS[segment.key]
        assert segment.count == page["info"]["totalResults"], segment.key
        assert segment.years is None
        if page["items"]:
            first = page["items"][0]
            assert segment.newest == date.fromisoformat(first["judgmentDate"]), segment.key
            numbers = [case["caseNumber"] for case in first.get("courtCases", []) if case.get("caseNumber")]
            assert segment.newest_ref == (numbers[0] if numbers else None), segment.key
        else:
            assert (segment.newest, segment.newest_ref) == (None, None), segment.key


@pytest.mark.asyncio
async def test_the_administrative_courts_measure_as_empty(
    saos_router: respx.MockRouter, provider: SaosCoverageProvider
) -> None:
    _serve_recordings(saos_router)

    coverage = await provider.measure(MEASUREMENT_DAY)

    administrative = next(segment for segment in coverage.segments if segment.key == "ADMINISTRATIVE")
    assert (administrative.count, administrative.newest, administrative.newest_ref) == (0, None, None)


@pytest.mark.asyncio
async def test_one_failing_court_type_fails_the_whole_measurement(
    saos_router: respx.MockRouter, provider: SaosCoverageProvider
) -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.params["courtType"] == "NATIONAL_APPEAL_CHAMBER":
            return httpx.Response(400, json={"error": {"httpStatus": "400"}})
        return httpx.Response(200, json=recording(RECORDED[request.url.params["courtType"]]))

    saos_router.get(SEARCH_URL).mock(side_effect=answer)

    with pytest.raises(SejmApiError, match="HTTP 400"):
        await provider.measure(MEASUREMENT_DAY)


@pytest.mark.asyncio
async def test_the_registry_serves_the_measurement_for_a_day_and_falls_back_when_saos_fails(
    saos_router: respx.MockRouter, provider: SaosCoverageProvider
) -> None:
    registry = CoverageRegistry(today=lambda: MEASUREMENT_DAY)
    registry.register(provider)
    route = _serve_recordings(saos_router)

    first = await registry.get(COVERAGE_KEY)
    second = await registry.get(COVERAGE_KEY)

    assert first is second and first is not None and first.origin == "measured"
    assert route.call_count == 5, "the second question is answered from the memo"

    # A client of its own: the first one remembers the pages it fetched.
    client, hub = make_saos_client()
    try:
        broken = CoverageRegistry(today=lambda: MEASUREMENT_DAY)
        broken.register(SaosCoverageProvider(client))
        route.mock(return_value=httpx.Response(400, json={"error": {"httpStatus": "400"}}))
        answered = await broken.get(COVERAGE_KEY)
    finally:
        await hub.close()

    assert answered is not None and answered.origin == "fallback" and answered.measured_on == FALLBACK_MEASURED_ON


# --- the fallback constants -----------------------------------------------------------------------


def test_the_fallback_equals_the_recordings_of_2026_11_02(provider: SaosCoverageProvider) -> None:
    fallback = provider.fallback()

    assert fallback.origin == "fallback" and fallback.source == "saos"
    assert fallback.measured_on == FALLBACK_MEASURED_ON == MEASUREMENT_DAY
    assert [segment.key for segment in fallback.segments] == list(COURT_TYPES)
    for segment in fallback.segments:
        page = recording(RECORDED[segment.key])
        assert segment.count == page["info"]["totalResults"], segment.key
        assert segment.label == COURT_LABELS[segment.key]
        assert segment.years is None and segment.newest_ref is None
        expected = date.fromisoformat(page["items"][0]["judgmentDate"]) if page["items"] else None
        assert segment.newest == expected, segment.key


def test_the_recordings_were_captured_with_the_measurement_day_as_the_upper_bound() -> None:
    provenance = (RECORDINGS / "PROVENANCE.md").read_text(encoding="utf-8")
    bounds = {
        re.search(r"judgmentDateTo=(\d{4}-\d{2}-\d{2})", line).group(1)  # type: ignore[union-attr]
        for line in provenance.splitlines()
        # Capture-table rows only: the redaction table names the same files without a request.
        if "https://" in line and line.startswith(tuple(f"| `{name}`" for name in RECORDED.values()))
    }

    assert bounds == {FALLBACK_MEASURED_ON.isoformat()}


def test_the_fallback_reads_no_file(monkeypatch: pytest.MonkeyPatch, provider: SaosCoverageProvider) -> None:
    def refuse(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("the fallback must not read a file")

    with monkeypatch.context() as patched:
        patched.setattr(builtins, "open", refuse)
        patched.setattr(Path, "read_text", refuse)
        patched.setattr(Path, "open", refuse)
        fallback = provider.fallback()

    assert len(fallback.segments) == 5


# --- registration ---------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_start_hook_registers_the_provider_under_saos_and_removes_it_on_exit() -> None:
    from law_scrapper_mcp.client.cache import TTLCache
    from law_scrapper_mcp.client.egress import EgressDefaults

    hub = EgressHub(TTLCache(max_entries=10), EgressDefaults(user_agent=Settings().user_agent))
    host = ModuleHost(egress=hub, caller=IDENTITY, settings=Settings(), act_sources=ActSourceRegistry())
    try:
        async with start(host) as state:
            assert host.coverage.keys() == ("saos",) == (COVERAGE_KEY,)
            assert state.coverage is host.coverage
        assert host.coverage.keys() == ()
    finally:
        await hub.close()


def test_the_descriptor_still_declares_the_two_tools_and_a_start_hook() -> None:
    assert module.tool_names == ("search_saos_judgments", "get_saos_judgment_details")
    assert module.start is start


# --- the gap rule on its own ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("date_to", "expected"),
    [(date(2016, 7, 22), False), (date(2016, 7, 23), True), (date(2016, 6, 22), False), (date(2026, 11, 2), True)],
)
def test_the_gap_starts_on_the_thirty_first_day_past_the_newest(
    provider: SaosCoverageProvider, date_to: date, expected: bool
) -> None:
    segment = next(s for s in provider.fallback().segments if s.key == "SUPREME")

    message = court_gap_message(segment, date_to=date_to, origin="measured", measured_on=date(2026, 11, 8))

    assert (message is not None) is expected
    if message is not None:
        assert message.startswith("Sąd Najwyższy w SAOS sięga do 2016-06-22 (pomiar z 2026-11-08);")
        assert "nie dowodzi" in message


def test_a_segment_with_no_newest_date_makes_no_claim(provider: SaosCoverageProvider) -> None:
    segment = next(s for s in provider.fallback().segments if s.key == "ADMINISTRATIVE")

    assert court_gap_message(segment, date_to=date(2026, 11, 2), origin="measured", measured_on=MEASUREMENT_DAY) is None


@pytest.mark.asyncio
async def test_through_the_server_the_category_lists_five_segments_and_a_supreme_search_gets_the_gap(
    saos_router: respx.MockRouter,
) -> None:
    """The real start hook, the real registry and the real tools; only the SAOS host is mocked."""
    from mcp import Client
    from saos_testing import loaded

    from law_scrapper_mcp.server import build_app

    def answer(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        court_type = params.get("courtType", "COMMON")
        return httpx.Response(200, json=recording(RECORDED.get(court_type, RECORDED["COMMON"])))

    saos_router.get(SEARCH_URL).mock(side_effect=answer)

    async with Client(build_app([loaded()])) as client:
        metadata = await client.call_tool("get_system_metadata", {"category": "coverage"})
        search = await client.call_tool("search_saos_judgments", {"court_type": "SUPREME", "limit": 3})

    assert metadata.is_error is False and metadata.structured_content is not None
    entries = [e for e in metadata.structured_content["data"]["metadata"]["coverage"] if e["source"] == "saos"]
    assert [e["segment"] for e in entries] == list(COURT_TYPES)
    assert {e["origin"] for e in entries} == {"measured"}
    assert search.is_error is False and search.structured_content is not None
    messages = [hint["message"] for hint in search.structured_content["hints"]]
    assert any(m.startswith("Sąd Najwyższy w SAOS sięga do 2016-06-22 (pomiar z ") for m in messages)

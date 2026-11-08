"""The module README states facts that live in code; these tests keep the two from drifting apart."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import respx
from saos_testing import saos_session

from law_scrapper_mcp.config import Settings
from law_scrapper_mcp_saos.mapping import LABOUR_CHAMBER_PATTERN, LABOUR_DIVISION_PATTERN
from law_scrapper_mcp_saos.service import (
    PORTAL_LINK_CAP,
    REQUEST_BUDGET,
    SCAN_MAX_PAGES,
)
from law_scrapper_mcp_saos.state import RATE_BURST, RATE_PER_SECOND

README = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
RECORDED_LABOUR_CHAMBER = "Izba Pracy, Ubezpieczeń Społecznych i Spraw Publicznych"


def test_the_readme_points_at_the_coverage_category_and_quotes_no_frozen_figures() -> None:
    from law_scrapper_mcp.services.coverage import STALE_AFTER_DAYS
    from law_scrapper_mcp_saos.coverage import FALLBACK_MEASURED_ON

    flat = " ".join(README.split())

    assert 'get_system_metadata(category="coverage")' in README
    assert f"more than {STALE_AFTER_DAYS} days past the newest judgment" in flat
    assert FALLBACK_MEASURED_ON.isoformat() in README
    # The old static list of counts is gone: the numbers are measured, and the fallback lives in code.
    for figure in ("474 859", "38 081", "9 503", "22 168"):
        assert figure not in README, figure


def test_the_pace_and_the_request_budgets_are_the_ones_in_code() -> None:
    assert f"{RATE_PER_SECOND:g} requests per second with a burst of {RATE_BURST}" in README
    assert f"first {PORTAL_LINK_CAP} results" in README
    assert f"never more than {REQUEST_BUDGET} in all" in README
    assert f"at most {SCAN_MAX_PAGES} per call" in README


def test_the_labour_rules_are_the_ones_in_code() -> None:
    for text in (LABOUR_DIVISION_PATTERN, "(15|21)$", LABOUR_CHAMBER_PATTERN, RECORDED_LABOUR_CHAMBER, "2016-06-22"):
        assert text in README, text


def test_the_settings_it_names_exist() -> None:
    named = set(re.findall(r"LAW_MCP_([A-Z_]+)", README)) - {"SOURCE_MODULES"}
    fields = {name.upper() for name in Settings.model_fields}

    assert named == {
        "CACHE_SEARCH_TTL",
        "FILTER_MAX_RECORDS",
        "API_RATE_PER_SECOND",
        "API_RATE_BURST",
        "DOC_STORE_TTL",
        "DOC_STORE_MAX_DOCUMENTS",
        "DOC_STORE_MAX_SIZE_BYTES",
        "CONTACT",
    }
    assert named <= fields


def test_the_identity_sentence_names_the_operators_contact_and_its_default() -> None:
    from law_scrapper_mcp.config import USER_AGENT_CONTACT

    lowered = " ".join(README.split())

    assert "the operator's contact, `LAW_MCP_CONTACT`, which defaults to the project repository" in lowered
    assert "project address" not in lowered
    assert USER_AGENT_CONTACT.startswith("https://github.com/")


def test_the_reading_section_states_what_the_code_does() -> None:
    from law_scrapper_mcp_saos.models import ReasoningStatus
    from law_scrapper_mcp_saos.reading import SERVER_NOTE_TITLE
    from law_scrapper_mcp_saos.service import MAX_READ_REQUESTS, REFUSAL_STATUSES

    assert "load_content" in README
    assert f"never more than {MAX_READ_REQUESTS}" in README
    assert f"up to {['zero', 'one', 'two', 'three'][MAX_READ_REQUESTS]}" in README
    assert SERVER_NOTE_TITLE in README
    for status in ReasoningStatus:
        assert f"`{status.value}`" in README, status
    for code in sorted(REFUSAL_STATUSES):
        assert str(code) in README, code
    assert "SAOS/554697" in README


@pytest.mark.asyncio
async def test_it_names_both_tools_and_every_parameter_of_the_search(saos_router: respx.MockRouter) -> None:
    async with saos_session(saos_router) as session:
        search = session.tools["search_saos_judgments"]

        assert "get_saos_judgment_details" in README
        for parameter in search.input_schema["properties"]:
            assert parameter in README, parameter


def test_it_claims_no_licence_and_no_permission_to_quote() -> None:
    lowered = " ".join(README.lower().split())

    assert "publishes no licence" in lowered
    assert "no permission was requested or obtained" in lowered
    assert "do not read this module as a statement that quoting" in lowered
    assert "licensed under" not in lowered and "permitted to quote" not in lowered


def test_it_carries_no_planning_identifiers() -> None:
    """Planning identifiers belong to the private workspace, not to a public README."""
    identifiers = re.compile(r"(?<![A-Za-z])(?:F|S|FR|NFR)-\d{2,3}(?!\d)", re.IGNORECASE)

    assert not identifiers.search(README)

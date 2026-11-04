"""The code-to-host table: 16 journals, 12 served, 4 withheld, each with a reason."""

from __future__ import annotations

import re

import pytest
from journal_support import KP, PM, load_json

from law_scrapper_mcp_voivodeship_journals.catalog import BY_CODE, CODE_PREFIX, JOURNALS, SERVED, WITHHELD, Journal


def test_there_are_sixteen_journals_with_unique_codes_and_hosts() -> None:
    assert len(JOURNALS) == 16
    assert len({journal.code for journal in JOURNALS}) == 16
    assert len({journal.host for journal in JOURNALS}) == 16
    assert all(re.fullmatch(rf"{CODE_PREFIX}[A-Z]{{2}}", journal.code) for journal in JOURNALS)


def test_the_withheld_set_is_exactly_mazovia_and_the_three_incomplete_tls_chains() -> None:
    assert {journal.code for journal in WITHHELD} == {"POL_WOJ_MZ", "POL_WOJ_LS", "POL_WOJ_LD", "POL_WOJ_MP"}
    assert len(SERVED) == 12
    assert all(journal.withheld for journal in WITHHELD)
    assert all(journal.withheld is None for journal in SERVED)


def test_every_withheld_journal_states_its_reason_in_polish() -> None:
    reasons = {journal.code: journal.withheld for journal in WITHHELD}

    assert "nie odpowiada" in (reasons["POL_WOJ_MZ"] or "")
    for code in ("POL_WOJ_LS", "POL_WOJ_LD", "POL_WOJ_MP"):
        assert "TLS" in (reasons[code] or "")


def test_the_hosts_of_the_recorded_journals_match_the_recordings() -> None:
    """The recordings name the host each publisher code was captured from; the table must agree."""
    assert BY_CODE["POL_WOJ_PM"].base_url == PM
    assert BY_CODE["POL_WOJ_KP"].base_url == KP
    assert load_json("publishers_camel.json")[0]["code"] in BY_CODE
    assert load_json("publishers_pascal.json")[0]["Code"] in BY_CODE


def test_publisher_codes_found_in_recorded_search_pages_are_in_the_table() -> None:
    camel = {item["publisher"] for item in load_json("search_page_camel.json")["items"]}
    pascal = {item["Publisher"] for item in load_json("search_page_pascal.json")["Items"]}

    assert (camel | pascal) <= set(BY_CODE)


def test_base_urls_are_https_and_pdf_urls_follow_the_host() -> None:
    assert all(journal.base_url.startswith("https://") for journal in JOURNALS)
    assert BY_CODE["POL_WOJ_PM"].pdf_url("POL_WOJ_PM", 2025, 5051) == f"{PM}/acts/POL_WOJ_PM/2025/5051/text.pdf"


def test_a_duplicate_code_or_host_fails_at_import_time(monkeypatch: pytest.MonkeyPatch) -> None:
    from law_scrapper_mcp_voivodeship_journals import catalog

    monkeypatch.setattr(catalog, "JOURNALS", (*JOURNALS, Journal("POL_WOJ_DS", "x", "other.example", "x")))

    with pytest.raises(ValueError, match="unikalne"):
        catalog._check_invariants()


def test_the_readme_lists_every_journal_with_its_host_and_state() -> None:
    from pathlib import Path

    readme = (Path(__file__).parents[1] / "README.md").read_text(encoding="utf-8")

    for journal in JOURNALS:
        row = next((line for line in readme.splitlines() if line.startswith(f"| `{journal.code}`")), None)
        assert row is not None, f"{journal.code} has no row in the README table"
        assert journal.host in row
        assert journal.voivodeship in row
        assert ("withheld" in row) is (journal.withheld is not None), journal.code

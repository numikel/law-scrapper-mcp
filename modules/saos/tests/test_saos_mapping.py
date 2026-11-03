"""Mapping of recorded SAOS search items onto the core's judgment record."""

from __future__ import annotations

import re
from typing import Any

import pytest
from pydantic import ValidationError
from saos_testing import CORPUS_FILES, recorded_items, recording

from law_scrapper_mcp_saos.mapping import (
    COURT_LABELS,
    COURT_TYPES,
    JUDGMENT_TYPE_LABELS,
    NO_DATE,
    NO_SIGNATURE,
    judgment_type_label,
    portal_url_of,
    saos_judgment_type,
    to_judgment,
)
from law_scrapper_mcp_saos.models import SaosSearchItem, SaosSearchPage

LABOUR = "search_labour_division_viii.json"
SUPREME = "search_supreme_newest_le_today.json"
TRIBUNAL = "search_newest_constitutional_tribunal.json"
CHAMBER = "search_newest_national_appeal_chamber.json"
ADMINISTRATIVE = "search_newest_administrative.json"
ALL_SEARCH_FILES = (*CORPUS_FILES, ADMINISTRATIVE)


def _item(raw: dict[str, Any]) -> SaosSearchItem:
    return SaosSearchItem.model_validate(raw)


class TestLabourDivisionPage:
    def test_every_record_maps_with_the_division_court_and_code(self) -> None:
        raws = recording(LABOUR)["items"]
        assert len(raws) == 20

        for raw in raws:
            record = to_judgment(_item(raw))

            assert record.source == "saos"
            assert record.source_id == str(raw["id"])
            assert record.signature == raw["courtCases"][0]["caseNumber"]
            assert record.court == raw["division"]["court"]["name"]
            assert record.court_type == "COMMON"
            assert record.division == raw["division"]["name"]
            assert record.division_code == raw["division"]["code"]
            assert record.division_code is not None and record.division_code.endswith("21")
            assert record.judgment_date == raw["judgmentDate"]
            assert record.judgment_type == JUDGMENT_TYPE_LABELS[raw["judgmentType"]]
            assert record.source_url == f"https://www.saos.org.pl/judgments/{raw['id']}"
            assert record.portal_url is None
            assert record.ecli is None

    def test_title_is_the_citation_label(self) -> None:
        raw = recording(LABOUR)["items"][0]
        record = to_judgment(_item(raw))

        assert record.title == f"{record.signature} | {record.judgment_type} | {record.court} | {record.judgment_date}"

    def test_excerpt_is_the_search_snippet_and_never_longer_than_800_characters(self) -> None:
        for raw in recording(LABOUR)["items"]:
            record = to_judgment(_item(raw))

            assert record.excerpt == raw["textContent"][:800]
            assert record.excerpt is not None and len(record.excerpt) <= 800

    def test_sentences_and_reasons_are_labelled_apart(self) -> None:
        labels = {to_judgment(_item(raw)).judgment_type for raw in recording(LABOUR)["items"]}

        assert labels == {"Wyrok", "Uzasadnienie"}


class TestOtherCourtTypes:
    def test_supreme_court_record_lists_the_chamber_before_the_division(self) -> None:
        raw = recording(SUPREME)["items"][0]
        record = to_judgment(_item(raw))

        assert record.court == "Sąd Najwyższy"
        assert record.court_type == "SUPREME"
        assert record.division == f"{raw['division']['chambers'][0]['name']} / {raw['division']['name']}"
        assert record.division == "Izba Karna / Wydział III"
        assert record.division_code is None

    @pytest.mark.parametrize(
        ("name", "court"),
        [(TRIBUNAL, "Trybunał Konstytucyjny"), (CHAMBER, "Krajowa Izba Odwoławcza")],
    )
    def test_courts_without_a_division_keep_a_fixed_label(self, name: str, court: str) -> None:
        raw = recording(name)["items"][0]
        record = to_judgment(_item(raw))

        assert "division" not in raw
        assert record.court == court
        assert record.division is None
        assert record.division_code is None
        assert record.judgment_type == "Postanowienie"

    def test_every_court_type_has_a_label_or_names_its_court_in_the_record(self) -> None:
        assert set(COURT_TYPES) - set(COURT_LABELS) == {"COMMON"}


class TestEveryRecordedShape:
    @pytest.mark.parametrize("name", ALL_SEARCH_FILES)
    def test_a_recorded_page_parses_and_maps(self, name: str) -> None:
        raw_page = recording(name)
        page = SaosSearchPage.model_validate(raw_page)

        assert page.info.total_results == raw_page["info"]["totalResults"]
        assert len(page.items) == len(raw_page["items"])
        for item in page.items:
            record = to_judgment(item)
            assert record.signature and record.title and record.court and record.judgment_type

    def test_the_empty_administrative_page_is_a_valid_empty_result(self) -> None:
        page = SaosSearchPage.model_validate(recording(ADMINISTRATIVE))

        assert page.items == []
        assert page.info.total_results == 0

    def test_the_past_the_end_page_is_a_valid_empty_result_that_keeps_the_total(self) -> None:
        page = SaosSearchPage.model_validate(recording("search_out_of_range_page.json"))

        assert page.items == []
        assert page.info.total_results > 0

    @pytest.mark.parametrize(
        "body", [[], {}, {"items": []}, {"info": {"totalResults": 1}}, recording("error_page_size_101.json")]
    )
    def test_a_body_that_is_not_a_search_page_does_not_read_as_an_empty_result(self, body: object) -> None:
        with pytest.raises(ValidationError):
            SaosSearchPage.model_validate(body)


class TestPeopleAreNeverMapped:
    def test_judges_exist_in_the_recordings_and_in_no_mapped_field(self) -> None:
        raws = recorded_items(*CORPUS_FILES)
        assert any(raw.get("judges") for raw in raws)

        for raw in raws:
            dumped = to_judgment(_item(raw)).model_dump_json()
            assert "[redacted]" not in dumped
            assert "judges" not in dumped
            assert "PRESIDING_JUDGE" not in dumped

    def test_the_api_model_does_not_even_hold_them(self) -> None:
        item = _item(recording(LABOUR)["items"][0])

        assert not hasattr(item, "judges")
        assert "judges" not in item.model_dump()


class TestToleranceForMissingParts:
    def test_missing_case_number_division_and_date(self) -> None:
        record = to_judgment(_item({"id": 7, "courtType": "COMMON", "judgmentType": "SENTENCE"}))

        assert record.signature == NO_SIGNATURE
        assert record.court == "Sąd powszechny"
        assert record.division is None
        assert record.judgment_date is None
        assert record.title.endswith(NO_DATE)
        assert record.excerpt is None

    def test_several_case_numbers_are_joined(self) -> None:
        raw = {
            "id": 7,
            "courtCases": [{"caseNumber": "I A 1/20"}, {"caseNumber": "I A 2/20"}],
            "judgmentType": "DECISION",
        }

        assert to_judgment(_item(raw)).signature == "I A 1/20, I A 2/20"

    def test_an_unknown_judgment_type_is_kept_as_it_came(self) -> None:
        assert to_judgment(_item({"id": 7, "judgmentType": "NEW_KIND"})).judgment_type == "NEW_KIND"

    @pytest.mark.parametrize("date", ["2026-10-23T10:00:00", "23.10.2026", "", "2026-1-5"])
    def test_a_date_that_is_not_iso_becomes_none(self, date: str) -> None:
        assert to_judgment(_item({"id": 7, "judgmentDate": date})).judgment_date is None

    def test_a_division_without_court_or_code(self) -> None:
        raw = {"id": 7, "courtType": "COMMON", "division": {"name": "I Wydział Cywilny"}}
        record = to_judgment(_item(raw))

        assert record.court == "Sąd powszechny"
        assert record.division == "I Wydział Cywilny"
        assert record.division_code is None

    def test_several_chambers_are_all_named(self) -> None:
        raw = {
            "id": 7,
            "courtType": "SUPREME",
            "division": {"name": "Wydział I", "chambers": [{"name": "Izba A"}, {"name": "Izba B"}]},
        }

        assert to_judgment(_item(raw)).division == "Izba A, Izba B / Wydział I"

    def test_a_longer_excerpt_is_cut_to_800_characters(self) -> None:
        assert len(to_judgment(_item({"id": 7, "textContent": "x" * 900})).excerpt or "") == 800


class TestLabels:
    def test_polish_label_and_saos_name_resolve_to_the_same_type(self) -> None:
        for saos_name, label in JUDGMENT_TYPE_LABELS.items():
            assert saos_judgment_type(label) == saos_name
            assert saos_judgment_type(label.upper()) == saos_name
            assert saos_judgment_type(saos_name.lower()) == saos_name
            assert judgment_type_label(saos_name) == label

    def test_an_unknown_type_resolves_to_none(self) -> None:
        assert saos_judgment_type("Wyrok końcowy") is None

    def test_portal_url_pattern_is_the_documented_one(self) -> None:
        assert re.fullmatch(r"https://www\.saos\.org\.pl/judgments/\d+", portal_url_of(554697))

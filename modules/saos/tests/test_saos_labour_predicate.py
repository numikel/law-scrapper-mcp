"""The labour rules: one predicate per court type, the same rule as the pattern the agent is told to use."""

from __future__ import annotations

import re

import pytest
from saos_testing import (
    FakeSaos,
    HarnessFactory,
    corpus,
    dictionary_division_codes,
    recording,
    supreme_chamber_items,
)

from law_scrapper_mcp_saos import service
from law_scrapper_mcp_saos.mapping import (
    COURT_TYPES,
    LABOUR_CHAMBER_PATTERN,
    LABOUR_DIVISION_PATTERN,
    LABOUR_RULES,
    LabourRule,
    is_labour_chamber,
    is_labour_division,
    labour_rule_for,
    to_judgment,
)
from law_scrapper_mcp_saos.models import SaosSearchItem

SEARCH_FILES = (
    "search_labour_division_viii.json",
    "search_common_newest_unbounded.json",
    "search_common_newest_le_today.json",
    "search_case_number_pair.json",
    "search_viii_sentence_all_years.json",
    "search_by_law_journal_entry.json",
)
RECORDED_LABOUR_CHAMBER = "Izba Pracy, Ubezpieczeń Społecznych i Spraw Publicznych"


def _recorded_codes() -> list[str]:
    codes = [
        item["division"]["code"]
        for name in SEARCH_FILES
        for item in recording(name)["items"]
        if item.get("division") and item["division"].get("code")
    ]
    return codes + dictionary_division_codes()


def _recorded_chamber_names() -> list[str]:
    names = [
        chamber["name"]
        for item in [*supreme_chamber_items(), *recording("search_supreme_newest_le_today.json")["items"]]
        for chamber in item["division"]["chambers"]
    ]
    return names


class TestCommonCourts:
    def test_the_documented_pattern_is_the_one_the_hints_and_the_service_use(self) -> None:
        assert LABOUR_DIVISION_PATTERN == "(15|18|21)$"
        assert service.LABOUR_DIVISION_PATTERN is LABOUR_DIVISION_PATTERN
        assert LABOUR_DIVISION_PATTERN in LABOUR_RULES["COMMON"].description

    def test_the_predicate_and_the_documented_regex_agree_on_every_recorded_division_code(self) -> None:
        codes = _recorded_codes()
        suffixes = {code[-2:] for code in codes}

        assert len(codes) > 300
        assert {"15", "18", "21"} <= suffixes
        assert len(suffixes) > 6, "the recordings must hold codes the rule has to reject"
        for code in codes:
            assert is_labour_division(code) == bool(re.search(LABOUR_DIVISION_PATTERN, code)), code

    def test_the_predicate_rejects_a_missing_or_empty_code(self) -> None:
        assert is_labour_division(None) is False
        assert is_labour_division("") is False
        assert is_labour_division("15") is True
        assert is_labour_division("0001503") is False  # 15 in the middle of the code is not the type

    def test_the_rule_agrees_with_the_predicate_on_every_recorded_item(self) -> None:
        rule = labour_rule_for("COMMON")
        items = [SaosSearchItem.model_validate(item) for item in corpus()]

        assert any(rule.matches(item) for item in items)
        assert any(not rule.matches(item) for item in items)
        for item in items:
            assert rule.matches(item) == is_labour_division(item.division.code if item.division else None)

    def test_the_rule_text_names_all_three_suffixes_and_the_narrower_pattern(self) -> None:
        text = LABOUR_RULES["COMMON"].description

        for part in ("15", "18", "21", "(15|21)$", "division_code"):
            assert part in text, part


class TestSupremeCourt:
    def test_the_recorded_labour_chamber_is_recognised_and_the_others_are_not(self) -> None:
        names = set(_recorded_chamber_names())

        assert RECORDED_LABOUR_CHAMBER in names
        assert {name for name in names if is_labour_chamber([name])} == {RECORDED_LABOUR_CHAMBER}
        assert {"Izba Cywilna", "Izba Karna", "Izba Wojskowa"} <= names

    def test_the_documented_pattern_and_the_predicate_agree_on_every_recorded_record(self) -> None:
        """`filter_results` reads the mapped `division`, which lists the chamber names first."""
        rule = labour_rule_for("SUPREME")
        items = [
            SaosSearchItem.model_validate(item)
            for item in [*supreme_chamber_items(), *recording("search_supreme_newest_le_today.json")["items"]]
        ]

        assert any(rule.matches(item) for item in items)
        assert any(not rule.matches(item) for item in items)
        for item in items:
            mapped = to_judgment(item).division or ""
            assert rule.matches(item) == bool(re.search(LABOUR_CHAMBER_PATTERN, mapped)), mapped

    @pytest.mark.parametrize(
        ("names", "expected"),
        [
            ([RECORDED_LABOUR_CHAMBER], True),
            (["Izba Pracy i Ubezpieczeń Społecznych"], True),  # a spelling no recorded SAOS record carries
            (["izba pracy"], True),
            (["Izba Karna", RECORDED_LABOUR_CHAMBER], True),
            (["Izba Karna", "Izba Cywilna"], False),
            (["Izba Wojskowa"], False),
            (["Pracy"], False),
            (["Wydział Pracy"], False),
            ([], False),
        ],
    )
    def test_the_prefix_rule_on_chamber_names(self, names: list[str], expected: bool) -> None:
        assert is_labour_chamber(names) is expected

    def test_a_record_without_a_division_is_not_labour(self) -> None:
        item = SaosSearchItem.model_validate({"id": 1, "courtType": "SUPREME"})

        assert labour_rule_for("SUPREME").matches(item) is False

    def test_the_rule_text_names_the_recorded_chamber_and_the_narrowing(self) -> None:
        text = LABOUR_RULES["SUPREME"].description

        for part in (RECORDED_LABOUR_CHAMBER, LABOUR_CHAMBER_PATTERN, "field='division'", "2016-06-22"):
            assert part in text, part


class TestRegistry:
    def test_common_courts_and_the_supreme_court_have_a_rule(self) -> None:
        assert set(LABOUR_RULES) == {"COMMON", "SUPREME"}
        assert set(LABOUR_RULES) <= set(COURT_TYPES)

    @pytest.mark.parametrize("court_type", ["CONSTITUTIONAL_TRIBUNAL", "NATIONAL_APPEAL_CHAMBER", "ADMINISTRATIVE"])
    def test_a_court_type_without_a_rule_is_refused_in_polish(self, court_type: str) -> None:
        with pytest.raises(ValueError, match="labour_only") as excinfo:
            labour_rule_for(court_type)

        assert court_type in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_a_rule_for_another_court_type_is_added_in_the_registry_alone(
        self, monkeypatch: pytest.MonkeyPatch, make_harness: HarnessFactory
    ) -> None:
        """The scan knows nothing about divisions or chambers: a rule registered for a court type is enough.

        The rule below is a stand-in on a recorded record of the National Appeal Chamber; it shows the
        extension point and is not a classification SAOS offers.
        """
        chamber = [item for item in corpus() if item["courtType"] == "NATIONAL_APPEAL_CHAMBER"]
        assert chamber
        stand_in = LabourRule(description="stand-in rule", matches=lambda item: item.court_type != "COMMON")
        monkeypatch.setitem(LABOUR_RULES, "NATIONAL_APPEAL_CHAMBER", stand_in)
        harness = make_harness(FakeSaos(chamber + [item for item in corpus() if item["courtType"] == "COMMON"][:5]))

        response = await harness.service.search(labour_only=True, court_type="NATIONAL_APPEAL_CHAMBER")

        assert response.data.scan is not None
        assert response.data.scan.rule == "stand-in rule"
        assert [r.source_id for r in response.data.results] == [str(item["id"]) for item in chamber]
        assert harness.fake.calls[0]["courtType"] == "NATIONAL_APPEAL_CHAMBER"

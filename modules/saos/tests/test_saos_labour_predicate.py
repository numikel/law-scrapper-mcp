"""The labour rule: one predicate, the same rule as the pattern the agent is told to use, one place to extend."""

from __future__ import annotations

import re

import pytest
from saos_testing import FakeSaos, HarnessFactory, corpus, dictionary_division_codes, recording

from law_scrapper_mcp_saos import mapping, service
from law_scrapper_mcp_saos.mapping import (
    COURT_TYPES,
    LABOUR_DIVISION_PATTERN,
    LABOUR_RULE_PENDING,
    LABOUR_RULES,
    LabourRule,
    is_labour_division,
    labour_rule_for,
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


def _recorded_codes() -> list[str]:
    codes = [
        item["division"]["code"]
        for name in SEARCH_FILES
        for item in recording(name)["items"]
        if item.get("division") and item["division"].get("code")
    ]
    return codes + dictionary_division_codes()


def test_the_documented_pattern_is_the_one_the_hints_and_the_service_use() -> None:
    assert LABOUR_DIVISION_PATTERN == "(15|18|21)$"
    assert service.LABOUR_DIVISION_PATTERN is LABOUR_DIVISION_PATTERN
    assert LABOUR_DIVISION_PATTERN in LABOUR_RULES["COMMON"].description


def test_the_predicate_and_the_documented_regex_agree_on_every_recorded_division_code() -> None:
    codes = _recorded_codes()
    suffixes = {code[-2:] for code in codes}

    assert len(codes) > 300
    assert {"15", "18", "21"} <= suffixes
    assert len(suffixes) > 6, "the recordings must hold codes the rule has to reject"
    for code in codes:
        assert is_labour_division(code) == bool(re.search(LABOUR_DIVISION_PATTERN, code)), code


def test_the_predicate_rejects_a_missing_or_empty_code() -> None:
    assert is_labour_division(None) is False
    assert is_labour_division("") is False
    assert is_labour_division("15") is True
    assert is_labour_division("0001503") is False  # 15 in the middle of the code is not the type


def test_the_common_court_rule_agrees_with_the_predicate_on_every_recorded_item() -> None:
    rule = labour_rule_for("COMMON")
    items = [SaosSearchItem.model_validate(item) for item in corpus()]

    assert any(rule.matches(item) for item in items)
    assert any(not rule.matches(item) for item in items)
    for item in items:
        assert rule.matches(item) == is_labour_division(item.division.code if item.division else None)


def test_the_rule_text_names_all_three_suffixes_and_the_narrower_pattern() -> None:
    text = LABOUR_RULES["COMMON"].description

    for part in ("15", "18", "21", "(15|21)$", "division_code"):
        assert part in text, part


def test_only_common_courts_have_a_rule_for_now_and_the_supreme_court_is_announced() -> None:
    assert set(LABOUR_RULES) == {"COMMON"}
    assert {"SUPREME"} == LABOUR_RULE_PENDING
    assert set(LABOUR_RULES) | LABOUR_RULE_PENDING <= set(COURT_TYPES)
    assert not set(LABOUR_RULES) & LABOUR_RULE_PENDING


@pytest.mark.parametrize(
    "court_type", ["SUPREME", "CONSTITUTIONAL_TRIBUNAL", "NATIONAL_APPEAL_CHAMBER", "ADMINISTRATIVE"]
)
def test_a_court_type_without_a_rule_is_refused_in_polish(court_type: str) -> None:
    with pytest.raises(ValueError, match="labour_only"):
        labour_rule_for(court_type)


@pytest.mark.asyncio
async def test_a_rule_for_another_court_type_is_added_in_the_registry_alone(
    monkeypatch: pytest.MonkeyPatch, make_harness: HarnessFactory
) -> None:
    """The scan knows nothing about divisions or chambers: a rule registered for a court type is enough.

    The rule below is a stand-in on a recorded chamber name; it shows the extension point, not the real
    Supreme Court rule.
    """
    supreme = [item for item in corpus() if item["courtType"] == "SUPREME"]
    assert supreme
    stand_in = LabourRule(
        description="stand-in rule",
        matches=lambda item: bool(item.division and any(c.name == "Izba Karna" for c in item.division.chambers)),
    )
    monkeypatch.setitem(LABOUR_RULES, "SUPREME", stand_in)
    monkeypatch.setattr(mapping, "LABOUR_RULE_PENDING", frozenset())
    harness = make_harness(FakeSaos(supreme + [item for item in corpus() if item["courtType"] == "COMMON"][:5]))

    response = await harness.service.search(labour_only=True, court_type="SUPREME")

    assert response.data.scan is not None
    assert response.data.scan.rule == "stand-in rule"
    assert [r.source_id for r in response.data.results] == [str(item["id"]) for item in supreme]
    assert harness.fake.calls[0]["courtType"] == "SUPREME"

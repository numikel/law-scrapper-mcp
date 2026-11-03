"""The static coverage sentence: every figure and date in it comes from a recording of the service."""

from __future__ import annotations

import re

import pytest
from saos_testing import RECORDINGS, recording

from law_scrapper_mcp_saos.service import COVERAGE_AS_OF, COVERAGE_SENTENCE

# Court label in the sentence -> (recording of its newest judgments, `info.totalResults` and first item date).
RECORDED = {
    "sądy powszechne": "search_common_newest_le_today.json",
    "Sąd Najwyższy": "search_supreme_newest_le_today.json",
    "Trybunał Konstytucyjny": "search_newest_constitutional_tribunal.json",
    "Krajowa Izba Odwoławcza": "search_newest_national_appeal_chamber.json",
}
ADMINISTRATIVE = "search_newest_administrative.json"
FIGURE = re.compile(r"\d{4}-\d{2}-\d{2}|\d{1,3}(?: \d{3})+|\d+")


def _thousands(value: int) -> str:
    return f"{value:,}".replace(",", " ")


@pytest.mark.parametrize(("court", "name"), list(RECORDED.items()))
def test_the_count_and_the_newest_date_of_each_court_are_those_of_its_recording(court: str, name: str) -> None:
    page = recording(name)
    match = re.search(
        rf"{court} (\d{{1,3}}(?: \d{{3}})*) (?:orzeczeń )?\(najnowsze z (\d{{4}}-\d{{2}}-\d{{2}})\)", COVERAGE_SENTENCE
    )

    assert match is not None, court
    assert match.group(1) == _thousands(page["info"]["totalResults"])
    assert match.group(2) == page["items"][0]["judgmentDate"]


def test_administrative_courts_are_stated_as_having_no_records_because_the_recording_has_none() -> None:
    page = recording(ADMINISTRATIVE)

    assert page["items"] == [] and page["info"]["totalResults"] == 0
    assert "sądy administracyjne 0" in COVERAGE_SENTENCE


def test_the_measurement_day_is_the_upper_date_bound_of_the_recorded_requests() -> None:
    provenance = (RECORDINGS / "PROVENANCE.md").read_text(encoding="utf-8")
    bounds = {
        re.search(r"judgmentDateTo=(\d{4}-\d{2}-\d{2})", line).group(1)  # type: ignore[union-attr]
        for line in provenance.splitlines()
        # Capture-table rows only: the redaction table names the same files without a request.
        if "https://" in line and line.startswith(tuple(f"| `{name}`" for name in (*RECORDED.values(), ADMINISTRATIVE)))
    }

    assert len(bounds) >= 1
    assert bounds == {COVERAGE_AS_OF}
    assert f"pomiar z {COVERAGE_AS_OF}" in COVERAGE_SENTENCE


def test_no_figure_in_the_sentence_lacks_a_recording() -> None:
    """Both directions: the sentence holds exactly the figures the recordings give, and no other."""
    expected = {COVERAGE_AS_OF, "0"}
    for name in RECORDED.values():
        page = recording(name)
        expected |= {_thousands(page["info"]["totalResults"]), page["items"][0]["judgmentDate"]}

    assert set(FIGURE.findall(COVERAGE_SENTENCE)) == expected


def test_it_says_that_a_missing_result_proves_nothing() -> None:
    assert "nie dowodzi" in COVERAGE_SENTENCE

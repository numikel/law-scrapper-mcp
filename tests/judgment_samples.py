"""Hand-built `JudgmentSummaryOutput` records for the core tests.

`JudgmentSummaryOutput` is this project's own model, so building it by hand is legitimate; the rule that
forbids invented records applies to shapes recorded from a foreign API (see `tests/fixtures/saos/PROVENANCE.md`).
The values below are invented placeholders, not data from any court.
"""

from __future__ import annotations

from law_scrapper_mcp.models.tool_outputs import JudgmentSummaryOutput


def make_judgment(
    source_id: str = "1",
    *,
    signature: str = "I A 1/20",
    court: str = "Sąd Testowy",
    division: str | None = "I Wydział Testowy",
    division_code: str | None = "0000001",
    judgment_type: str = "Wyrok",
    judgment_date: str | None = "2020-01-15",
    excerpt: str | None = "Początek testowego tekstu.",
    source: str = "testsource",
) -> JudgmentSummaryOutput:
    """One judgment record with every field filled from the arguments."""
    return JudgmentSummaryOutput(
        source=source,
        source_id=source_id,
        signature=signature,
        title=f"{signature} | {judgment_type} | {court} | {judgment_date}",
        court=court,
        court_type="COMMON",
        division=division,
        division_code=division_code,
        judgment_type=judgment_type,
        judgment_date=judgment_date,
        source_url=f"https://example.test/judgments/{source_id}",
        excerpt=excerpt,
    )


def sample_judgments() -> list[JudgmentSummaryOutput]:
    """Five records spread over divisions, types, years and one missing date."""
    return [
        make_judgment(
            "1",
            signature="VIII U 10/19",
            court="Sąd Okręgowy w Alfie",
            division="VIII Wydział Pracy i Ubezpieczeń Społecznych",
            division_code="0001021",
            judgment_type="Wyrok",
            judgment_date="2019-03-01",
        ),
        make_judgment(
            "2",
            signature="IV P 5/20",
            court="Sąd Rejonowy w Becie",
            division="IV Wydział Pracy",
            division_code="0002015",
            judgment_type="Postanowienie",
            judgment_date="2020-06-10",
        ),
        make_judgment(
            "3",
            signature="II K 7/21",
            court="Sąd Rejonowy w Becie",
            division="II Wydział Karny",
            division_code="0002003",
            judgment_type="Wyrok",
            judgment_date="2021-09-30",
        ),
        make_judgment(
            "4",
            signature="III AUa 2/22",
            court="Sąd Apelacyjny w Gammie",
            division="III Wydział Ubezpieczeń Społecznych",
            division_code="0003018",
            judgment_type="Uzasadnienie",
            judgment_date="2022-02-02",
            excerpt=None,
        ),
        make_judgment(
            "5",
            signature="K 1/23",
            court="Trybunał Testowy",
            division=None,
            division_code=None,
            judgment_type="Wyrok",
            judgment_date=None,
        ),
    ]

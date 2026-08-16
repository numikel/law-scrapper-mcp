"""Tests for search/browse response hint generation."""

from law_scrapper_mcp.services.response_enrichment import metadata_hints, search_hints


def test_search_hints_detects_more_results_after_offset_page() -> None:
    hints = search_hints(
        total_count=100,
        has_results=True,
        eli="DU/2024/1",
        result_set_id="rs_1",
        offset=20,
        returned_count=20,
        applied_limit=20,
    )

    assert any("ograniczone do 20" in hint.message for hint in hints)


def test_search_hints_no_truncation_when_offset_page_is_final() -> None:
    hints = search_hints(
        total_count=40,
        has_results=True,
        eli="DU/2024/1",
        result_set_id="rs_1",
        offset=20,
        returned_count=20,
        applied_limit=20,
    )

    assert not any("ograniczone do" in hint.message for hint in hints)


def test_search_hints_detects_truncation_on_first_page_without_offset() -> None:
    hints = search_hints(
        total_count=50,
        has_results=True,
        eli="DU/2024/1",
        result_set_id="rs_1",
        offset=0,
        returned_count=20,
        applied_limit=20,
    )

    assert any("ograniczone do 20" in hint.message for hint in hints)


def test_metadata_hints_name_the_failed_categories() -> None:
    hints = metadata_hints("all", ["statuses", "types"])

    assert any("statuses" in hint.message and "types" in hint.message for hint in hints)


def test_metadata_hints_stay_silent_when_nothing_failed() -> None:
    before = metadata_hints("all")
    after = metadata_hints("all", [])

    assert [hint.message for hint in before] == [hint.message for hint in after]

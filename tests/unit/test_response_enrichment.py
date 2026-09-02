"""Tests for search/browse response hint generation."""

from law_scrapper_mcp.models.tool_outputs import ActSummaryOutput, FilterOutput, Hint, ResultSetScope, SetScope
from law_scrapper_mcp.services.pagination import paginate_items
from law_scrapper_mcp.services.response_enrichment import filter_hints, metadata_hints, search_hints


def _page_scope(stored: int = 20, corpus: int = 1_984, offset: int = 0) -> ResultSetScope:
    return ResultSetScope(scope=SetScope.PAGE, stored_count=stored, window_offset=offset, corpus_count=corpus)


def _complete_scope(stored: int = 5) -> ResultSetScope:
    return ResultSetScope(scope=SetScope.COMPLETE, stored_count=stored, window_offset=0, corpus_count=stored)


def _pagination_hint(hints: list[Hint]) -> Hint | None:
    return next((h for h in hints if h.parameters and "offset" in h.parameters), None)


def test_search_hints_detects_more_results_after_offset_page() -> None:
    hints = search_hints(
        100,
        True,
        "DU/2024/1",
        "rs_1",
        tool_name="search_legal_acts",
        next_call_params={"publisher": "DU", "year": 2024},
        filter_max_records=100,
        scope=_page_scope(corpus=100),
        offset=20,
        returned_count=20,
        applied_limit=20,
    )
    hint = _pagination_hint(hints)
    assert hint is not None
    assert hint.parameters is not None
    assert hint.parameters["offset"] == 40


def test_search_hints_no_truncation_when_offset_page_is_final() -> None:
    hints = search_hints(
        40,
        True,
        "DU/2024/1",
        "rs_1",
        tool_name="search_legal_acts",
        next_call_params={"publisher": "DU", "year": 2024},
        filter_max_records=100,
        scope=_page_scope(corpus=40, offset=20),
        offset=20,
        returned_count=20,
        applied_limit=20,
    )
    assert _pagination_hint(hints) is None


def test_search_hints_detects_truncation_on_first_page_without_offset() -> None:
    hints = search_hints(
        50,
        True,
        "DU/2024/1",
        "rs_1",
        tool_name="search_legal_acts",
        next_call_params={"publisher": "DU", "year": 2024},
        filter_max_records=100,
        scope=_page_scope(corpus=50),
        returned_count=20,
        applied_limit=20,
    )
    hint = _pagination_hint(hints)
    assert hint is not None
    assert hint.parameters is not None
    assert hint.parameters["offset"] == 20


def test_pagination_hint_names_the_tool_that_produced_it() -> None:
    """Criterion 10 — this is the F48 regression test.

    `browse_acts` used to hand the model a hint pointing at `search_legal_acts`.
    """
    hints = search_hints(
        1_984,
        True,
        "DU/2024/1984",
        "rs_1",
        tool_name="browse_acts",
        next_call_params={"publisher": "DU", "year": 2024},
        filter_max_records=100,
        scope=_page_scope(),
        returned_count=20,
        applied_limit=20,
    )
    hint = _pagination_hint(hints)
    assert hint is not None
    assert hint.tool == "browse_acts"
    assert hint.parameters == {"publisher": "DU", "year": 2024, "limit": 20, "offset": 20}


def test_pagination_hint_names_search_when_search_produced_it() -> None:
    """Criterion 11."""
    hints = search_hints(
        1_984,
        True,
        "DU/2024/1",
        "rs_1",
        tool_name="search_legal_acts",
        next_call_params={"publisher": "DU", "title": "budżet"},
        filter_max_records=100,
        scope=_page_scope(),
        returned_count=20,
        applied_limit=20,
    )
    hint = _pagination_hint(hints)
    assert hint is not None
    assert hint.tool == "search_legal_acts"
    assert hint.parameters == {
        "publisher": "DU",
        "title": "budżet",
        "limit": 20,
        "offset": 20,
    }


def test_a_complete_set_gets_no_pagination_hint() -> None:
    """Criterion 12."""
    hints = search_hints(
        5,
        True,
        "DU/2024/1",
        "rs_1",
        tool_name="search_legal_acts",
        next_call_params={"publisher": "DU", "year": 2024},
        filter_max_records=100,
        scope=_complete_scope(),
        returned_count=5,
        applied_limit=20,
    )
    assert _pagination_hint(hints) is None


def test_suggested_limit_never_exceeds_the_filter_ceiling() -> None:
    """Criterion 13, first half: a reachable corpus gets a concrete limit."""
    hints = search_hints(
        60,
        True,
        "DU/2024/1",
        "rs_1",
        tool_name="search_legal_acts",
        next_call_params={"publisher": "DU", "year": 2024},
        filter_max_records=100,
        scope=_page_scope(corpus=60),
        returned_count=20,
        applied_limit=20,
    )
    suggested = [h for h in hints if h.parameters and h.parameters.get("limit") == 60]
    assert suggested, "a corpus below the ceiling should get a concrete limit suggestion"
    assert suggested[0].parameters is not None
    assert suggested[0].parameters["limit"] <= 100


def test_an_unreachable_corpus_is_told_to_narrow_instead() -> None:
    """Criterion 13, second half: suggesting limit=1984 would be unexecutable.

    A 1984-record set is rejected by ResultSetTooLargeError on the first filter,
    so telling the model to widen the window would reproduce F48 one level up.
    """
    hints = search_hints(
        1_984,
        True,
        "DU/2024/1",
        "rs_1",
        tool_name="search_legal_acts",
        next_call_params={"publisher": "DU", "year": 2024},
        filter_max_records=100,
        scope=_page_scope(),
        returned_count=20,
        applied_limit=20,
    )
    limits = [h.parameters["limit"] for h in hints if h.parameters and "limit" in h.parameters]
    assert all(value <= 100 for value in limits)
    assert any("Zawęź kryteria" in h.message for h in hints)


def test_max_result_limit_caps_the_suggestion_below_the_filter_ceiling() -> None:
    """A corpus fits under filter_max_records but exceeds the calling tool's own cap.

    `browse_acts` clamps `limit` to MAX_ITEM_LIMIT (100) regardless of
    `filter_max_records`. Suggesting limit=300 here would be silently clamped by
    `browse_acts` before it reaches the result store, handing back another PAGE
    instead of the COMPLETE set the hint promised — the exact defect this task exists
    to eliminate, one level removed.
    """
    hints = search_hints(
        300,
        True,
        "DU/2024/1",
        "rs_1",
        tool_name="browse_acts",
        next_call_params={"publisher": "DU", "year": 2024},
        filter_max_records=500,
        scope=_page_scope(corpus=300),
        returned_count=20,
        applied_limit=20,
        max_result_limit=100,
    )
    limits = [h.parameters["limit"] for h in hints if h.parameters and "limit" in h.parameters]
    assert all(value <= 100 for value in limits)
    assert any("Zawęź kryteria" in h.message for h in hints)


def test_max_result_limit_allows_the_suggestion_when_corpus_fits_both_ceilings() -> None:
    """Mirror image: a corpus under both ceilings still gets a concrete limit."""
    hints = search_hints(
        60,
        True,
        "DU/2024/1",
        "rs_1",
        tool_name="browse_acts",
        next_call_params={"publisher": "DU", "year": 2024},
        filter_max_records=500,
        scope=_page_scope(corpus=60),
        returned_count=20,
        applied_limit=20,
        max_result_limit=100,
    )
    suggested = [h for h in hints if h.parameters and h.parameters.get("limit") == 60]
    assert suggested, "a corpus below both ceilings should get a concrete limit suggestion"


def test_the_filter_hint_says_what_is_being_narrowed() -> None:
    hints = search_hints(
        1_984,
        True,
        "DU/2024/1",
        "rs_1",
        tool_name="search_legal_acts",
        next_call_params={"publisher": "DU", "year": 2024},
        filter_max_records=100,
        scope=_page_scope(),
        returned_count=20,
        applied_limit=20,
    )
    filter_hint = next(h for h in hints if h.tool == "filter_results")
    assert "20" in filter_hint.message
    assert "1984" in filter_hint.message


def test_metadata_hints_name_the_failed_categories() -> None:
    hints = metadata_hints("all", ["statuses", "types"])

    assert any("statuses" in hint.message and "types" in hint.message for hint in hints)


def test_metadata_hints_stay_silent_when_nothing_failed() -> None:
    before = metadata_hints("all")
    after = metadata_hints("all", [])

    assert [hint.message for hint in before] == [hint.message for hint in after]


def test_clamped_context_chars_produces_a_hint() -> None:
    from law_scrapper_mcp.services.response_enrichment import search_in_act_hints

    hints = search_in_act_hints(5000, 2000)

    assert len(hints) == 1
    assert "5000" in hints[0].message
    assert "2000" in hints[0].message


def test_context_chars_within_the_limit_produces_no_hint() -> None:
    from law_scrapper_mcp.services.response_enrichment import search_in_act_hints

    assert search_in_act_hints(300, 300) == []


def _filter_output(
    *,
    source_scope: ResultSetScope,
    filtered: list[ActSummaryOutput],
    inconclusive: bool,
    result_set_id: str | None = None,
) -> FilterOutput:
    page, page_info = paginate_items(filtered, limit=20, offset=0)
    return FilterOutput(
        source_result_set_id="rs_1",
        result_set_id=result_set_id,
        results=page,
        original_count=source_scope.stored_count,
        filtered_count=len(filtered),
        source_scope=source_scope,
        no_match_is_inconclusive=inconclusive,
        page_info=page_info,
    )


def test_an_empty_match_on_a_window_gets_an_executable_next_step() -> None:
    """Criterion 14. The worst failure mode in a legal domain is a confident 'no'."""
    output = _filter_output(
        source_scope=_page_scope(stored=20, corpus=60),
        filtered=[],
        inconclusive=True,
    )

    hints = filter_hints(output, filter_max_records=100)

    assert hints, "an inconclusive empty match must not be answered with silence"
    message = hints[0].message
    assert "OKNO" in message
    assert "NIE dowodzi" in message
    assert "limit=60" in message


def test_an_unreachable_corpus_is_told_to_narrow_the_search() -> None:
    output = _filter_output(
        source_scope=_page_scope(stored=20, corpus=1_984),
        filtered=[],
        inconclusive=True,
    )

    hints = filter_hints(output, filter_max_records=100)

    assert "Zawęź kryteria" in hints[0].message


def test_a_conclusive_empty_match_gets_no_warning() -> None:
    """Criterion 15 seen from the hint side: silence is the correct answer here."""
    output = _filter_output(
        source_scope=_complete_scope(stored=5),
        filtered=[],
        inconclusive=False,
    )

    assert filter_hints(output, filter_max_records=100) == []


def test_the_inconclusive_hint_names_no_tool() -> None:
    """Deliberate: FilterOutput does not carry which tool produced the source set.

    Guessing `search_legal_acts` when the source came from `browse_acts` would
    reproduce F48 inside the fix for F48. The model knows which tool it called.
    """
    output = _filter_output(
        source_scope=_page_scope(stored=20, corpus=60),
        filtered=[],
        inconclusive=True,
    )

    assert filter_hints(output, filter_max_records=100)[0].tool is None

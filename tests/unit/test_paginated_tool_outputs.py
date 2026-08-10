"""Tests for paginated tool output contracts and error factories."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from law_scrapper_mcp.models.pagination import PageUnit
from law_scrapper_mcp.models.tool_outputs import (
    ChangesOutput,
    ContentOutput,
    FilterOutput,
    MetadataOutput,
    SearchInActOutput,
)
from law_scrapper_mcp.tools.act_content import _content_error_output
from law_scrapper_mcp.tools.act_search import _search_in_act_error_output
from law_scrapper_mcp.tools.changes import _changes_error_output
from law_scrapper_mcp.tools.filter_results import _filter_results_error_output
from law_scrapper_mcp.tools.metadata import _metadata_error_output


@pytest.mark.parametrize(
    ("model_cls", "payload"),
    [
        (
            ContentOutput,
            {"eli": "DU/2024/1", "section_title": "Spis treści", "content": ""},
        ),
        (
            SearchInActOutput,
            {"eli": "DU/2024/1", "query": "x", "matches": [], "total_matches": 0},
        ),
        (
            MetadataOutput,
            {"category": "all", "metadata": {}, "count": 0},
        ),
        (
            ChangesOutput,
            {
                "date_range": "2024",
                "publisher": "DU",
                "keywords": [],
                "changes": [],
                "total_count": 0,
            },
        ),
        (
            FilterOutput,
            {
                "source_result_set_id": "rs_1",
                "results": [],
                "original_count": 0,
                "filtered_count": 0,
            },
        ),
    ],
)
def test_paginated_outputs_require_page_info(model_cls: type, payload: dict) -> None:
    with pytest.raises(ValidationError):
        model_cls(**payload)


def test_read_act_content_error_factory_uses_item_unit_for_toc() -> None:
    output = _content_error_output(Exception("x"), {"eli": "DU/2024/1"})
    assert output.page_info.unit == PageUnit.ITEMS


def test_read_act_content_error_factory_uses_character_unit_for_section() -> None:
    output = _content_error_output(Exception("x"), {"eli": "DU/2024/1", "section": "Art. 1"})
    assert output.page_info.unit == PageUnit.CHARACTERS


def test_read_act_content_error_factory_treats_empty_section_as_characters() -> None:
    output = _content_error_output(Exception("x"), {"eli": "DU/2024/1", "section": ""})
    assert output.page_info.unit == PageUnit.CHARACTERS


def test_search_in_act_error_factory_uses_items_unit() -> None:
    output = _search_in_act_error_output(Exception("x"), {"eli": "DU/2024/1", "query": "x"})
    assert output.page_info.unit == PageUnit.ITEMS


def test_metadata_error_factory_uses_items_unit() -> None:
    output = _metadata_error_output(Exception("x"), {"category": "all"})
    assert output.page_info.unit == PageUnit.ITEMS


def test_changes_error_factory_uses_items_unit() -> None:
    output = _changes_error_output(Exception("x"), {"date_from": "2024-01-01"})
    assert output.page_info.unit == PageUnit.ITEMS


def test_filter_results_error_factory_uses_items_unit() -> None:
    output = _filter_results_error_output(Exception("x"), {"result_set_id": "rs_1"})
    assert output.page_info.unit == PageUnit.ITEMS

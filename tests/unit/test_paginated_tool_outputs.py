"""Tests for paginated tool output contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from law_scrapper_mcp.models.tool_outputs import (
    ChangesOutput,
    ContentOutput,
    FilterOutput,
    LoadedDocumentListOutput,
    MetadataOutput,
    ResultSetListOutput,
    SearchInActOutput,
)


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
        (
            ResultSetListOutput,
            {"sets": [], "count": 0},
        ),
        (
            LoadedDocumentListOutput,
            {"documents": [], "count": 0},
        ),
    ],
)
def test_paginated_outputs_require_page_info(model_cls: type, payload: dict) -> None:
    with pytest.raises(ValidationError):
        model_cls(**payload)

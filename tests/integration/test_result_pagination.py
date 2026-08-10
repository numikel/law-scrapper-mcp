"""Pagination contract tests for stored results and metadata."""

import pytest

from mcp_helpers import parse_tool_result

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


def _assert_page(
    page_info: dict[str, object],
    *,
    total: int,
    limit: int,
    offset: int,
) -> None:
    returned = min(limit, max(total - offset, 0))
    truncated = min(offset + returned, total) < total
    next_offset = offset + returned if limit > 0 and returned > 0 and truncated else None
    assert page_info == {
        "limit": limit,
        "offset": offset,
        "returned_count": returned,
        "total_count": total,
        "was_truncated": truncated,
        "next_offset": next_offset,
        "unit": "items",
    }


async def _assert_tool_page_matrix(
    mcp_client,
    tool_name: str,
    base_arguments: dict[str, object],
    *,
    items_field: str | None = None,
) -> None:
    baseline = await mcp_client.call_tool(tool_name, base_arguments)
    baseline_payload = parse_tool_result(baseline)
    total = int(baseline_payload["data"]["page_info"]["total_count"])
    assert total >= 3
    cases = [
        ({}, 20, 0),
        ({"limit": 500}, 100, 0),
        ({"limit": 1, "offset": 0}, 1, 0),
        ({"limit": 1, "offset": total // 2}, 1, total // 2),
        ({"limit": 1, "offset": total - 1}, 1, total - 1),
        ({"limit": 1, "offset": total}, 1, total),
        ({"limit": 0, "offset": 0}, 0, 0),
    ]
    for arguments, expected_limit, expected_offset in cases:
        result = await mcp_client.call_tool(
            tool_name,
            {**base_arguments, **arguments},
        )
        payload = parse_tool_result(result)
        page_info = payload["data"]["page_info"]
        _assert_page(
            page_info,
            total=total,
            limit=expected_limit,
            offset=expected_offset,
        )
        if items_field is not None:
            assert len(payload["data"][items_field]) == page_info["returned_count"]
        elif "metadata" in payload["data"]:
            metadata = payload["data"]["metadata"]
            assert sum(len(values) for values in metadata.values()) == page_info["returned_count"]
            if base_arguments.get("category", "all") == "all":
                assert list(metadata) == [
                    "keywords",
                    "publishers",
                    "statuses",
                    "types",
                    "institutions",
                ]


async def test_filter_stores_full_set_but_returns_one_page(mcp_client) -> None:
    search = await mcp_client.call_tool("search_legal_acts", {"year": 2024})
    source_id = parse_tool_result(search)["data"]["result_set_id"]
    filtered = await mcp_client.call_tool(
        "filter_results",
        {"result_set_id": source_id, "limit": 1, "offset": 0},
    )
    filtered_payload = parse_tool_result(filtered)
    chained_id = filtered_payload["data"]["result_set_id"]
    chained = await mcp_client.call_tool(
        "filter_results",
        {"result_set_id": chained_id, "limit": 100},
    )
    chained_payload = parse_tool_result(chained)

    assert len(filtered_payload["data"]["results"]) == 1
    assert filtered_payload["data"]["filtered_count"] == 3
    assert len(chained_payload["data"]["results"]) == 3


async def test_metadata_all_has_one_global_limit(mcp_client) -> None:
    result = await mcp_client.call_tool(
        "get_system_metadata",
        {"category": "all", "limit": 2, "offset": 0},
    )
    payload = parse_tool_result(result)

    assert payload["data"]["count"] == 2
    assert sum(len(values) for values in payload["data"]["metadata"].values()) == 2
    assert list(payload["data"]["metadata"]) == [
        "keywords",
        "publishers",
        "statuses",
        "types",
        "institutions",
    ]


async def test_changes_store_full_set_but_return_one_page(mcp_client) -> None:
    result = await mcp_client.call_tool(
        "track_legal_changes",
        {"date_from": "2024-01-01", "date_to": "2024-12-31", "limit": 1},
    )
    payload = parse_tool_result(result)
    stored = await mcp_client.call_tool(
        "filter_results",
        {"result_set_id": payload["data"]["result_set_id"], "limit": 100},
    )
    stored_payload = parse_tool_result(stored)

    assert len(payload["data"]["changes"]) == 1
    assert payload["data"]["total_count"] == 3
    assert len(stored_payload["data"]["results"]) == 3


async def test_filter_sort_before_slice_returns_second_sorted_item(mcp_client) -> None:
    search = await mcp_client.call_tool("search_legal_acts", {"year": 2024})
    source_id = parse_tool_result(search)["data"]["result_set_id"]
    sorted_all = await mcp_client.call_tool(
        "filter_results",
        {
            "result_set_id": source_id,
            "sort_by": "promulgation_date",
            "sort_desc": False,
            "limit": 100,
        },
    )
    page_two = await mcp_client.call_tool(
        "filter_results",
        {
            "result_set_id": source_id,
            "sort_by": "promulgation_date",
            "sort_desc": False,
            "limit": 1,
            "offset": 1,
        },
    )
    sorted_payload = parse_tool_result(sorted_all)
    page_payload = parse_tool_result(page_two)

    globally_sorted = sorted_payload["data"]["results"]
    assert len(globally_sorted) == 3
    assert page_payload["data"]["results"][0]["eli"] == globally_sorted[1]["eli"]


async def test_result_tools_cover_every_page_boundary(mcp_client) -> None:
    search = await mcp_client.call_tool("search_legal_acts", {"year": 2024})
    source_id = parse_tool_result(search)["data"]["result_set_id"]
    await _assert_tool_page_matrix(
        mcp_client,
        "filter_results",
        {"result_set_id": source_id},
        items_field="results",
    )
    await _assert_tool_page_matrix(
        mcp_client,
        "get_system_metadata",
        {"category": "all"},
    )
    await _assert_tool_page_matrix(
        mcp_client,
        "track_legal_changes",
        {"date_from": "2024-01-01", "date_to": "2024-12-31"},
        items_field="changes",
    )


@pytest.mark.parametrize("field", ["limit", "offset"])
async def test_every_result_tool_rejects_negative_pages(
    mcp_client,
    field: str,
) -> None:
    search = await mcp_client.call_tool("search_legal_acts", {"year": 2024})
    source_id = parse_tool_result(search)["data"]["result_set_id"]
    calls = [
        ("filter_results", {"result_set_id": source_id, field: -1}),
        ("get_system_metadata", {"category": "all", field: -1}),
        (
            "track_legal_changes",
            {
                "date_from": "2024-01-01",
                "date_to": "2024-12-31",
                field: -1,
            },
        ),
    ]
    for tool_name, arguments in calls:
        result = await mcp_client.call_tool(tool_name, arguments)
        assert result.is_error is True
        assert result.structured_content is None
        assert field in result.content[0].text

"""Pagination contract tests for stored results and metadata."""

import json

import pytest

pytestmark = pytest.mark.integration


def _parse_payload(result) -> dict:
    if isinstance(result.data, dict):
        return result.data
    if isinstance(result.data, str):
        return json.loads(result.data)
    if isinstance(result.structured_content, dict) and isinstance(
        result.structured_content.get("result"), str
    ):
        return json.loads(result.structured_content["result"])
    return result.structured_content


async def _payload(result) -> dict:
    return _parse_payload(result)


def _assert_page(
    page_info: dict[str, object],
    *,
    total: int,
    limit: int,
    offset: int,
) -> None:
    returned = min(limit, max(total - offset, 0))
    truncated = min(offset + returned, total) < total
    next_offset = (
        offset + returned
        if limit > 0 and returned > 0 and truncated
        else None
    )
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
    baseline_payload = await _payload(baseline)
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
        payload = await _payload(result)
        page_info = payload["data"]["page_info"]
        _assert_page(
            page_info,
            total=total,
            limit=expected_limit,
            offset=expected_offset,
        )
        if items_field is not None:
            assert len(payload["data"][items_field]) == page_info["returned_count"]


async def test_filter_stores_full_set_but_returns_one_page(mcp_client) -> None:
    search = await mcp_client.call_tool("search_legal_acts", {"year": 2024})
    source_id = (await _payload(search))["data"]["result_set_id"]
    filtered = await mcp_client.call_tool(
        "filter_results",
        {"result_set_id": source_id, "limit": 1, "offset": 0},
    )
    filtered_payload = await _payload(filtered)
    chained_id = filtered_payload["data"]["result_set_id"]
    chained = await mcp_client.call_tool(
        "filter_results",
        {"result_set_id": chained_id, "limit": 100},
    )
    chained_payload = await _payload(chained)

    assert len(filtered_payload["data"]["results"]) == 1
    assert filtered_payload["data"]["filtered_count"] == 3
    assert len(chained_payload["data"]["results"]) == 3


async def test_metadata_all_has_one_global_limit(mcp_client) -> None:
    result = await mcp_client.call_tool(
        "get_system_metadata",
        {"category": "all", "limit": 2, "offset": 0},
    )
    payload = await _payload(result)

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
    payload = await _payload(result)
    stored = await mcp_client.call_tool(
        "filter_results",
        {"result_set_id": payload["data"]["result_set_id"], "limit": 100},
    )
    stored_payload = await _payload(stored)

    assert len(payload["data"]["changes"]) == 1
    assert payload["data"]["total_count"] == 3
    assert len(stored_payload["data"]["results"]) == 3


async def test_filter_sort_before_slice_returns_second_sorted_item(mcp_client) -> None:
    search = await mcp_client.call_tool("search_legal_acts", {"year": 2024})
    source_id = (await _payload(search))["data"]["result_set_id"]
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
    sorted_payload = await _payload(sorted_all)
    page_payload = await _payload(page_two)

    globally_sorted = sorted_payload["data"]["results"]
    assert len(globally_sorted) == 3
    assert page_payload["data"]["results"][0]["eli"] == globally_sorted[1]["eli"]


async def test_result_tools_cover_every_page_boundary(mcp_client) -> None:
    search = await mcp_client.call_tool("search_legal_acts", {"year": 2024})
    source_id = (await _payload(search))["data"]["result_set_id"]
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
    source_id = (await _payload(search))["data"]["result_set_id"]
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
        payload = await _payload(result)
        assert payload["error"] is not None
        assert field in payload["error"]

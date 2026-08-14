"""Pagination contract tests for document tools."""

import pytest

from law_scrapper_mcp.models.pagination import MAX_CONTEXT_CHARS
from mcp_helpers import parse_tool_result

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def _load(mcp_client) -> None:
    await mcp_client.call_tool("get_act_details", {"eli": "DU/2024/1", "load_content": True})


async def test_search_in_act_returns_first_page_and_total(mcp_client) -> None:
    await _load(mcp_client)
    result = await mcp_client.call_tool(
        "search_in_act",
        {"eli": "DU/2024/1", "query": "Content", "limit": 1, "offset": 0},
    )
    payload = parse_tool_result(result)

    assert payload["data"]["total_matches"] == payload["data"]["page_info"]["total_count"]
    assert payload["data"]["page_info"]["limit"] == 1
    assert payload["data"]["page_info"]["returned_count"] == 1
    assert payload["data"]["page_info"]["unit"] == "items"
    assert len(payload["data"]["matches"]) == 1


async def test_search_in_act_clamps_context_chars(mcp_client) -> None:
    await _load(mcp_client)
    query = "Content"
    result = await mcp_client.call_tool(
        "search_in_act",
        {"eli": "DU/2024/1", "query": query, "context_chars": 999999},
    )
    payload = parse_tool_result(result)
    max_context_len = len(query) + 2 * MAX_CONTEXT_CHARS

    matches = payload["data"]["matches"]
    assert matches, "Expected at least one search hit in the long-context fixture"
    assert all(len(match["context"]) <= max_context_len for match in matches)
    assert any(len(match["context"]) == max_context_len for match in matches)


async def test_read_content_toc_uses_item_pages(mcp_client) -> None:
    await _load(mcp_client)
    result = await mcp_client.call_tool("read_act_content", {"eli": "DU/2024/1", "limit": 1})
    payload = parse_tool_result(result)

    assert len(payload["data"]["toc"]) <= 1
    assert payload["data"]["page_info"]["unit"] == "items"


async def test_negative_document_offset_is_an_error(mcp_client) -> None:
    await _load(mcp_client)
    result = await mcp_client.call_tool("read_act_content", {"eli": "DU/2024/1", "offset": -1})

    assert result.is_error is True
    assert result.structured_content is None
    assert "offset" in result.content[0].text


def _assert_page(
    page_info: dict[str, object],
    *,
    total: int,
    limit: int,
    offset: int,
    unit: str,
) -> None:
    returned = min(limit, max(total - offset, 0))
    truncated = min(offset + returned, total) < total
    assert page_info["limit"] == limit
    assert page_info["offset"] == offset
    assert page_info["returned_count"] == returned
    assert page_info["total_count"] == total
    assert page_info["was_truncated"] is truncated
    assert page_info["unit"] == unit
    assert page_info["next_offset"] == (offset + returned if limit > 0 and returned > 0 and truncated else None)


async def _assert_item_matrix(
    mcp_client,
    tool_name: str,
    base_arguments: dict[str, object],
    *,
    items_field: str,
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
    for arguments, effective_limit, effective_offset in cases:
        result = await mcp_client.call_tool(
            tool_name,
            {**base_arguments, **arguments},
        )
        payload = parse_tool_result(result)
        page_info = payload["data"]["page_info"]
        _assert_page(
            page_info,
            total=total,
            limit=effective_limit,
            offset=effective_offset,
            unit="items",
        )
        assert len(payload["data"][items_field]) == page_info["returned_count"]


async def test_toc_and_search_cover_every_item_boundary(mcp_client) -> None:
    await _load(mcp_client)
    await _assert_item_matrix(
        mcp_client,
        "read_act_content",
        {"eli": "DU/2024/1"},
        items_field="toc",
    )
    await _assert_item_matrix(
        mcp_client,
        "search_in_act",
        {"eli": "DU/2024/1", "query": "Content"},
        items_field="matches",
    )


@pytest.mark.parametrize(
    ("tool_name", "base_arguments", "field"),
    [
        ("read_act_content", {"eli": "DU/2024/1"}, "limit"),
        ("read_act_content", {"eli": "DU/2024/1"}, "offset"),
        ("search_in_act", {"eli": "DU/2024/1", "query": "Content"}, "limit"),
        ("search_in_act", {"eli": "DU/2024/1", "query": "Content"}, "offset"),
        (
            "search_in_act",
            {"eli": "DU/2024/1", "query": "Content"},
            "context_chars",
        ),
    ],
)
async def test_document_tools_reject_negative_paging_values(
    mcp_client,
    tool_name: str,
    base_arguments: dict[str, object],
    field: str,
) -> None:
    await _load(mcp_client)
    result = await mcp_client.call_tool(
        tool_name,
        {**base_arguments, field: -1},
    )

    assert result.is_error is True
    assert result.structured_content is None
    assert field in result.content[0].text


async def test_section_character_pages_are_contiguous(mcp_client) -> None:
    await _load(mcp_client)
    toc_result = await mcp_client.call_tool("read_act_content", {"eli": "DU/2024/1"})
    toc_payload = parse_tool_result(toc_result)
    section = toc_payload["data"]["toc"][0]["id"]
    full = await mcp_client.call_tool(
        "read_act_content",
        {"eli": "DU/2024/1", "section": section},
    )
    first = await mcp_client.call_tool(
        "read_act_content",
        {"eli": "DU/2024/1", "section": section, "limit": 5, "offset": 0},
    )
    second = await mcp_client.call_tool(
        "read_act_content",
        {"eli": "DU/2024/1", "section": section, "limit": 5, "offset": 5},
    )
    full_payload = parse_tool_result(full)
    first_payload = parse_tool_result(first)
    second_payload = parse_tool_result(second)

    assert first_payload["data"]["page_info"]["unit"] == "characters"
    assert second_payload["data"]["page_info"]["offset"] == 5
    assert first_payload["data"]["content"] + second_payload["data"]["content"] == full_payload["data"]["content"][:10]

    total = int(first_payload["data"]["page_info"]["total_count"])
    for arguments, effective_limit, effective_offset in [
        ({}, 10_000, 0),
        ({"limit": 100_000}, 50_000, 0),
        ({"limit": 1, "offset": 0}, 1, 0),
        ({"limit": 1, "offset": total // 2}, 1, total // 2),
        ({"limit": 1, "offset": total - 1}, 1, total - 1),
        ({"limit": 1, "offset": total}, 1, total),
        ({"limit": 0, "offset": 0}, 0, 0),
    ]:
        result = await mcp_client.call_tool(
            "read_act_content",
            {"eli": "DU/2024/1", "section": section, **arguments},
        )
        payload = parse_tool_result(result)
        page_info = payload["data"]["page_info"]
        _assert_page(
            page_info,
            total=total,
            limit=effective_limit,
            offset=effective_offset,
            unit="characters",
        )
        assert len(payload["data"]["content"]) == page_info["returned_count"]

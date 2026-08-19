"""Pagination contract for the two in-memory listing tools."""

from __future__ import annotations

import pytest

from law_scrapper_mcp.models.pagination import DEFAULT_ITEM_LIMIT
from law_scrapper_mcp.models.tool_outputs import LoadedDocumentListOutput, ResultSetListOutput
from law_scrapper_mcp.services.document_store import DocumentStore
from law_scrapper_mcp.services.result_store import ResultStore

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def _store_with_documents(count: int) -> DocumentStore:
    store = DocumentStore(max_documents=count + 1)
    for n in range(count):
        await store.load(f"DU/2024/{n}", f"Art. {n}\ntresc", [])
    return store


async def _store_with_sets(count: int) -> ResultStore:
    store = ResultStore(max_sets=count + 1)
    for n in range(count):
        await store.store([], f"zapytanie {n}", 0)
    return store


class TestLoadedDocumentsPage:
    async def test_listing_tools_accept_limit_and_offset(self) -> None:
        store = await _store_with_documents(5)

        output = await store.list_documents_page(limit=2, offset=1)

        assert isinstance(output, LoadedDocumentListOutput)
        assert len(output.documents) == 2
        assert output.count == 2
        assert output.page_info.total_count == 5
        assert output.page_info.offset == 1
        assert output.page_info.next_offset == 3

    async def test_listing_tools_report_item_pages(self) -> None:
        store = await _store_with_documents(2)

        output = await store.list_documents_page()

        assert output.page_info.unit == "items"
        assert output.page_info.limit == DEFAULT_ITEM_LIMIT

    async def test_listing_without_parameters_returns_the_full_store(self) -> None:
        store = await _store_with_documents(10)

        output = await store.list_documents_page()

        assert len(output.documents) == 10
        assert output.page_info.was_truncated is False
        assert output.page_info.next_offset is None

    async def test_limit_is_clamped_to_the_shared_maximum(self) -> None:
        store = await _store_with_documents(3)

        output = await store.list_documents_page(limit=10**9)

        assert output.page_info.limit == 100

    async def test_negative_offset_is_rejected_in_polish(self) -> None:
        store = await _store_with_documents(1)

        with pytest.raises(ValueError, match="Parametr 'offset' nie może być ujemny."):
            await store.list_documents_page(offset=-1)


class TestResultSetsPage:
    async def test_listing_tools_accept_limit_and_offset(self) -> None:
        store = await _store_with_sets(5)

        output = await store.list_sets_page(limit=2, offset=1)

        assert isinstance(output, ResultSetListOutput)
        assert len(output.sets) == 2
        assert output.count == 2
        assert output.page_info.total_count == 5
        assert output.page_info.next_offset == 3

    async def test_listing_tools_report_item_pages(self) -> None:
        store = await _store_with_sets(2)

        output = await store.list_sets_page()

        assert output.page_info.unit == "items"

    async def test_listing_without_parameters_returns_the_full_store(self) -> None:
        store = await _store_with_sets(20)

        output = await store.list_sets_page()

        assert len(output.sets) == 20
        assert output.page_info.was_truncated is False


class TestListingToolsOverTheWire:
    async def test_both_tools_expose_page_info(self, mcp_client) -> None:  # type: ignore[no-untyped-def]
        documents = await mcp_client.call_tool("list_loaded_documents", {})
        sets = await mcp_client.call_tool("list_result_sets", {})

        assert documents.structured_content["data"]["page_info"]["unit"] == "items"
        assert sets.structured_content["data"]["page_info"]["unit"] == "items"

    async def test_both_tools_accept_limit_and_offset(self, mcp_client) -> None:  # type: ignore[no-untyped-def]
        documents = await mcp_client.call_tool("list_loaded_documents", {"limit": 1, "offset": 0})
        sets = await mcp_client.call_tool("list_result_sets", {"limit": 1, "offset": 0})

        assert documents.structured_content["data"]["page_info"]["limit"] == 1
        assert sets.structured_content["data"]["page_info"]["limit"] == 1

    async def test_both_tools_clamp_limit_to_the_shared_maximum(self, mcp_client) -> None:  # type: ignore[no-untyped-def]
        """K11 claims validation identical to the other listing tools.

        The store-level tests prove the clamp inside the store; this proves it
        survives the tool layer, which is where a forgotten parameter would
        actually leak an unbounded page to the client.
        """
        documents = await mcp_client.call_tool("list_loaded_documents", {"limit": 500})
        sets = await mcp_client.call_tool("list_result_sets", {"limit": 500})

        assert documents.structured_content["data"]["page_info"]["limit"] == 100
        assert sets.structured_content["data"]["page_info"]["limit"] == 100

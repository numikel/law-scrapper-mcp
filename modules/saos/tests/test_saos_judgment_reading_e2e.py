"""The whole chain through the MCP layer: load a judgment, read it in pages, search it, list it.

A real in-process server with only the SAOS module, a fake SAOS behind it that serves the recorded skeletons, and
a client on top. Every answer is validated against the `outputSchema` the server advertised, so the optional
`document_id` / `document_kind` and the new `loaded` block are checked where an integrator would meet them.
"""

from __future__ import annotations

import pytest
import respx
from saos_reading_testing import LONE_ID, REASONS_ID, SENTENCE_ID, pair_fake, reading_session

TOOL = "get_saos_judgment_details"
KEY = f"SAOS/{SENTENCE_ID}"


@pytest.mark.asyncio
async def test_a_judgment_is_loaded_read_in_pages_searched_and_listed(saos_router: respx.MockRouter) -> None:
    fake = pair_fake()
    async with reading_session(saos_router, fake) as session:
        details = await session.call(TOOL, {"source_id": str(SENTENCE_ID), "load_content": True})

        loaded = details["loaded"]
        assert loaded["document_id"] == KEY
        assert loaded["reasoning"] == "merged"
        assert loaded["requests_made"] == 3
        assert details["judgment"]["portal_url"]
        assert fake.detail_ids == [SENTENCE_ID, REASONS_ID]

        toc = await session.call("read_act_content", {"eli": KEY})
        assert [entry["title"] for entry in toc["toc"]] == ["Treść", "WYROK", "UZASADNIENIE"]
        assert toc["eli"] == toc["document_id"] == KEY
        assert toc["document_kind"] == "judgment"

        first = await session.call("read_act_content", {"eli": KEY, "section": "UZASADNIENIE", "limit": 100})
        second = await session.call(
            "read_act_content",
            {"eli": KEY, "section": "UZASADNIENIE", "limit": 100, "offset": first["page_info"]["next_offset"]},
        )
        assert first["content"].startswith("## UZASADNIENIE")
        assert first["page_info"]["was_truncated"] is True and first["page_info"]["next_offset"] == 100
        assert second["content"] and second["content"] != first["content"]
        assert second["page_info"]["offset"] == 100

        found = await session.call("search_in_act", {"eli": KEY, "query": "UZASADNIENIE"})
        assert found["total_matches"] == 1
        assert found["matches"][0]["section_id"] == "UZASADNIENIE"
        assert found["document_id"] == KEY and found["document_kind"] == "judgment"

        listing = await session.call("list_loaded_documents", {})
        assert [(doc["document_id"], doc["document_kind"]) for doc in listing["documents"]] == [(KEY, "judgment")]
        assert listing["documents"][0]["eli"] == KEY


@pytest.mark.asyncio
async def test_a_sentence_with_no_reasons_record_shows_the_server_note_when_read(
    saos_router: respx.MockRouter,
) -> None:
    async with reading_session(saos_router, pair_fake()) as session:
        details = await session.call(TOOL, {"source_id": str(LONE_ID), "load_content": True})
        key = details["loaded"]["document_id"]

        toc = await session.call("read_act_content", {"eli": key})
        note = await session.call("read_act_content", {"eli": key, "section": "Uwaga_serwera_Uzasadnienie"})

        assert details["loaded"]["reasoning"] == "absent"
        assert toc["toc"][-1]["title"] == "[Uwaga serwera] Uzasadnienie"
        assert "Brak uzasadnienia w źródle" in note["content"]
        assert "nie tekst orzeczenia" in note["content"]


@pytest.mark.asyncio
async def test_a_judgment_that_is_not_loaded_gets_an_error_that_names_the_source_tool(
    saos_router: respx.MockRouter,
) -> None:
    async with reading_session(saos_router, pair_fake()) as session:
        judgment = await session.fail("read_act_content", {"eli": KEY})
        act = await session.fail("read_act_content", {"eli": "DU/2024/1"})

    assert "get_saos_judgment_details z load_content=true" in judgment
    assert "get_act_details" not in judgment
    # An ELI keeps the text it always had.
    assert "Użyj get_act_details(eli='DU/2024/1', load_content=true)" in act


@pytest.mark.asyncio
async def test_without_load_content_the_text_is_not_loaded(saos_router: respx.MockRouter) -> None:
    async with reading_session(saos_router, pair_fake()) as session:
        details = await session.call(TOOL, {"source_id": str(SENTENCE_ID)})
        listing = await session.call("list_loaded_documents", {})

    assert details["loaded"] is None
    assert listing["documents"] == []


@pytest.mark.asyncio
async def test_the_tool_advertises_the_flag_and_keeps_the_source_id_required(saos_router: respx.MockRouter) -> None:
    async with reading_session(saos_router, pair_fake()) as session:
        tool = session.tools[TOOL]
        properties = tool.input_schema["properties"]

        assert set(properties) == {"source_id", "load_content"}
        assert tool.input_schema["required"] == ["source_id"]
        assert properties["load_content"]["description"].strip()
        assert {"search_saos_judgments", TOOL} <= set(session.tools)
        assert "load_content" in (tool.description or "")


@pytest.mark.asyncio
async def test_bad_input_is_a_polish_tool_error(saos_router: respx.MockRouter) -> None:
    async with reading_session(saos_router, pair_fake(missing_details=[SENTENCE_ID])) as session:
        flag = await session.fail(TOOL, {"source_id": str(SENTENCE_ID), "load_content": "może"})
        missing = await session.fail(TOOL, {"source_id": str(SENTENCE_ID), "load_content": True})

    assert "load_content" in flag
    assert "Nie znaleziono orzeczenia" in missing

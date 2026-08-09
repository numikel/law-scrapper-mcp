"""Tests for MCP server registration and lifespan."""

from __future__ import annotations

import httpx
import pytest
from fastmcp import Client

from law_scrapper_mcp.server import app, lifespan

EXPECTED_TOOLS = sorted(
    [
        "analyze_act_relationships",
        "browse_acts",
        "calculate_legal_date",
        "compare_acts",
        "filter_results",
        "get_act_details",
        "get_system_metadata",
        "list_loaded_documents",
        "list_result_sets",
        "read_act_content",
        "search_in_act",
        "search_legal_acts",
        "track_legal_changes",
    ]
)

LIFESPAN_KEYS = {
    "client",
    "cache",
    "document_store",
    "content_processor",
    "result_store",
    "metadata_service",
    "search_service",
    "act_service",
    "changes_service",
}


async def test_all_tools_registered() -> None:
    """list_tools() returns exactly 13 tools."""
    async with Client(app) as client:
        tools = await client.list_tools()
        tool_names = sorted(tool.name for tool in tools)

    assert len(tool_names) == 13
    assert tool_names == EXPECTED_TOOLS


async def test_tool_names_match_expected() -> None:
    """Tool names match the MCP contract."""
    async with Client(app) as client:
        tools = await client.list_tools()
        tool_names = {tool.name for tool in tools}

    assert tool_names == set(EXPECTED_TOOLS)


async def test_lifespan_yields_services() -> None:
    """Lifespan initializes all required service keys."""
    async with lifespan(app) as ctx:
        assert set(ctx.keys()) == LIFESPAN_KEYS
        assert ctx["metadata_service"] is not None
        assert ctx["search_service"] is not None
        assert ctx["act_service"] is not None


async def test_lifespan_result_store_receives_configured_limits(monkeypatch) -> None:
    """Wartości niedomyślne, celowo — z dwóch powodów naraz.

    512/False/100 to jednocześnie domyślne wartości `Settings` I domyślne argumenty
    konstruktora `ResultStore`, więc test na wartościach domyślnych przechodzi
    również po usunięciu wstrzyknięcia (fałszywy pozytyw) i jest wrażliwy na
    ambientne `LAW_MCP_MAX_PATTERN_LENGTH`/`LAW_MCP_FILTER_MAX_RECORDS` w środowisku
    uruchamiającym (fałszywy negatyw). Wartości 256/42 nie pokrywają się z żadnym
    z tych dwóch źródeł, więc test pada, jeśli wstrzyknięcie zniknie z server.py.
    """
    from law_scrapper_mcp import server as server_module

    monkeypatch.setattr(server_module.settings, "max_pattern_length", 256)
    monkeypatch.setattr(server_module.settings, "filter_max_records", 42)

    async with lifespan(app) as ctx:
        store = ctx["result_store"]
        assert store.max_pattern_length == 256
        assert store.pattern_length_limit_clamped is False
        assert store.max_records == 42


async def test_lifespan_result_store_uses_clamped_limit(monkeypatch) -> None:
    """Konfiguracja spoza widełek dociera do ResultStore jako wartość efektywna."""
    from law_scrapper_mcp import server as server_module

    monkeypatch.setattr(server_module.settings, "max_pattern_length", 10000)
    monkeypatch.setattr(server_module.settings, "filter_max_records", 7)

    async with lifespan(app) as ctx:
        store = ctx["result_store"]

        assert store.max_pattern_length == 4096
        assert store.pattern_length_limit_clamped is True
        assert store.max_records == 7


async def test_lifespan_result_store_floors_zero_record_limit(monkeypatch) -> None:
    """Podłoga z Taska 3 musi zadziałać, a nie zostać obejściem surowego pola.

    Bez niej `LAW_MCP_FILTER_MAX_RECORDS=0` czyniłby filter_results trwale
    niesprawnym: każde wywołanie na niepustym zestawie kończyłoby się odmową.
    Ten test pada, jeśli server.py wstrzyknie `settings.filter_max_records`
    zamiast `settings.effective_filter_max_records`.
    """
    from law_scrapper_mcp import server as server_module

    monkeypatch.setattr(server_module.settings, "filter_max_records", 0)

    async with lifespan(app) as ctx:
        assert ctx["result_store"].max_records == 1


async def test_lifespan_warns_about_clamped_pattern_limit(monkeypatch, caplog) -> None:
    """Fakt przycięcia jest widoczny w logu przy starcie (D3.1, punkt 1)."""
    import logging

    from law_scrapper_mcp import server as server_module

    monkeypatch.setattr(server_module.settings, "max_pattern_length", 10000)

    with caplog.at_level(logging.WARNING):
        async with lifespan(app):
            pass

    assert "10000" in caplog.text
    assert "4096" in caplog.text


async def test_lifespan_closes_client_when_body_raises() -> None:
    """Wyjątek w ciele `async with lifespan(app)` nie może pominąć sprzątania.

    Bez `try/finally` wokół `yield` błąd wewnątrz bloku (np. nieudana asercja
    w teście, albo awaria w produkcji) pomijał `await client.close()` i zostawiał
    otwarty `httpx.AsyncClient`. Asercja sprawdza publiczny stan
    `httpx.AsyncClient.is_closed`, nie sam fakt wywołania close().
    """
    httpx_client: httpx.AsyncClient | None = None

    with pytest.raises(RuntimeError, match="boom"):
        async with lifespan(app) as ctx:
            httpx_client = ctx["client"]._client
            raise RuntimeError("boom")

    assert httpx_client is not None
    assert httpx_client.is_closed is True

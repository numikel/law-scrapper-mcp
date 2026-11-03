"""Server wiring: build_app, tool registration checks, module lifespan and the startup log."""

from __future__ import annotations

import gc
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import Mock

import pytest
from fake_act_source import FakeActSource
from mcp.server import MCPServer

import law_scrapper_mcp.server as server_module
from fake_source_modules import fake_module, loaded, register_named_tools, started
from law_scrapper_mcp.client.egress import CallerIdentity, EgressHub
from law_scrapper_mcp.config import settings
from law_scrapper_mcp.context import AppContext, get_app_context
from law_scrapper_mcp.server import CORE_TOOL_COUNT, SERVER_INSTRUCTIONS, build_app, lifespan
from law_scrapper_mcp.services.act_sources import ActSourceRegistry
from law_scrapper_mcp.source_modules.contract import ModuleHost, SourceModuleError
from law_scrapper_mcp.source_modules.discovery import registered_tool_names
from mcp_helpers import EXPECTED_TOOLS

LOGGER = "law_scrapper_mcp.server"


async def listed_names(server: MCPServer[AppContext]) -> list[str]:
    return sorted(tool.name for tool in await server.list_tools())


# --- build_app ---------------------------------------------------------------------------------


def test_core_tool_count_is_pinned_to_the_live_core_tool_list() -> None:
    assert CORE_TOOL_COUNT == len(EXPECTED_TOOLS) == len(registered_tool_names(build_app()))


def test_without_modules_the_server_is_the_one_before_the_mechanism() -> None:
    server = build_app()

    assert registered_tool_names(server) == frozenset(EXPECTED_TOOLS)
    assert server.instructions == SERVER_INSTRUCTIONS


def test_production_app_has_no_module_in_the_root_suite() -> None:
    assert registered_tool_names(server_module.app) == frozenset(EXPECTED_TOOLS)
    assert server_module.app.instructions == SERVER_INSTRUCTIONS


@pytest.mark.asyncio
async def test_tools_list_is_core_plus_declared_names() -> None:
    module = fake_module("alpha", ["alpha_one", "alpha_two"])

    server = build_app([loaded(module)])

    assert await listed_names(server) == sorted([*EXPECTED_TOOLS, "alpha_one", "alpha_two"])
    assert server.instructions is not None
    assert "DOSTĘPNE NARZĘDZIA (15):" in server.instructions
    assert "MODUŁ ŹRÓDŁA — alpha (narzędzi: 2)" in server.instructions
    assert module.instructions in server.instructions


def test_a_module_may_not_shadow_a_core_tool() -> None:
    """The case the SDK would silently ignore: the duplicate name is dropped with a warning only."""
    calls: list[str] = []

    def register(_server: MCPServer[AppContext]) -> None:
        calls.append("register")

    shadow = fake_module("alpha", ["search_legal_acts"], register=register)

    with pytest.raises(SourceModuleError) as excinfo:
        build_app([loaded(shadow)])

    assert "alpha" in str(excinfo.value)
    assert "search_legal_acts" in str(excinfo.value)
    assert "już istnieje" in str(excinfo.value), "refused by name, not merely by the count afterwards"
    assert "LAW_MCP_SOURCE_MODULES" in str(excinfo.value)
    assert calls == [], "a module that declares a taken name never gets to register anything"


def test_two_modules_may_not_claim_the_same_tool() -> None:
    alpha = fake_module("alpha", ["shared_tool"])
    beta = fake_module("beta", ["shared_tool"])

    with pytest.raises(SourceModuleError, match="beta") as excinfo:
        build_app([loaded(alpha), loaded(beta)])

    assert "już istnieje" in str(excinfo.value)


def test_registering_more_than_declared_is_refused() -> None:
    greedy = fake_module("alpha", ["alpha_one"], register=register_named_tools("alpha_one", "alpha_extra"))

    with pytest.raises(SourceModuleError) as excinfo:
        build_app([loaded(greedy)])

    assert "alpha_extra" in str(excinfo.value)


def test_registering_less_than_declared_is_refused() -> None:
    lazy = fake_module("alpha", ["alpha_one", "alpha_two"], register=register_named_tools("alpha_one"))

    with pytest.raises(SourceModuleError) as excinfo:
        build_app([loaded(lazy)])

    assert "alpha_two" in str(excinfo.value)


def test_header_count_follows_the_registered_tools() -> None:
    server = build_app([loaded(fake_module("alpha", ["a_one"])), loaded(fake_module("beta", ["b_one", "b_two"]))])

    assert server.instructions is not None
    assert "DOSTĘPNE NARZĘDZIA (16):" in server.instructions
    assert len(registered_tool_names(server)) == 16


def test_the_module_set_of_a_discarded_server_is_not_kept_alive() -> None:
    gc.collect()  # servers built by earlier tests may still be waiting to be collected
    before = len(server_module._DISCOVERIES)
    server = build_app([loaded(fake_module("alpha"))])
    assert len(server_module._DISCOVERIES) == before + 1

    del server
    gc.collect()

    assert len(server_module._DISCOVERIES) == before


# --- lifespan ----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_module_state_is_reachable_through_get_app_context() -> None:
    state = object()
    server = build_app([loaded(fake_module("alpha", start=started(state)))])

    async with lifespan(server) as app_context:
        ctx = Mock()
        ctx.request_context.lifespan_context = app_context
        assert get_app_context(ctx).module_state["alpha"] is state


@pytest.mark.asyncio
async def test_a_module_without_a_start_hook_has_no_state_entry() -> None:
    server = build_app([loaded(fake_module("alpha"))])

    async with lifespan(server) as app_context:
        assert dict(app_context.module_state) == {}


@pytest.mark.asyncio
async def test_start_hook_receives_the_hub_the_derived_identity_and_the_settings() -> None:
    seen: list[ModuleHost] = []

    def hook(host: ModuleHost) -> object:
        seen.append(host)
        return started("state")(host)

    server = build_app(
        [loaded(fake_module("alpha", start=hook), distribution="law-scrapper-mcp-alpha", version="1.2.3")]
    )  # type: ignore[arg-type]

    async with lifespan(server) as app_context:
        (host,) = seen
        assert host.egress is app_context.egress
        assert isinstance(host.egress, EgressHub)
        assert host.caller == CallerIdentity(module="law-scrapper-mcp-alpha", version="1.2.3")
        assert host.settings is settings
        assert isinstance(host.act_sources, ActSourceRegistry)


@pytest.mark.asyncio
async def test_a_source_registered_by_a_start_hook_is_served_by_the_context_services() -> None:
    """The registry in `ModuleHost` is the very object the four routed services consult."""
    source = FakeActSource(total=3)

    @asynccontextmanager
    async def hook(host: ModuleHost) -> AsyncIterator[None]:
        host.act_sources.register(source, publishers=["ZZ_TEST"])
        try:
            yield None
        finally:
            host.act_sources.unregister(source)

    server = build_app([loaded(fake_module("alpha", start=hook))])  # type: ignore[arg-type]

    async with lifespan(server) as app_context:
        output = await app_context.search_service.search("ZZ_TEST")
        details = await app_context.act_service.get_details("ZZ_TEST/2025/2")
        relations = await app_context.relationship_service.get_relationships("ZZ_TEST/2025/2")
        changes = await app_context.changes_service.track_changes("ZZ_TEST", "2025-01-01", "2025-12-31")

    assert output.total_count == 3
    assert details.title == "Uchwała w sprawie budżetu"
    assert relations.total_count == 1
    assert len(changes.changes) == 3
    assert [call[0] for call in source.calls] == ["search", "get_details", "get_relationships", "search"]


@pytest.mark.asyncio
async def test_without_a_registering_module_the_registry_is_empty() -> None:
    seen: list[ModuleHost] = []

    def hook(host: ModuleHost) -> object:
        seen.append(host)
        return started("state")(host)

    server = build_app([loaded(fake_module("alpha", start=hook))])  # type: ignore[arg-type]

    async with lifespan(server):
        (host,) = seen
        assert host.act_sources.publishers() == ()
        assert host.act_sources.for_publisher("POL_WOJ_PM") is None


@pytest.mark.asyncio
async def test_identity_that_is_not_a_token_stops_startup_naming_the_module() -> None:
    events: list[str] = []
    server = build_app([loaded(fake_module("alpha", start=started("x", events)), distribution="bad name")])

    with pytest.raises(SourceModuleError) as excinfo:
        async with lifespan(server):
            pytest.fail("the lifespan must not start")

    assert "alpha" in str(excinfo.value)
    assert "LAW_MCP_SOURCE_MODULES" in str(excinfo.value)
    assert events == []


@pytest.mark.asyncio
async def test_modules_start_in_order_and_stop_in_reverse_before_the_hub_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    original_close = EgressHub.close

    async def recording_close(self: EgressHub) -> None:
        events.append("hub-closed")
        await original_close(self)

    monkeypatch.setattr(EgressHub, "close", recording_close)
    server = build_app(
        [
            loaded(fake_module("alpha", start=started("a", events, "alpha"))),
            loaded(fake_module("beta", start=started("b", events, "beta"))),
        ]
    )

    async with lifespan(server):
        assert events == ["start:alpha", "start:beta"]

    assert events == ["start:alpha", "start:beta", "stop:beta", "stop:alpha", "hub-closed"]


@pytest.mark.asyncio
async def test_a_failing_start_aborts_the_lifespan_and_closes_what_was_opened(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    def failing(_host: ModuleHost) -> object:
        raise RuntimeError("upstream nieosiągalny")

    server = build_app(
        [
            loaded(fake_module("alpha", start=started("a", events, "alpha"))),
            loaded(fake_module("beta", start=failing)),  # type: ignore[arg-type]
            loaded(fake_module("gamma", start=started("c", events, "gamma"))),
        ]
    )

    with pytest.raises(SourceModuleError) as excinfo:
        async with lifespan(server):
            pytest.fail("the lifespan must not yield")

    message = str(excinfo.value)
    assert "beta" in message
    assert "upstream nieosiągalny" in message
    assert "-beta" in message
    assert events == ["start:alpha", "stop:alpha"], "alpha is closed, gamma never started"
    assert server_module._health_state.sources() == [], "the shared state is cleared on a failed start"


@pytest.mark.asyncio
async def test_a_start_error_that_is_already_a_source_module_error_passes_through() -> None:
    def failing(_host: ModuleHost) -> object:
        raise SourceModuleError("Moduł alpha: własny komunikat.")

    server = build_app([loaded(fake_module("alpha", start=failing))])  # type: ignore[arg-type]

    with pytest.raises(SourceModuleError, match="własny komunikat"):
        async with lifespan(server):
            pytest.fail("the lifespan must not yield")


# --- the startup log ---------------------------------------------------------------------------


def source_module_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        record
        for record in caplog.records
        if record.name == LOGGER and record.getMessage().startswith("Source modules")
    ]


@pytest.mark.asyncio
async def test_info_line_names_installed_active_disabled_and_the_variable(caplog: pytest.LogCaptureFixture) -> None:
    server = build_app([loaded(fake_module("woj"))], inactive=["reference", "saos"], requested=["-*", "woj"])

    with caplog.at_level(logging.INFO, logger=LOGGER):
        async with lifespan(server):
            pass

    (record,) = source_module_records(caplog)
    assert record.levelno == logging.INFO
    assert record.getMessage() == (
        "Source modules: installed=reference,saos,woj active=woj disabled=reference,saos "
        "LAW_MCP_SOURCE_MODULES='-*,woj'"
    )


@pytest.mark.asyncio
async def test_info_line_says_unset_when_the_variable_has_no_tokens(caplog: pytest.LogCaptureFixture) -> None:
    server = build_app([loaded(fake_module("woj"))])

    with caplog.at_level(logging.INFO, logger=LOGGER):
        async with lifespan(server):
            pass

    (record,) = source_module_records(caplog)
    assert record.getMessage() == (
        "Source modules: installed=woj active=woj disabled=none LAW_MCP_SOURCE_MODULES=(unset)"
    )


@pytest.mark.asyncio
async def test_info_line_shows_a_bare_name_override_as_it_was_supplied(caplog: pytest.LogCaptureFixture) -> None:
    """A bare `woj` keeps every module on; the line must make that visible in the first log."""
    server = build_app(
        [loaded(fake_module("reference")), loaded(fake_module("saos")), loaded(fake_module("woj"))],
        requested=["woj"],
    )

    with caplog.at_level(logging.INFO, logger=LOGGER):
        async with lifespan(server):
            pass

    (record,) = source_module_records(caplog)
    assert "active=reference,saos,woj" in record.getMessage()
    assert "LAW_MCP_SOURCE_MODULES='woj'" in record.getMessage()


@pytest.mark.asyncio
async def test_no_line_at_all_when_no_module_is_installed(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger=LOGGER):
        async with lifespan(build_app()):
            pass

    assert source_module_records(caplog) == []


@pytest.mark.asyncio
async def test_warning_names_only_the_ignored_unknown_names(caplog: pytest.LogCaptureFixture) -> None:
    server = build_app(
        [loaded(fake_module("alpha")), loaded(fake_module("beta"))],
        requested=["-saoss"],
        ignored_unknown=["saoss", "wojj"],
    )

    with caplog.at_level(logging.INFO, logger=LOGGER):
        async with lifespan(server):
            pass

    warnings = [record for record in source_module_records(caplog) if record.levelno == logging.WARNING]
    (warning,) = warnings
    assert warning.getMessage() == (
        "Source modules: LAW_MCP_SOURCE_MODULES names modules that are not installed and are switched off "
        "or overridden, ignoring them: saoss,wojj (installed=alpha,beta)"
    )
    assert len(source_module_records(caplog)) == 2, "one INFO and one WARNING"


@pytest.mark.asyncio
async def test_warning_is_absent_without_ignored_names_and_present_with_no_module_installed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger=LOGGER):
        async with lifespan(build_app([loaded(fake_module("alpha"))])):
            pass
    assert [r for r in source_module_records(caplog) if r.levelno == logging.WARNING] == []

    caplog.clear()
    with caplog.at_level(logging.INFO, logger=LOGGER):
        async with lifespan(build_app(requested=["-saoss"], ignored_unknown=["saoss"])):
            pass
    (warning,) = source_module_records(caplog)
    assert warning.levelno == logging.WARNING
    assert warning.getMessage().endswith("ignoring them: saoss (installed=none)")

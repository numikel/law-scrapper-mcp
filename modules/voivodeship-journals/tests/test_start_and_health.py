"""The start hook: clients through the hub only, registration and unregistration, identity, health labels."""

from __future__ import annotations

from unittest.mock import Mock

import httpx
import pytest
import respx
from journal_support import CALLER, PM, Started, load_json

from law_scrapper_mcp.client.cache import TTLCache
from law_scrapper_mcp.client.egress import EgressDefaults, EgressHub
from law_scrapper_mcp.client.exceptions import EgressConfigError
from law_scrapper_mcp.config import Settings
from law_scrapper_mcp.context import AppContext
from law_scrapper_mcp.services.act_sources import ActSourceRegistry, SourceSearchQuery
from law_scrapper_mcp.source_modules.contract import ModuleHost, SourceModule
from law_scrapper_mcp_voivodeship_journals import module, start
from law_scrapper_mcp_voivodeship_journals.catalog import JOURNALS, SERVED, WITHHELD
from law_scrapper_mcp_voivodeship_journals.state import MODULE_NAME, JournalsState, journals_state


def _fresh_host() -> tuple[ModuleHost, EgressHub, ActSourceRegistry]:
    hub = EgressHub(TTLCache(max_entries=100), EgressDefaults(user_agent=Settings().user_agent))
    registry = ActSourceRegistry()
    return ModuleHost(egress=hub, caller=CALLER, settings=Settings(), act_sources=registry), hub, registry


# --- descriptor --------------------------------------------------------------------------------


def test_the_descriptor_declares_no_tool_and_a_start_hook() -> None:
    assert isinstance(module, SourceModule)
    assert module.name == "voivodeship-journals" == MODULE_NAME
    assert module.tool_names == ()
    assert module.start is not None


def test_the_instructions_list_every_served_code_and_every_withheld_one_with_its_reason() -> None:
    text = module.instructions

    for journal in SERVED:
        assert journal.code in text
    for journal in WITHHELD:
        assert journal.code in text
        assert journal.withheld in text
    for rule in ("in_force", "date_from", "pub_date_from", "limit powyżej 100", "PDF", "(cid:N)"):
        assert rule in text


# --- registration ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_hook_registers_exactly_the_served_journals_on_the_hub(started: Started) -> None:
    states = started.hub.source_states()

    assert [state.name for state in states] == [journal.code for journal in SERVED]
    assert len(states) == 12
    assert not {state.name for state in states} & {journal.code for journal in WITHHELD}


@pytest.mark.asyncio
async def test_the_registry_serves_every_code_and_the_whole_prefix_but_not_the_core_codes(started: Started) -> None:
    registry = started.registry

    assert registry.publishers() == tuple(sorted(journal.code for journal in JOURNALS))
    assert registry.for_publisher("POL_WOJ_PM") is registry.for_publisher("POL_WOJ_MZ")
    assert registry.for_publisher("POL_WOJ_XX") is not None, "an unserved code is refused by the source, loudly"
    assert registry.for_publisher("DU") is None
    assert registry.for_publisher("MP") is None


@pytest.mark.asyncio
async def test_the_hook_leaves_the_served_codes_as_state() -> None:
    host, hub, _ = _fresh_host()

    async with start(host) as state:
        assert isinstance(state, JournalsState)
        assert state.codes == tuple(journal.code for journal in SERVED)
    await hub.close()


@pytest.mark.asyncio
async def test_the_accessor_returns_the_state_and_refuses_a_missing_one() -> None:
    state = JournalsState(codes=("POL_WOJ_PM",))

    assert journals_state(Mock(spec=AppContext, module_state={MODULE_NAME: state})) is state
    with pytest.raises(RuntimeError, match="voivodeship-journals"):
        journals_state(Mock(spec=AppContext, module_state={}))


@pytest.mark.asyncio
@respx.mock
async def test_starting_sends_no_request() -> None:
    """`respx` rejects any unmocked request, so a clean start proves the hook is offline."""
    host, hub, _ = _fresh_host()

    async with start(host):
        pass
    await hub.close()


# --- unregistration ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_leaving_the_hook_unregisters_the_source() -> None:
    host, hub, registry = _fresh_host()

    async with start(host):
        assert registry.for_publisher("POL_WOJ_PM") is not None
    await hub.close()

    assert registry.for_publisher("POL_WOJ_PM") is None
    assert registry.publishers() == ()


@pytest.mark.asyncio
async def test_a_failure_inside_the_hook_still_unregisters_the_source() -> None:
    host, hub, registry = _fresh_host()

    with pytest.raises(RuntimeError, match="boom"):
        async with start(host):
            raise RuntimeError("boom")
    await hub.close()

    assert registry.publishers() == ()


@pytest.mark.asyncio
async def test_a_second_start_in_the_same_hub_fails_on_the_origin_claim(started: Started) -> None:
    with pytest.raises(EgressConfigError, match="już"):
        async with start(started.host):
            pytest.fail("the second start must not get past client_for")

    assert started.registry.for_publisher("POL_WOJ_PM") is not None, "the first start is untouched"
    assert len(started.hub.source_states()) == 12


# --- identity and health -----------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_traffic_carries_the_module_identity_in_the_user_agent(started: Started) -> None:
    route = respx.get(f"{PM}/search").mock(return_value=httpx.Response(200, json=load_json("search_page_camel.json")))
    source = started.registry.for_publisher("POL_WOJ_PM")
    assert source is not None

    await source.search("POL_WOJ_PM", SourceSearchQuery(year=2025, limit=1))

    agent = route.calls.last.request.headers["user-agent"]
    assert agent == f"{Settings().user_agent} law-scrapper-mcp-voivodeship-journals/0.0.0"


@pytest.mark.asyncio
async def test_each_host_is_labelled_by_its_publisher_code_and_starts_closed(started: Started) -> None:
    states = started.hub.source_states()

    assert {state.circuit_state.value for state in states} == {"closed"}
    assert all(state.name.startswith("POL_WOJ_") for state in states)


# --- installation ------------------------------------------------------------------------------


def test_the_entry_point_of_the_installed_distribution_loads_this_descriptor() -> None:
    import importlib.metadata

    from law_scrapper_mcp.source_modules.contract import ENTRY_POINT_GROUP

    found = [ep for ep in importlib.metadata.entry_points(group=ENTRY_POINT_GROUP) if ep.name == MODULE_NAME]

    assert len(found) == 1, "the member is not installed: run `uv sync --extra dev --all-packages`"
    assert found[0].value == "law_scrapper_mcp_voivodeship_journals:module"
    assert found[0].dist is not None
    assert (found[0].dist.name, found[0].dist.version) == ("law-scrapper-mcp-voivodeship-journals", "0.0.0")
    assert found[0].load() is module

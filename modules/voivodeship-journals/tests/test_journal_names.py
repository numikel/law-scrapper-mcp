"""Voivodeship names, the descriptions the module registers, and its refusals of unknown publishers."""

from __future__ import annotations

import httpx
import pytest
import respx
from journal_support import CALLER, Started, load_json

from law_scrapper_mcp.client.cache import TTLCache
from law_scrapper_mcp.client.egress import EgressDefaults, EgressHub
from law_scrapper_mcp.client.exceptions import ApiUnavailableError
from law_scrapper_mcp.config import Settings, settings
from law_scrapper_mcp.services.act_sources import (
    MAX_PUBLISHER_NAME_LENGTH,
    ActSource,
    ActSourceRegistry,
    SourceSearchQuery,
    UnknownPublisherError,
    fold_publisher_name,
)
from law_scrapper_mcp.source_modules.contract import ModuleHost
from law_scrapper_mcp_voivodeship_journals import catalog, start
from law_scrapper_mcp_voivodeship_journals import source as source_module
from law_scrapper_mcp_voivodeship_journals.catalog import (
    BY_CODE,
    JOURNALS,
    PUBLISHER_INFO,
    SERVED,
    WITHHELD,
    Journal,
    names_index,
)
from law_scrapper_mcp_voivodeship_journals.source import JournalActSource

pytestmark = pytest.mark.asyncio

BY_NAME = names_index(JOURNALS)


def pm_source(started: Started) -> ActSource:
    source = started.registry.for_publisher("POL_WOJ_PM")
    assert source is not None
    return source


# --- the name index ----------------------------------------------------------------------------


async def test_every_voivodeship_name_is_indexed_exactly_once() -> None:
    assert len(BY_NAME) == 16
    assert {journal.code for journal in BY_NAME.values()} == {journal.code for journal in JOURNALS}


@pytest.mark.parametrize(
    ("typed", "code"),
    [
        ("pomorskie", "POL_WOJ_PM"),
        ("Pomorskie", "POL_WOJ_PM"),
        ("Łódzkie", "POL_WOJ_LD"),
        ("lodzkie", "POL_WOJ_LD"),
        ("ŚLĄSKIE", "POL_WOJ_SL"),
        ("swietokrzyskie", "POL_WOJ_SK"),
        ("kujawsko pomorskie", "POL_WOJ_KP"),
        ("Warmińsko_Mazurskie", "POL_WOJ_WM"),
    ],
)
async def test_a_name_is_found_however_it_is_typed(typed: str, code: str) -> None:
    assert BY_NAME[fold_publisher_name(typed)].code == code


async def test_pomorskie_does_not_match_as_a_suffix_of_kujawsko_pomorskie() -> None:
    assert BY_NAME[fold_publisher_name("pomorskie")].code == "POL_WOJ_PM"
    assert fold_publisher_name("pomorskie") != fold_publisher_name("kujawsko-pomorskie")
    assert "kujawsko" not in BY_NAME


async def test_the_source_resolves_names_from_the_catalog_it_was_given() -> None:
    """The source builds its name index from its own catalog argument, not from a module-level copy."""
    own = [Journal("POL_WOJ_ZZ", "Zzzlandia", "zzz.example", "Dz. Urz. Woj. Zzzlandii", "wyłączony na próbę")]
    source = JournalActSource({}, own, Settings())

    with pytest.raises(ApiUnavailableError) as excinfo:
        await source.search("ZZZLANDIA", SourceSearchQuery(year=2025))

    assert excinfo.value.source == "POL_WOJ_ZZ" and "wyłączony na próbę" in str(excinfo.value)
    with pytest.raises(UnknownPublisherError):
        await source.search("pomorskie", SourceSearchQuery(year=2025))


async def test_a_value_too_long_to_be_a_name_is_refused_without_being_normalised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Under the claimed prefix a caller can send any length; the fold must not run over it."""

    def forbidden(_text: str) -> str:
        raise AssertionError("a value longer than any name was normalised")

    monkeypatch.setattr(source_module, "fold_publisher_name", forbidden)
    source = JournalActSource({}, JOURNALS, Settings())
    too_long = "POL_WOJ_" + "x" * (MAX_PUBLISHER_NAME_LENGTH + 1)

    with pytest.raises(UnknownPublisherError):
        await source.search(too_long, SourceSearchQuery(year=2025))


async def test_two_names_that_fold_to_the_same_text_fail_at_import_time(monkeypatch: pytest.MonkeyPatch) -> None:
    clash = Journal("POL_WOJ_ZZ", "ŁÓDZKIE", "other.example", "x")
    monkeypatch.setattr(catalog, "JOURNALS", (*JOURNALS, clash))

    with pytest.raises(ValueError, match="nazwy województw"):
        catalog._check_invariants()


async def test_an_empty_name_fails_at_import_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(catalog, "JOURNALS", (*JOURNALS, Journal("POL_WOJ_ZZ", "  ", "other.example", "x")))

    with pytest.raises(ValueError, match="niepuste"):
        catalog._check_invariants()


# --- the descriptions --------------------------------------------------------------------------


async def test_the_descriptions_cover_all_sixteen_and_give_a_reason_only_for_the_withheld() -> None:
    assert [info.code for info in PUBLISHER_INFO] == [journal.code for journal in JOURNALS]
    for info in PUBLISHER_INFO:
        journal = BY_CODE[info.code]
        assert info.label == journal.voivodeship
        assert info.available is (journal.withheld is None)
        assert info.item["code"] == journal.code
        assert info.item["voivodeship"] == journal.voivodeship
        assert info.item["shortName"] == journal.short_name
        assert info.item["available"] is info.available
        if journal.withheld is None:
            assert set(info.item) == {"code", "voivodeship", "shortName", "available"}
        else:
            assert info.item["withheldReason"] == journal.withheld


async def test_the_hook_registers_the_descriptions_and_the_names(started: Started) -> None:
    registry = started.registry

    assert registry.describe() == tuple(sorted(PUBLISHER_INFO, key=lambda info: info.code))
    assert registry.for_publisher("Pomorskie") is registry.for_publisher("POL_WOJ_PM")
    assert registry.for_publisher("mazowieckie") is registry.for_publisher("POL_WOJ_MZ"), "a withheld one is named too"


async def test_leaving_the_hook_drops_the_descriptions_and_the_names() -> None:
    hub = EgressHub(TTLCache(max_entries=100), EgressDefaults(user_agent=Settings().user_agent))
    registry = ActSourceRegistry()
    host = ModuleHost(egress=hub, caller=CALLER, settings=Settings(), act_sources=registry)

    async with start(host):
        assert len(registry.describe()) == 16
    await hub.close()

    assert registry.describe() == ()
    assert registry.for_publisher("pomorskie") is None, "an empty registry leaves any value to the Sejm"


# --- a name in the publisher parameter ---------------------------------------------------------


@respx.mock
@pytest.mark.parametrize("journal", SERVED, ids=lambda journal: journal.code)
@pytest.mark.parametrize("style", ["upper", "ascii"])
async def test_a_name_reaches_its_own_journal_host_and_no_other(started: Started, journal: Journal, style: str) -> None:
    typed = journal.voivodeship.upper() if style == "upper" else fold_publisher_name(journal.voivodeship)
    route = respx.get(f"{journal.base_url}/search").mock(
        return_value=httpx.Response(200, json=load_json("search_page_camel.json"))
    )
    source = started.registry.for_publisher(typed)
    assert source is not None

    await source.search(typed, SourceSearchQuery(year=2025, limit=1))

    # Any other host is unmocked, so a wrong resolution would have raised instead of passing.
    assert route.call_count == 1
    assert dict(route.calls.last.request.url.params) == {"limit": "1", "year": "2025"}


@respx.mock
async def test_a_withheld_journal_addressed_by_name_fails_with_its_reason(started: Started) -> None:
    route = respx.route().mock(return_value=httpx.Response(200, json={}))

    with pytest.raises(ApiUnavailableError) as excinfo:
        await pm_source(started).search("mazowieckie", SourceSearchQuery(year=2025))

    assert excinfo.value.source == "POL_WOJ_MZ"
    assert "POL_WOJ_MZ" in str(excinfo.value) and "wyłączony" in str(excinfo.value)
    assert route.call_count == 0


# --- a name inside an ELI ----------------------------------------------------------------------


@respx.mock
@pytest.mark.parametrize("operation", ["details", "relations", "pdf", "pdf_url"])
async def test_a_name_inside_an_eli_is_refused_with_the_code_to_use(started: Started, operation: str) -> None:
    route = respx.route().mock(return_value=httpx.Response(500))
    source = pm_source(started)

    with pytest.raises(ValueError, match="W ELI podaj kod") as excinfo:
        if operation == "details":
            await source.get_details("pomorskie", 2025, 5051)
        elif operation == "relations":
            await source.get_relationships("pomorskie", 2025, 5051)
        elif operation == "pdf":
            await source.get_pdf("pomorskie", 2025, 5051, max_bytes=1000)
        else:
            source.pdf_url("pomorskie", 2025, 5051)

    text = str(excinfo.value)
    assert "pomorskie = POL_WOJ_PM" in text
    assert "POL_WOJ_PM/2025/1" in text
    assert not isinstance(excinfo.value, UnknownPublisherError), "the name is known, only its place is wrong"
    assert route.call_count == 0


@respx.mock
async def test_a_code_inside_an_eli_still_works(started: Started) -> None:
    route = respx.get(f"{BY_CODE['POL_WOJ_PM'].base_url}/acts/POL_WOJ_PM/2025/5051").mock(
        return_value=httpx.Response(200, json=load_json("act_detail_camel.json"))
    )

    result = await pm_source(started).get_details("POL_WOJ_PM", 2025, 5051)

    assert result.eli == "POL_WOJ_PM/2025/5051"
    assert route.call_count == 1


# --- an unknown publisher ----------------------------------------------------------------------


@respx.mock
@pytest.mark.parametrize("value", ["xyz", "POL_WOJ_XX"])
async def test_an_unknown_value_is_refused_with_the_served_codes_and_their_names(started: Started, value: str) -> None:
    route = respx.route().mock(return_value=httpx.Response(200, json={}))

    with pytest.raises(UnknownPublisherError) as excinfo:
        await pm_source(started).search(value, SourceSearchQuery(year=2025))

    text = str(excinfo.value)
    assert value in text
    assert "POL_WOJ_PM (pomorskie)" in text and "POL_WOJ_SK (świętokrzyskie)" in text
    assert "Zamiast kodu można podać nazwę" in text
    assert "DU, MP" in text
    assert not any(journal.code in text for journal in WITHHELD), "a withheld journal is not offered as valid"
    assert route.call_count == 0


async def test_the_registry_and_the_module_refuse_an_unknown_value_in_the_same_words(started: Started) -> None:
    with pytest.raises(UnknownPublisherError) as from_source:
        await pm_source(started).search("xyz", SourceSearchQuery(year=2025))
    with pytest.raises(UnknownPublisherError) as from_registry:
        started.registry.for_publisher("xyz")

    assert str(from_source.value) == str(from_registry.value)


@pytest.mark.parametrize("value", ["xyz", "x" * 40, "x" * 500])
async def test_the_refusal_fits_the_tool_error_budget_with_the_real_catalog(started: Started, value: str) -> None:
    with pytest.raises(UnknownPublisherError) as excinfo:
        started.registry.for_publisher(value)

    assert len(str(excinfo.value)) <= settings.error_message_max_chars

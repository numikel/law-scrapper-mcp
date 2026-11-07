"""Publisher descriptions, names and the refusal of unknown publishers in the act-source registry."""

from __future__ import annotations

from typing import Any

import pytest

from fake_act_source import FakeActSource
from law_scrapper_mcp.services import act_sources
from law_scrapper_mcp.services.act_sources import (
    MAX_PUBLISHER_NAME_LENGTH,
    UNKNOWN_PUBLISHER_VALUE_LIMIT,
    ActSourceConflictError,
    ActSourceRegistry,
    PublisherInfo,
    UnknownPublisherError,
    fold_publisher_name,
    unknown_publisher_message,
)
from law_scrapper_mcp.tools.error_handling import _classify_error

# The names of the twelve voivodeship journals that are served, so the length checks below use
# the size of the real listing (the module tests pin the real catalog separately).
SERVED_NAMES = (
    "dolnośląskie",
    "kujawsko-pomorskie",
    "lubelskie",
    "opolskie",
    "podkarpackie",
    "podlaskie",
    "pomorskie",
    "śląskie",
    "świętokrzyskie",
    "warmińsko-mazurskie",
    "wielkopolskie",
    "zachodniopomorskie",
)


def info(code: str, label: str, *, available: bool = True, **item: Any) -> PublisherInfo:
    return PublisherInfo(code=code, label=label, available=available, item={"code": code, "label": label, **item})


def served_infos() -> list[PublisherInfo]:
    return [info(f"ZZ_{index:02d}", name) for index, name in enumerate(SERVED_NAMES)]


def registry_with(
    *infos: PublisherInfo, source: FakeActSource | None = None
) -> tuple[ActSourceRegistry, FakeActSource]:
    registry = ActSourceRegistry()
    source = source or FakeActSource()
    registry.register(source, publishers=[entry.code for entry in infos], prefixes=["ZZ_"], info=infos)
    return registry, source


@pytest.mark.parametrize(
    ("given", "folded"),
    [
        ("pomorskie", "pomorskie"),
        ("Pomorskie", "pomorskie"),
        ("Łódzkie", "lodzkie"),
        ("lodzkie", "lodzkie"),
        ("śląskie", "slaskie"),
        ("ŚWIĘTOKRZYSKIE", "swietokrzyskie"),
        ("Kujawsko Pomorskie", "kujawsko-pomorskie"),
        ("KUJAWSKO_POMORSKIE", "kujawsko-pomorskie"),
        ("  kujawsko -- pomorskie  ", "kujawsko-pomorskie"),
    ],
)
def test_names_are_compared_without_case_diacritics_or_separator_style(given: str, folded: str) -> None:
    assert fold_publisher_name(given) == folded


@pytest.mark.parametrize("given", ["pomorskie", "Pomorskie", "POMORSKIE", " pomorskie "])
def test_a_registered_name_reaches_the_source_that_registered_it(given: str) -> None:
    registry, source = registry_with(info("ZZ_PM", "pomorskie"))

    assert registry.for_publisher(given) is source


def test_a_name_matches_exactly_and_never_as_a_suffix_of_another() -> None:
    registry, source = registry_with(info("ZZ_KP", "kujawsko-pomorskie"))

    assert registry.for_publisher("Kujawsko Pomorskie") is source
    with pytest.raises(UnknownPublisherError):
        registry.for_publisher("pomorskie")


def test_two_names_resolve_to_their_own_sources_only() -> None:
    registry = ActSourceRegistry()
    first, second = FakeActSource(), FakeActSource()
    registry.register(first, publishers=["AA_1"], info=[info("AA_1", "lubelskie")])
    registry.register(second, publishers=["BB_1"], info=[info("BB_1", "opolskie")])

    assert registry.for_publisher("lubelskie") is first
    assert registry.for_publisher("opolskie") is second


def test_describe_returns_every_description_sorted_by_code() -> None:
    registry, _ = registry_with(info("ZZ_B", "beta"), info("ZZ_A", "alfa", available=False))

    assert [entry.code for entry in registry.describe()] == ["ZZ_A", "ZZ_B"]
    assert [entry.available for entry in registry.describe()] == [False, True]


def test_unregister_drops_the_descriptions_and_the_names_of_that_source() -> None:
    registry, source = registry_with(info("ZZ_PM", "pomorskie"))
    other = FakeActSource()
    registry.register(other, publishers=["OTHER_1"])

    registry.unregister(source)

    assert registry.describe() == ()
    with pytest.raises(UnknownPublisherError):
        registry.for_publisher("pomorskie")


def test_a_description_for_a_code_the_source_did_not_register_is_refused() -> None:
    registry = ActSourceRegistry()

    with pytest.raises(ActSourceConflictError, match="ZZ_PM"):
        registry.register(FakeActSource(), publishers=["ZZ_OTHER"], info=[info("ZZ_PM", "pomorskie")])

    assert registry.describe() == ()
    assert registry.publishers() == ()


@pytest.mark.parametrize("label", ["", "   ", "DU", "mp", "ZZ_PM", "zz-pm"])
def test_an_empty_reserved_or_code_like_name_is_refused(label: str) -> None:
    registry = ActSourceRegistry()

    with pytest.raises(ActSourceConflictError):
        registry.register(FakeActSource(), publishers=["ZZ_PM"], info=[info("ZZ_PM", label)])

    assert registry.describe() == ()
    assert registry.publishers() == ()


def test_a_name_that_is_already_taken_is_refused_and_nothing_is_half_registered() -> None:
    registry = ActSourceRegistry()
    first = FakeActSource()
    registry.register(first, publishers=["AA_1"], info=[info("AA_1", "Łódzkie")])

    with pytest.raises(ActSourceConflictError, match="lodzkie|Łódzkie"):
        registry.register(
            FakeActSource(), publishers=["BB_1", "BB_2"], info=[info("BB_1", "ok"), info("BB_2", "lodzkie")]
        )
    with pytest.raises(ActSourceConflictError):
        registry.register(
            FakeActSource(), publishers=["CC_1", "CC_2"], info=[info("CC_1", "same"), info("CC_2", "Same")]
        )

    assert registry.publishers() == ("AA_1",)
    assert [entry.code for entry in registry.describe()] == ["AA_1"]


def test_an_empty_registry_leaves_every_value_to_the_sejm() -> None:
    registry = ActSourceRegistry()

    for value in ("DU", "du", "xyz", "pomorskie", "POL_WOJ_PM", ""):
        assert registry.for_publisher(value) is None


def test_with_a_source_registered_an_unclaimed_value_is_refused() -> None:
    registry, _ = registry_with(info("ZZ_PM", "pomorskie"))

    with pytest.raises(UnknownPublisherError) as excinfo:
        registry.for_publisher("xyz")

    message = str(excinfo.value)
    assert "xyz" in message
    assert "ZZ_PM (pomorskie)" in message
    assert "DU, MP" in message


@pytest.mark.parametrize("code", ["DU", "MP", "du", "Mp"])
def test_the_codes_the_core_serves_are_never_refused_in_any_letter_case(code: str) -> None:
    registry, _ = registry_with(info("ZZ_PM", "pomorskie"))

    assert registry.for_publisher(code) is None


def test_a_code_under_a_claimed_prefix_still_reaches_its_source() -> None:
    registry, source = registry_with(info("ZZ_PM", "pomorskie"))

    assert registry.for_publisher("ZZ_UNSERVED") is source


def test_a_source_without_descriptions_still_makes_the_registry_refuse_unknown_values() -> None:
    registry = ActSourceRegistry()
    registry.register(FakeActSource(), publishers=["AA_1"])

    with pytest.raises(UnknownPublisherError) as excinfo:
        registry.for_publisher("xyz")

    assert "Poprawne: DU, MP, AA_1." in str(excinfo.value), "a served but undescribed code is valid too"
    assert "Zamiast kodu" not in str(excinfo.value), "no name to suggest, so no hint"


def test_undescribed_codes_follow_the_described_ones_and_unavailable_ones_stay_out() -> None:
    registry = ActSourceRegistry()
    registry.register(
        FakeActSource(),
        publishers=["ZZ_B", "ZZ_A", "ZZ_X", "ZZ_OFF"],
        info=[info("ZZ_B", "beta"), info("ZZ_A", "alfa"), info("ZZ_OFF", "off", available=False)],
    )

    with pytest.raises(UnknownPublisherError) as excinfo:
        registry.for_publisher("xyz")

    assert "Poprawne: DU, MP, ZZ_A (alfa), ZZ_B (beta), ZZ_X." in str(excinfo.value)
    assert "ZZ_OFF" not in str(excinfo.value)


def test_a_later_registration_cannot_shadow_an_earlier_name_with_a_code() -> None:
    registry = ActSourceRegistry()
    first = FakeActSource()
    registry.register(first, publishers=["AA_1"], info=[info("AA_1", "pomorskie")])

    with pytest.raises(ActSourceConflictError, match="POMORSKIE"):
        registry.register(FakeActSource(), publishers=["BB_1", "POMORSKIE"])

    assert registry.publishers() == ("AA_1",), "nothing of the refused registration was kept"
    assert registry.for_publisher("pomorskie") is first


@pytest.mark.parametrize("padded", [" DU", "mp ", " Du\t", "\nMP\n"])
def test_whitespace_around_the_codes_the_core_serves_is_not_a_reason_to_refuse(padded: str) -> None:
    registry, _ = registry_with(info("ZZ_PM", "pomorskie"))

    assert registry.for_publisher(padded) is None, "left to the Sejm unchanged, as before the module"


@pytest.mark.parametrize(
    ("typed", "canonical"),
    [(" du", "DU"), ("Mp ", "MP"), ("\tDU\n", "DU"), ("mp", "MP"), ("DU", "DU")],
)
def test_the_canonical_publisher_of_du_and_mp_typed_loosely_is_du_and_mp(typed: str, canonical: str) -> None:
    registry, _ = registry_with(info("ZZ_PM", "pomorskie"))

    assert registry.canonical_publisher(typed) == canonical


@pytest.mark.parametrize("value", ["xyz", "pomorskie", " ZZ_PM ", "DUMP", "", "D U"])
def test_every_other_value_is_left_exactly_as_typed(value: str) -> None:
    registry, _ = registry_with(info("ZZ_PM", "pomorskie"))

    assert registry.canonical_publisher(value) == value


@pytest.mark.parametrize("typed", [" du", "Mp ", "DU", "xyz"])
def test_with_an_empty_registry_nothing_is_normalised(typed: str) -> None:
    assert ActSourceRegistry().canonical_publisher(typed) == typed


def test_an_empty_registry_returns_before_any_normalisation(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(_text: str) -> str:
        raise AssertionError("the empty-registry path must not normalise the value")

    monkeypatch.setattr(act_sources, "fold_publisher_name", forbidden)

    assert ActSourceRegistry().for_publisher("anything") is None


def test_a_value_too_long_to_be_a_name_is_not_normalised(monkeypatch: pytest.MonkeyPatch) -> None:
    registry, _ = registry_with(info("ZZ_PM", "pomorskie"))
    monkeypatch.setattr(act_sources, "fold_publisher_name", lambda _text: pytest.fail("a long value was folded"))

    with pytest.raises(UnknownPublisherError):
        registry.for_publisher("x" * (MAX_PUBLISHER_NAME_LENGTH + 1))


def test_a_name_longer_than_the_limit_cannot_be_registered() -> None:
    with pytest.raises(ActSourceConflictError, match="znaków"):
        ActSourceRegistry().register(
            FakeActSource(), publishers=["ZZ_1"], info=[info("ZZ_1", "n" * (MAX_PUBLISHER_NAME_LENGTH + 1))]
        )


def test_control_and_zero_width_characters_are_not_echoed() -> None:
    message = unknown_publisher_message("a\x1b[31m​b‮c\x00", [info("ZZ_1", "alfa")])

    assert message.startswith("Nieznany wydawca: a[31mbc.")
    assert not any(not char.isprintable() for char in message)


def test_the_refusal_is_a_validation_error_for_the_tool_layer() -> None:
    assert issubclass(UnknownPublisherError, ValueError)
    assert _classify_error(UnknownPublisherError("x")) == "validation"


def test_the_message_offers_only_available_publishers_with_a_name_hint_first() -> None:
    message = unknown_publisher_message(
        "pomorskei", [info("ZZ_B", "beta"), info("ZZ_A", "alfa"), info("ZZ_X", "off", available=False)]
    )

    assert message.startswith("Nieznany wydawca: pomorskei.")
    hint = "Zamiast kodu można podać nazwę w parametrze publisher (nie w ELI), np. alfa"
    assert message.index(hint) < message.index("Poprawne:")
    assert "ZZ_A (alfa), ZZ_B (beta)" in message
    assert "ZZ_X" not in message
    assert "off" not in message


def test_a_long_value_is_capped_and_whitespace_in_it_is_collapsed() -> None:
    long_value = "x" * 100
    capped = unknown_publisher_message(long_value, served_infos())
    multiline = unknown_publisher_message("a\n\nb\tc", served_infos())

    assert "x" * UNKNOWN_PUBLISHER_VALUE_LIMIT + "…" in capped
    assert "x" * (UNKNOWN_PUBLISHER_VALUE_LIMIT + 1) not in capped
    assert "Nieznany wydawca: a b c." in multiline


@pytest.mark.parametrize("value", ["xyz", "x" * UNKNOWN_PUBLISHER_VALUE_LIMIT, "x" * 500])
def test_the_full_listing_fits_the_tool_error_budget_for_any_input(value: str) -> None:
    from law_scrapper_mcp.config import settings

    message = unknown_publisher_message(value, served_infos())

    assert len(message) <= settings.error_message_max_chars

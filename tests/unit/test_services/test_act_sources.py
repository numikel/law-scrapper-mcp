"""Registry rules for pluggable act sources."""

from __future__ import annotations

import pytest
from fake_act_source import FakeActSource

from law_scrapper_mcp.services.act_sources import ActSourceConflictError, ActSourceRegistry


def test_an_empty_registry_serves_nothing() -> None:
    registry = ActSourceRegistry()

    assert registry.for_publisher("DU") is None
    assert registry.for_publisher("POL_WOJ_PM") is None
    assert registry.publishers() == ()


def test_exact_code_and_prefix_both_resolve() -> None:
    registry = ActSourceRegistry()
    source = FakeActSource()
    registry.register(source, publishers=["POL_WOJ_PM", "POL_WOJ_DS"], prefixes=["POL_WOJ_"])

    assert registry.for_publisher("POL_WOJ_PM") is source
    assert registry.for_publisher("POL_WOJ_XX") is source, "an unserved code under the prefix still reaches its source"
    assert registry.for_publisher("DU") is None
    assert registry.publishers() == ("POL_WOJ_DS", "POL_WOJ_PM")


def test_exact_code_wins_over_a_prefix_and_the_longest_prefix_wins() -> None:
    registry = ActSourceRegistry()
    broad, narrow, exact = FakeActSource(), FakeActSource(), FakeActSource()
    registry.register(broad, publishers=[], prefixes=["ZZ_"])
    registry.register(narrow, publishers=[], prefixes=["ZZ_AB_"])
    registry.register(exact, publishers=["ZZ_AB_1"])

    assert registry.for_publisher("ZZ_CD") is broad
    assert registry.for_publisher("ZZ_AB_2") is narrow
    assert registry.for_publisher("ZZ_AB_1") is exact


@pytest.mark.parametrize("code", ["DU", "MP"])
def test_the_codes_the_core_serves_cannot_be_claimed(code: str) -> None:
    registry = ActSourceRegistry()

    with pytest.raises(ActSourceConflictError, match=code):
        registry.register(FakeActSource(), publishers=[code])

    assert registry.for_publisher(code) is None


@pytest.mark.parametrize("prefix", ["D", "M", "DU", "MP"])
def test_a_prefix_that_covers_a_core_code_is_refused(prefix: str) -> None:
    registry = ActSourceRegistry()

    with pytest.raises(ActSourceConflictError):
        registry.register(FakeActSource(), publishers=[], prefixes=[prefix])


def test_an_empty_prefix_is_refused() -> None:
    with pytest.raises(ActSourceConflictError):
        ActSourceRegistry().register(FakeActSource(), publishers=[], prefixes=[""])


def test_a_code_or_prefix_already_claimed_is_refused_and_nothing_is_half_registered() -> None:
    registry = ActSourceRegistry()
    first = FakeActSource()
    registry.register(first, publishers=["ZZ_1"], prefixes=["ZZ_"])

    with pytest.raises(ActSourceConflictError, match="ZZ_1"):
        registry.register(FakeActSource(), publishers=["ZZ_NEW", "ZZ_1"])
    with pytest.raises(ActSourceConflictError, match="ZZ_"):
        registry.register(FakeActSource(), publishers=["ZZ_OTHER"], prefixes=["ZZ_"])

    assert registry.for_publisher("ZZ_NEW") is first, "falls under the first source's prefix, not a half registration"
    assert registry.publishers() == ("ZZ_1",)


def test_unregister_releases_codes_and_prefixes_of_that_source_only() -> None:
    registry = ActSourceRegistry()
    first, second = FakeActSource(), FakeActSource()
    registry.register(first, publishers=["AA_1"], prefixes=["AA_"])
    registry.register(second, publishers=["BB_1"])

    registry.unregister(first)

    assert registry.for_publisher("AA_1") is None
    assert registry.for_publisher("AA_2") is None
    assert registry.for_publisher("BB_1") is second
    registry.register(FakeActSource(), publishers=["AA_1"], prefixes=["AA_"])


def test_unregistering_an_unknown_source_is_harmless() -> None:
    ActSourceRegistry().unregister(FakeActSource())

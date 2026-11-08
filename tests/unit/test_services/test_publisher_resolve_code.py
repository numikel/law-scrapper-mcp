"""`ActSourceRegistry.resolve_code`: the publisher code a typed value stands for, used to key coverage lookups."""

from __future__ import annotations

import pytest

from fake_act_source import FakeActSource
from law_scrapper_mcp.services.act_sources import MAX_PUBLISHER_NAME_LENGTH, ActSourceRegistry, PublisherInfo

PM = PublisherInfo(code="ZZ_PM", label="pomorskie", available=True, item={"code": "ZZ_PM"})


def with_pomorskie() -> ActSourceRegistry:
    registry = ActSourceRegistry()
    registry.register(FakeActSource(), publishers=["ZZ_PM"], prefixes=["ZZ_"], info=[PM])
    return registry


def test_a_registered_code_is_returned_as_is() -> None:
    assert with_pomorskie().resolve_code("ZZ_PM") == "ZZ_PM"


@pytest.mark.parametrize("typed", ["pomorskie", "Pomorskie", "POMORSKIE", " pomorskie "])
def test_a_registered_name_in_any_letter_case_resolves_to_its_code(typed: str) -> None:
    assert with_pomorskie().resolve_code(typed) == "ZZ_PM"


@pytest.mark.parametrize("registry_kind", ["empty", "with-source"])
@pytest.mark.parametrize(("typed", "code"), [("du", "DU"), (" Mp ", "MP"), ("DU", "DU")])
def test_du_and_mp_in_any_letter_case_resolve_with_or_without_a_source(
    registry_kind: str, typed: str, code: str
) -> None:
    registry = ActSourceRegistry() if registry_kind == "empty" else with_pomorskie()

    assert registry.resolve_code(typed) == code


@pytest.mark.parametrize("typed", ["xyz", " xyz ", "ZZ_UNKNOWN"])
def test_an_unknown_value_or_a_mere_prefix_match_comes_back_unchanged(typed: str) -> None:
    assert with_pomorskie().resolve_code(typed) == typed


def test_an_over_long_value_is_not_folded_into_a_name_and_comes_back_unchanged() -> None:
    typed = "pomorskie" + " " * MAX_PUBLISHER_NAME_LENGTH

    assert len(typed) > MAX_PUBLISHER_NAME_LENGTH
    assert with_pomorskie().resolve_code(typed) == typed

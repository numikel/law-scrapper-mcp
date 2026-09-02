"""The `law_scrapper_mcp.client` facade must re-export every public exception (#31).

Callers outside the package import errors from the facade, and a class missing
there — `ContentTooLargeError` was — forces them to reach into `client.exceptions`,
which the facade exists to make unnecessary.
"""

from __future__ import annotations

import inspect

import pytest

from law_scrapper_mcp import client as facade
from law_scrapper_mcp.client import exceptions

PUBLIC_EXCEPTIONS = sorted(
    name
    for name, obj in inspect.getmembers(exceptions, inspect.isclass)
    if obj.__module__ == exceptions.__name__ and issubclass(obj, exceptions.LawScrapperError)
)


def test_the_sweep_sees_the_exception_hierarchy() -> None:
    """Guard the guard: an empty sweep would make the test below pass for nothing."""
    assert "LawScrapperError" in PUBLIC_EXCEPTIONS
    assert len(PUBLIC_EXCEPTIONS) >= 7


@pytest.mark.parametrize("name", PUBLIC_EXCEPTIONS)
def test_every_public_exception_is_re_exported_by_the_facade(name: str) -> None:
    assert name in facade.__all__
    assert getattr(facade, name) is getattr(exceptions, name)

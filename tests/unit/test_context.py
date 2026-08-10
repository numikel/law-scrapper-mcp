"""Tests for the typed application context."""

from dataclasses import FrozenInstanceError, fields

import pytest

from law_scrapper_mcp.context import AppContext

EXPECTED_FIELDS = {
    "client",
    "cache",
    "document_store",
    "content_processor",
    "result_store",
    "metadata_service",
    "search_service",
    "act_service",
    "changes_service",
    "comparison_service",
    "relationship_service",
    "date_service",
}


def test_app_context_declares_every_lifespan_dependency() -> None:
    assert {field.name for field in fields(AppContext)} == EXPECTED_FIELDS


async def test_lifespan_returns_frozen_app_context() -> None:
    from law_scrapper_mcp.server import app, lifespan

    async with lifespan(app) as context:
        assert isinstance(context, AppContext)
        with pytest.raises(FrozenInstanceError):
            context.client = None  # type: ignore[misc]

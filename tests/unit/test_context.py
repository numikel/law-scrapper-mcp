"""Tests for the typed application context."""

from dataclasses import FrozenInstanceError, fields
from unittest.mock import Mock

import pytest

from law_scrapper_mcp.context import AppContext, get_app_context
from law_scrapper_mcp.server import app, lifespan

pytestmark = pytest.mark.asyncio

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


async def test_lifespan_yields_frozen_app_context() -> None:
    async with lifespan(app) as app_context:
        assert isinstance(app_context, AppContext)
        with pytest.raises(FrozenInstanceError):
            app_context.client = None  # type: ignore[misc, assignment]


async def test_get_app_context_reads_typed_value_from_request_context() -> None:
    async with lifespan(app) as app_context:
        ctx = Mock()
        ctx.request_context.lifespan_context = app_context
        context = get_app_context(ctx)
        assert isinstance(context, AppContext)
        assert context.search_service is not None

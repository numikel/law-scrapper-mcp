"""Tests for the typed application context."""

from dataclasses import MISSING, FrozenInstanceError, fields
from unittest.mock import Mock

import pytest

from law_scrapper_mcp.context import AppContext, get_app_context
from law_scrapper_mcp.server import app, lifespan

EXPECTED_FIELDS = {
    "client",
    "cache",
    "document_store",
    "content_processor",
    "result_store",
    "metadata_service",
    "search_service",
    "act_service",
    "content_service",
    "changes_service",
    "comparison_service",
    "relationship_service",
    "date_service",
    "egress",
    "module_state",
}


def test_app_context_declares_every_lifespan_dependency() -> None:
    assert {field.name for field in fields(AppContext)} == EXPECTED_FIELDS


def test_module_state_is_the_last_field_and_has_a_default() -> None:
    """Required fields stay in front, so the additive field cannot break a construction."""
    all_fields = fields(AppContext)
    assert all_fields[-1].name == "module_state"
    assert all_fields[-2].name == "egress"
    assert all_fields[-1].default_factory is not MISSING


@pytest.mark.asyncio
async def test_module_state_is_empty_when_no_module_is_active() -> None:
    async with lifespan(app) as app_context:
        assert dict(app_context.module_state) == {}


@pytest.mark.asyncio
async def test_lifespan_yields_frozen_app_context() -> None:
    async with lifespan(app) as app_context:
        assert isinstance(app_context, AppContext)
        with pytest.raises(FrozenInstanceError):
            app_context.client = None  # type: ignore[misc, assignment]


@pytest.mark.asyncio
async def test_get_app_context_reads_typed_value_from_request_context() -> None:
    async with lifespan(app) as app_context:
        ctx = Mock()
        ctx.request_context.lifespan_context = app_context
        context = get_app_context(ctx)
        assert isinstance(context, AppContext)
        assert context.search_service is not None

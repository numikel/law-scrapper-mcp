"""Tests for the typed application context."""

from dataclasses import FrozenInstanceError, fields

import pytest

from law_scrapper_mcp.context import APP_CONTEXT_KEY, AppContext, get_app_context, require_app_context

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


async def test_lifespan_yields_dictionary_with_frozen_app_context() -> None:
    from law_scrapper_mcp.server import app, lifespan

    async with lifespan(app) as lifespan_context:
        assert isinstance(lifespan_context, dict)
        assert set(lifespan_context.keys()) == {APP_CONTEXT_KEY}
        context = require_app_context(lifespan_context)
        assert isinstance(context, AppContext)
        with pytest.raises(FrozenInstanceError):
            context.client = None  # type: ignore[misc]


async def test_get_app_context_reads_typed_value_from_fastmcp_context() -> None:
    from unittest.mock import Mock

    from law_scrapper_mcp.server import app, lifespan

    async with lifespan(app) as lifespan_context:
        ctx = Mock()
        ctx.lifespan_context = lifespan_context
        context = get_app_context(ctx)
        assert isinstance(context, AppContext)
        assert context.search_service is not None


def test_require_app_context_rejects_missing_key() -> None:
    with pytest.raises(TypeError, match=APP_CONTEXT_KEY):
        require_app_context({})


def test_require_app_context_rejects_wrong_type() -> None:
    with pytest.raises(TypeError, match=APP_CONTEXT_KEY):
        require_app_context({APP_CONTEXT_KEY: object()})

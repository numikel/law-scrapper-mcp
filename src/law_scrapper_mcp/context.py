"""Typed application dependencies shared through the MCP lifespan."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TypedDict

from fastmcp import Context

from law_scrapper_mcp.client.cache import TTLCache
from law_scrapper_mcp.client.sejm_client import SejmApiClient
from law_scrapper_mcp.services.act_service import ActService
from law_scrapper_mcp.services.changes_service import ChangesService
from law_scrapper_mcp.services.comparison_service import ComparisonService
from law_scrapper_mcp.services.content_processor import ContentProcessor
from law_scrapper_mcp.services.date_service import DateService
from law_scrapper_mcp.services.document_store import DocumentStore
from law_scrapper_mcp.services.metadata_service import MetadataService
from law_scrapper_mcp.services.relationship_service import RelationshipService
from law_scrapper_mcp.services.result_store import ResultStore
from law_scrapper_mcp.services.search_service import SearchService

APP_CONTEXT_KEY = "app_context"


@dataclass(frozen=True, slots=True)
class AppContext:
    """Application resources created once for the server lifespan."""

    client: SejmApiClient
    cache: TTLCache
    document_store: DocumentStore
    content_processor: ContentProcessor
    result_store: ResultStore
    metadata_service: MetadataService
    search_service: SearchService
    act_service: ActService
    changes_service: ChangesService
    comparison_service: ComparisonService
    relationship_service: RelationshipService
    date_service: DateService


class LifespanState(TypedDict):
    """FastMCP lifespan dictionary contract for this server."""

    app_context: AppContext


def require_app_context(lifespan_context: Mapping[str, object]) -> AppContext:
    """Return the typed application context from a lifespan dictionary."""
    raw = lifespan_context.get(APP_CONTEXT_KEY)
    if not isinstance(raw, AppContext):
        raise TypeError(
            f"Lifespan context must contain typed {APP_CONTEXT_KEY!r}, "
            f"got {type(raw).__name__!r}"
        )
    return raw


def get_app_context(ctx: Context) -> AppContext:
    """Return the typed application context from a FastMCP tool context."""
    return require_app_context(ctx.lifespan_context)

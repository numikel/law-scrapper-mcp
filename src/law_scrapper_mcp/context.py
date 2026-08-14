"""Typed application dependencies shared through the MCP lifespan."""

from dataclasses import dataclass

from mcp.server.mcpserver import Context

from law_scrapper_mcp.client.cache import TTLCache
from law_scrapper_mcp.client.sejm_client import SejmApiClient
from law_scrapper_mcp.services.act_service import ActService
from law_scrapper_mcp.services.changes_service import ChangesService
from law_scrapper_mcp.services.comparison_service import ComparisonService
from law_scrapper_mcp.services.content_processor import ContentProcessor
from law_scrapper_mcp.services.content_service import ContentService
from law_scrapper_mcp.services.date_service import DateService
from law_scrapper_mcp.services.document_store import DocumentStore
from law_scrapper_mcp.services.metadata_service import MetadataService
from law_scrapper_mcp.services.relationship_service import RelationshipService
from law_scrapper_mcp.services.result_store import ResultStore
from law_scrapper_mcp.services.search_service import SearchService


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
    content_service: ContentService
    changes_service: ChangesService
    comparison_service: ComparisonService
    relationship_service: RelationshipService
    date_service: DateService


def get_app_context(ctx: Context[AppContext]) -> AppContext:
    """Return the typed application context from an MCP tool context."""
    return ctx.request_context.lifespan_context

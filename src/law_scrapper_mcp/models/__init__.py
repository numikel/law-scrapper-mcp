"""Data models for Law Scrapper MCP."""

from law_scrapper_mcp.models.api_responses import (
    ActDetail,
    ActReference,
    ActSummary,
    PublisherInfo,
    SearchApiResponse,
    StructureNode,
)
from law_scrapper_mcp.models.enums import (
    ContentFormat,
    DetailLevel,
    MetadataCategory,
    Publisher,
    RelationshipType,
)
from law_scrapper_mcp.models.tool_inputs import (
    ActDetailsRequest,
    BrowseRequest,
    DateCalculationRequest,
    MetadataRequest,
    ReadContentRequest,
    RelationshipsRequest,
    SearchInActRequest,
    SearchRequest,
    TrackChangesRequest,
    parse_eli,
)
from law_scrapper_mcp.models.tool_outputs import (
    ActDetailOutput,
    ActSummaryOutput,
    ChangesOutput,
    ContentOutput,
    DateOutput,
    EnrichedResponse,
    Hint,
    MetadataOutput,
    RelationshipsOutput,
    SearchInActOutput,
    SearchOutput,
)

__all__ = [
    # API responses
    "ActDetail",
    "ActReference",
    "ActSummary",
    "PublisherInfo",
    "SearchApiResponse",
    "StructureNode",
    # Enums
    "ContentFormat",
    "DetailLevel",
    "MetadataCategory",
    "Publisher",
    "RelationshipType",
    # Tool inputs
    "ActDetailsRequest",
    "BrowseRequest",
    "DateCalculationRequest",
    "MetadataRequest",
    "ReadContentRequest",
    "RelationshipsRequest",
    "SearchInActRequest",
    "SearchRequest",
    "TrackChangesRequest",
    "parse_eli",
    # Tool outputs
    "ActDetailOutput",
    "ActSummaryOutput",
    "ChangesOutput",
    "ContentOutput",
    "DateOutput",
    "EnrichedResponse",
    "Hint",
    "MetadataOutput",
    "RelationshipsOutput",
    "SearchInActOutput",
    "SearchOutput",
]

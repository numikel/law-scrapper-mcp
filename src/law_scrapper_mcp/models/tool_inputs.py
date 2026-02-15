"""Pydantic models for tool input parameters."""

from __future__ import annotations

from pydantic import BaseModel, Field

from law_scrapper_mcp.models.enums import (
    DetailLevel,
    MetadataCategory,
    Publisher,
    RelationshipType,
)


class SearchRequest(BaseModel):
    """Parameters for searching legal acts."""

    publisher: Publisher = Field(
        default=Publisher.DU, description="Publisher code (DU or MP)"
    )
    year: int | None = Field(default=None, description="Year of publication")
    keywords: list[str] | None = Field(
        default=None,
        description="Keywords to search for (AND logic - all must be present)",
    )
    date_from: str | None = Field(
        default=None, description="Start date for date range filter (YYYY-MM-DD)"
    )
    date_to: str | None = Field(
        default=None, description="End date for date range filter (YYYY-MM-DD)"
    )
    title: str | None = Field(default=None, description="Title search phrase")
    act_type: str | None = Field(default=None, description="Type of legal act")
    pub_date_from: str | None = Field(
        default=None, description="Publication date from (YYYY-MM-DD)"
    )
    pub_date_to: str | None = Field(
        default=None, description="Publication date to (YYYY-MM-DD)"
    )
    in_force: bool | None = Field(
        default=None, description="Filter by acts currently in force"
    )
    limit: int | None = Field(default=None, description="Maximum number of results")
    offset: int | None = Field(default=None, description="Number of results to skip")
    detail_level: DetailLevel = Field(
        default=DetailLevel.STANDARD, description="Level of detail in results"
    )


class BrowseRequest(BaseModel):
    """Parameters for browsing acts by publisher and year."""

    publisher: Publisher = Field(description="Publisher code (DU or MP)")
    year: int = Field(description="Year to browse")
    detail_level: DetailLevel = Field(
        default=DetailLevel.STANDARD, description="Level of detail in results"
    )


class ActDetailsRequest(BaseModel):
    """Parameters for retrieving act details."""

    eli: str = Field(
        description="ELI identifier of the act (e.g., 'DU/2024/1' or full URL)"
    )
    load_content: bool = Field(
        default=False,
        description="Whether to load full content (HTML/PDF) into document store",
    )


class ReadContentRequest(BaseModel):
    """Parameters for reading act content."""

    eli: str = Field(description="ELI identifier of the act")
    section: str | None = Field(
        default=None,
        description="Section ID to read (if None, returns table of contents)",
    )


class SearchInActRequest(BaseModel):
    """Parameters for searching within an act."""

    eli: str = Field(description="ELI identifier of the act")
    query: str = Field(description="Search query text")
    context_chars: int = Field(
        default=500, description="Number of characters of context around matches"
    )


class MetadataRequest(BaseModel):
    """Parameters for retrieving metadata."""

    category: MetadataCategory = Field(
        default=MetadataCategory.ALL, description="Category of metadata to retrieve"
    )


class RelationshipsRequest(BaseModel):
    """Parameters for retrieving act relationships."""

    eli: str = Field(description="ELI identifier of the act")
    relationship_type: RelationshipType | None = Field(
        default=None, description="Specific relationship type to retrieve"
    )


class TrackChangesRequest(BaseModel):
    """Parameters for tracking changes to legal acts."""

    publisher: Publisher = Field(
        default=Publisher.DU, description="Publisher code (DU or MP)"
    )
    date_from: str = Field(description="Start date for tracking (YYYY-MM-DD)")
    date_to: str | None = Field(
        default=None, description="End date for tracking (YYYY-MM-DD, default: today)"
    )
    keywords: list[str] | None = Field(
        default=None, description="Filter by keywords"
    )


class DateCalculationRequest(BaseModel):
    """Parameters for date calculation."""

    days: int = Field(default=0, description="Number of days to add (positive=future, negative=past)")
    months: int = Field(default=0, description="Number of months to add (positive=future, negative=past)")
    years: int = Field(default=0, description="Number of years to add (positive=future, negative=past)")
    base_date: str | None = Field(
        default=None, description="Base date for calculation (YYYY-MM-DD, default: today)"
    )


def parse_eli(eli: str) -> tuple[str, int, int]:
    """Parse ELI string into publisher, year, and position.

    Args:
        eli: ELI identifier (e.g., "DU/2024/1" or full URL)

    Returns:
        Tuple of (publisher, year, pos)

    Raises:
        ValueError: If ELI format is invalid
    """
    # Strip URL prefix if present
    if "api.sejm.gov.pl/eli/" in eli:
        eli = eli.split("api.sejm.gov.pl/eli/")[1]
    elif eli.startswith("http"):
        raise ValueError(f"Invalid ELI URL format: {eli}")

    # Remove trailing slashes and split
    eli = eli.rstrip("/")
    parts = eli.split("/")

    if len(parts) != 3:
        raise ValueError(
            f"Invalid ELI format: {eli}. Expected format: PUBLISHER/YEAR/POS"
        )

    publisher, year_str, pos_str = parts

    try:
        year = int(year_str)
        pos = int(pos_str)
    except ValueError as e:
        raise ValueError(f"Invalid year or position in ELI: {eli}") from e

    return publisher, year, pos

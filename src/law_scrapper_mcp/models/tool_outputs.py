"""Pydantic models for tool outputs."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

from law_scrapper_mcp.models.pagination import PageInfo

T = TypeVar("T")


class Hint(BaseModel):
    """Hint for next actions or related tools."""

    message: str = Field(description="Hint message")
    tool: str | None = Field(default=None, description="Related tool name")
    parameters: dict[str, Any] | None = Field(default=None, description="Suggested parameters")


class EnrichedResponse(BaseModel, Generic[T]):
    """Wrapper for tool responses with hints and metadata."""

    data: T = Field(description="The actual response data")
    hints: list[Hint] = Field(default_factory=list, description="Suggested next steps")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional metadata")


class ActSummaryOutput(BaseModel):
    """Formatted output for act summary."""

    eli: str
    publisher: str
    year: int
    pos: int
    title: str
    status: str
    type: str | None = None
    promulgation_date: str | None = None
    effective_date: str | None = Field(default=None, description="Data wejścia w życie (entryIntoForce).")
    in_force: str | None = None


class SetScope(StrEnum):
    """Whether a stored result set is the whole answer or a window into it."""

    COMPLETE = "complete"
    PAGE = "page"


class ResultSetScope(BaseModel):
    """Zasięg zestawu wyników względem korpusu, z którego pochodzi."""

    scope: SetScope = Field(
        description=(
            "'complete' — zestaw zawiera każdy rekord pasujący do zapytania w chwili wywołania. "
            "'page' — zestaw jest oknem wyciętym z większego korpusu, więc brak dopasowania "
            "w filter_results NIE dowodzi, że akt nie istnieje w zbiorze."
        )
    )
    stored_count: int = Field(description="Liczba rekordów w tym zestawie.")
    window_offset: int = Field(description="Pozycja początku okna w korpusie. Zero dla zestawu kompletnego.")
    corpus_count: int | None = Field(
        default=None,
        description=(
            "Rozmiar korpusu, z którego pochodzi zestaw. None, gdy nieznany — tak jest dla "
            "zestawu powstałego z filtrowania okna: wiadomo, ile rekordów trafiło w oknie, "
            "nie wiadomo, ile rekordów korpusu pasowałoby do tych samych kryteriów."
        ),
    )


class SearchOutput(BaseModel):
    """Output for search results."""

    results: list[ActSummaryOutput]
    total_count: int
    query_summary: str
    returned_count: int
    result_set_id: str | None = None
    result_set_scope: ResultSetScope | None = Field(
        default=None,
        description=(
            "Zasięg zapisanego zestawu wyników. None, gdy zestaw nie powstał (zero wyników). "
            "Wartość 'page' oznacza, że filter_results zawęża okno, a nie cały zbiór."
        ),
    )
    page_info: PageInfo


class ActDetailOutput(BaseModel):
    """Output for detailed act information."""

    eli: str
    publisher: str
    year: int
    pos: int
    title: str
    status: str
    type: str | None = None
    announcement_date: str | None = None
    promulgation_date: str | None = None
    entry_into_force: str | None = None
    valid_from: str | None = None
    repeal_date: str | None = None
    change_date: str | None = None
    keywords: list[str] = []
    references: dict[str, Any] | None = None
    volume: int | None = None
    has_pdf: bool = False
    has_html: bool = False
    toc: list[dict[str, Any]] = []
    is_loaded: bool = False


class ContentOutput(BaseModel):
    """Output for act content."""

    eli: str
    section_id: str | None = None
    section_title: str
    content: str
    toc: list[dict[str, Any]] = []
    page_info: PageInfo


class SearchInActOutput(BaseModel):
    """Output for search within act."""

    eli: str
    query: str
    matches: list[dict[str, Any]]
    total_matches: int
    page_info: PageInfo
    context_chars_requested: int = 500
    context_chars_applied: int = 500


class MetadataOutput(BaseModel):
    """Output for metadata."""

    category: str
    metadata: dict[str, Any]
    count: int
    page_info: PageInfo
    failed_categories: list[str] = []


class RelationshipsOutput(BaseModel):
    """Output for act relationships."""

    eli: str
    relationship_type: str | None = None
    relationships: dict[str, Any]
    total_count: int


class ChangesOutput(BaseModel):
    """Output for tracked changes."""

    date_range: str
    publisher: str
    keywords: list[str]
    changes: list[ActSummaryOutput]
    total_count: int
    result_set_id: str | None = None
    result_set_scope: ResultSetScope | None = Field(
        default=None,
        description=(
            "Zasięg zapisanego zestawu zmian: 'complete', gdy pierwsza strona mieści cały "
            "zakres dat, albo 'page', gdy zakres jest szerszy niż jedna strona — wtedy "
            "filter_results przeszukuje tylko to okno."
        ),
    )
    page_info: PageInfo


class FilterOutput(BaseModel):
    """Output for filtered results."""

    source_result_set_id: str
    result_set_id: str | None = None
    results: list[ActSummaryOutput]
    original_count: int
    filtered_count: int
    filters_applied: dict[str, Any] = {}
    source_scope: ResultSetScope = Field(
        description="Zasięg zestawu, który filtrowano — decyduje, czego dowodzi pusty wynik."
    )
    result_set_scope: ResultSetScope | None = Field(
        default=None,
        description="Zasięg zestawu powstałego z filtrowania. None, gdy nic nie dopasowano.",
    )
    no_match_is_inconclusive: bool = Field(
        default=False,
        description=(
            "True, gdy filtrowano okno i nic nie dopasowano. Wynik NIE rozstrzyga, "
            "czy akt istnieje w zbiorze — przeszukano tylko okno. Poszerz zestaw "
            "albo zawęź kryteria wyszukiwania przed wyciągnięciem wniosku."
        ),
    )
    page_info: PageInfo


class DateOutput(BaseModel):
    """Output for date calculation."""

    base_date: str
    calculated_date: str
    days_offset: int
    months_offset: int
    years_offset: int
    description: str


class ResultSetInfo(BaseModel):
    """Info about a single stored result set."""

    result_set_id: str
    query_summary: str
    result_count: int
    total_count: int
    scope: SetScope = Field(
        description="Zasięg zestawu: 'complete' (cały zbiór) albo 'page' (okno z większego zbioru)."
    )
    created_at: str


class ResultSetListOutput(BaseModel):
    """Output for listing result sets."""

    sets: list[ResultSetInfo]
    count: int
    page_info: PageInfo


class LoadedDocumentInfo(BaseModel):
    """Info about a single loaded document."""

    eli: str
    size_bytes: int
    section_count: int
    loaded_at: str
    last_accessed: str


class LoadedDocumentListOutput(BaseModel):
    """Output for listing loaded documents."""

    documents: list[LoadedDocumentInfo]
    count: int
    page_info: PageInfo


class CompareOutput(BaseModel):
    """Output for comparing two acts."""

    eli_a: str
    eli_b: str
    comparison: dict[str, Any]
    common_keywords: list[str]
    differences: list[str]

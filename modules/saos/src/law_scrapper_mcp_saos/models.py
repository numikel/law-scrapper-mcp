"""Models of the SAOS API (only what this module reads) and of the module's tool output.

The API models ignore every key they do not name. In particular `judges[]` (names of people) and the
full set of search-item keys are never parsed, so they cannot leak into an output by accident.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from law_scrapper_mcp.models.pagination import PageInfo
from law_scrapper_mcp.models.tool_outputs import JudgmentSummaryOutput, ResultSetScope


class SaosModel(BaseModel):
    """Base of the API models: unknown keys are dropped, not rejected."""

    model_config = ConfigDict(extra="ignore")


class SaosCourt(SaosModel):
    """The court of a common-court division."""

    name: str | None = None


class SaosChamber(SaosModel):
    """A chamber of the Supreme Court."""

    name: str | None = None


class SaosDivision(SaosModel):
    """A division. Common courts give `code` and `court`, the Supreme Court gives `chambers`."""

    name: str | None = None
    code: str | None = None
    court: SaosCourt | None = None
    chambers: list[SaosChamber] = Field(default_factory=list)


class SaosCourtCase(SaosModel):
    """One case number of a judgment."""

    case_number: str | None = Field(default=None, alias="caseNumber")


class SaosSearchItem(SaosModel):
    """One search result: a judgment record with an excerpt of at most 800 characters."""

    id: int
    court_type: str = Field(default="", alias="courtType")
    court_cases: list[SaosCourtCase] = Field(default_factory=list, alias="courtCases")
    judgment_type: str = Field(default="", alias="judgmentType")
    text_content: str | None = Field(default=None, alias="textContent")
    division: SaosDivision | None = None
    judgment_date: str | None = Field(default=None, alias="judgmentDate")


class SaosPageInfo(SaosModel):
    """The `info` block of a search page."""

    total_results: int = Field(alias="totalResults")


class SaosSearchPage(SaosModel):
    """One page of `search/judgments`.

    `items` and `info.totalResults` are required: every recorded page has them, and a body without them
    must fail loudly instead of reading as an empty result.
    """

    items: list[SaosSearchItem]
    info: SaosPageInfo


class SearchSaosJudgmentsOutput(BaseModel):
    """Output of `search_saos_judgments`."""

    source: str = Field(description="Źródło orzeczeń: 'saos'.")
    results: list[JudgmentSummaryOutput]
    total_count: int = Field(description="Liczba wszystkich orzeczeń pasujących do zapytania w SAOS.")
    returned_count: int
    query_summary: str
    result_set_id: str | None = Field(
        default=None,
        description="Identyfikator zapisanego zestawu; użyj go w filter_results. Brak, gdy nie ma wyników.",
    )
    result_set_scope: ResultSetScope | None = Field(
        default=None,
        description=(
            "Zasięg zapisanego zestawu. Wartość 'page' oznacza, że filter_results zawęża okno wyników, "
            "a nie wszystkie orzeczenia pasujące do zapytania."
        ),
    )
    page_info: PageInfo

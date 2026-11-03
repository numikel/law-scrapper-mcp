"""Models of the SAOS API (only what this module reads) and of the module's tool output.

The API models ignore every key they do not name. In particular `judges[]` (names of people) and the
text and reporters of a detail are never parsed, so they cannot leak into an output by accident.
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


class SaosRecord(SaosModel):
    """The keys a search item and a judgment detail have in common."""

    id: int
    court_type: str = Field(default="", alias="courtType")
    court_cases: list[SaosCourtCase] = Field(default_factory=list, alias="courtCases")
    judgment_type: str = Field(default="", alias="judgmentType")
    division: SaosDivision | None = None
    judgment_date: str | None = Field(default=None, alias="judgmentDate")


class SaosSearchItem(SaosRecord):
    """One search result: a judgment record with an excerpt of at most 800 characters."""

    text_content: str | None = Field(default=None, alias="textContent")


class SaosSource(SaosModel):
    """Where the court published the judgment; `judgmentUrl` is the court portal address."""

    judgment_url: str | None = Field(default=None, alias="judgmentUrl")


class SaosDetailDivision(SaosDivision):
    """A division as the detail gives it: with its type (`Pracy i Ubezpieczeń Społecznych`)."""

    type: str | None = None


class SaosDetailData(SaosRecord):
    """The `data` block of a judgment detail. The text, the judges and the reporters are never parsed."""

    division: SaosDetailDivision | None = None  # type: ignore[assignment]
    source: SaosSource | None = None


class SaosDetail(SaosModel):
    """A judgment detail (`judgments/{id}`)."""

    data: SaosDetailData


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


class PortalLinks(BaseModel):
    """What `include_portal_links` did: how many detail requests were made and how they ended."""

    requested: int = Field(description="Liczba wyników, dla których pobrano szczegóły (pierwsze wyniki strony).")
    resolved: int = Field(description="Liczba wyników, które dostały odnośnik do portalu sądu.")
    without_link: int = Field(description="Liczba wyników, których szczegóły nie zawierają poprawnego odnośnika.")
    failed: int = Field(
        description="Liczba wyników bez odnośnika z powodu błędu żądania, także pominiętych po awarii SAOS."
    )
    stopped_after_outage: bool = Field(
        description="True, gdy po awarii SAOS przerwano dalsze żądania szczegółów (wyszukiwanie zostało zwrócone)."
    )
    skipped_for_budget: int = Field(
        description=(
            "Liczba wyników strony bez próby pobrania odnośnika (limit 5 pobrań na wywołanie, 4 przy offset "
            "niewyrównanym do limit). Ich odnośniki pobierze get_saos_judgment_details."
        )
    )


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
    portal_links: PortalLinks | None = Field(
        default=None, description="Wynik include_portal_links; brak, gdy parametr nie był użyty."
    )


class SaosJudgmentDetailsOutput(BaseModel):
    """Output of `get_saos_judgment_details`."""

    source: str = Field(description="Źródło orzeczeń: 'saos'.")
    judgment: JudgmentSummaryOutput = Field(
        description="Rekord orzeczenia z odnośnikiem do portalu sądu w polu portal_url (bez fragmentu tekstu)."
    )
    division_type: str | None = Field(
        default=None,
        description="Rodzaj wydziału (np. 'Pracy i Ubezpieczeń Społecznych'); brak, gdy SAOS go nie podaje.",
    )

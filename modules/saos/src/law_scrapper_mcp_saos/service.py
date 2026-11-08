"""Search service of the SAOS module: validation, SAOS paging, result storage and hints.

Everything an agent reads in a response is built here, so the tool stays a thin adapter.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date
from typing import Any

from law_scrapper_mcp.client.exceptions import (
    ApiUnavailableError,
    EgressRedirectRefusedError,
    ResponseTooLargeError,
    SejmApiError,
)
from law_scrapper_mcp.config import settings
from law_scrapper_mcp.context import AppContext
from law_scrapper_mcp.models.pagination import DEFAULT_ITEM_LIMIT, MAX_ITEM_LIMIT
from law_scrapper_mcp.models.tool_outputs import (
    DocumentKind,
    EnrichedResponse,
    Hint,
    JudgmentSummaryOutput,
    ResultSetScope,
    SetScope,
)
from law_scrapper_mcp.services.coverage import FALLBACK_NOTE, CoverageRegistry
from law_scrapper_mcp.services.document_store import DocumentStore
from law_scrapper_mcp.services.pagination import item_page_info, parse_non_negative
from law_scrapper_mcp.services.result_store import ResultStore
from law_scrapper_mcp_saos.client import SaosClient
from law_scrapper_mcp_saos.coverage import ADMINISTRATIVE, ADMINISTRATIVE_MESSAGE, COVERAGE_KEY, court_gap_message
from law_scrapper_mcp_saos.mapping import (
    COURT_TYPES,
    JUDGMENT_TYPE_LABELS,
    LABOUR_CHAMBER_PATTERN,
    LABOUR_DIVISION_PATTERN,
    NO_SIGNATURE,
    SOURCE,
    LabourRule,
    judgment_type_label,
    labour_rule_for,
    saos_judgment_type,
    to_detail,
    to_judgment,
)
from law_scrapper_mcp_saos.models import (
    LabourScan,
    PortalLinks,
    ReasoningStatus,
    SaosDivision,
    SaosJudgmentDetailsOutput,
    SaosLoadedJudgment,
    SaosSearchItem,
    SaosTextData,
    SearchSaosJudgmentsOutput,
)
from law_scrapper_mcp_saos.reading import (
    has_reasoning_heading,
    index_sections,
    to_markdown_async,
    with_no_reasoning_note,
    with_reasons,
)
from law_scrapper_mcp_saos.state import saos_state

logger = logging.getLogger(__name__)

TOOL_SEARCH = "search_saos_judgments"
TOOL_DETAILS = "get_saos_judgment_details"
# One search call may send at most this many requests to SAOS, the search itself included: the search
# takes one (two when the offset is not a multiple of the limit) and the portal links take the rest, five
# at most.
REQUEST_BUDGET = 6
PORTAL_LINK_CAP = 5
# SAOS saying "no" (not authorised, forbidden, too many requests, unavailable for legal reasons). The egress
# client keeps these as a plain `SejmApiError` that carries the status; only 5xx become `ApiUnavailableError`.
# After one of them no further request goes out in the same call. (A 429 has already been retried once by
# the egress client, after the pause the host asked for, by the time it reaches this module.)
#
# Not the core's `_is_refusal` (`tools/error_handling.py`), on purpose: that one is private, picks the wording
# of the message an agent reads, knows 401, 403 and 451 only and ignores an error without a named source. This
# one decides whether to keep asking, so it also stops on 429 (too many requests).
REFUSAL_STATUSES = frozenset({401, 403, 429, 451})
_SOURCE_ID = re.compile(r"[0-9]{1,12}")
PHRASE_MAX_LENGTH = 500
SIGNATURE_MAX_LENGTH = 100
# `labour_only` reads whole 100-record pages and gives up after this many of them in one call.
SCAN_PAGE_SIZE = 100
SCAN_MAX_PAGES = 5
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def current_date() -> date:
    """Today's date; looked up at call time so tests can replace it."""
    return date.today()


def is_refusal(error: Exception) -> bool:
    """Whether SAOS refused the request (401, 403, 429 or 451), as opposed to failing or not having the record."""
    return isinstance(error, SejmApiError) and error.status_code in REFUSAL_STATUSES


def _text(value: str | None, *, name: str, max_length: int) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    if len(stripped) > max_length:
        raise ValueError(f"Parametr '{name}' może mieć co najwyżej {max_length} znaków.")
    return stripped


def _iso_date(value: str | None, *, name: str) -> date | None:
    if value is None or not value.strip():
        return None
    text = value.strip()
    if not _ISO_DATE.fullmatch(text):
        raise ValueError(f"Parametr '{name}' musi mieć postać RRRR-MM-DD (np. 2024-01-31).")
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ValueError(f"Parametr '{name}' nie jest poprawną datą: {text}.") from None


def _court_type(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    folded = value.strip().upper()
    if folded not in COURT_TYPES:
        raise ValueError(f"Parametr 'court_type' ma wartość '{value}'. Dozwolone: {', '.join(COURT_TYPES)}.")
    return folded


def _judgment_type(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    saos_name = saos_judgment_type(value)
    if saos_name is None:
        allowed = ", ".join(f"{label} ({name})" for name, label in JUDGMENT_TYPE_LABELS.items())
        raise ValueError(f"Parametr 'judgment_type' ma wartość '{value}'. Dozwolone: {allowed}.")
    return saos_name


def _flag(value: bool | str | None, *, name: str) -> bool:
    if value is None or isinstance(value, bool):
        return bool(value)
    folded = value.strip().lower()
    if folded in ("true", "1", "yes", "tak"):
        return True
    if folded in ("false", "0", "no", "nie", ""):
        return False
    raise ValueError(f"Parametr '{name}' musi mieć wartość true albo false.")


def _source_id(value: str | int) -> str:
    text = str(value).strip()
    if not _SOURCE_ID.fullmatch(text):
        raise ValueError(
            "Parametr 'source_id' musi składać się z cyfr (np. 554697) - identyfikator z pola source_id wyniku."
        )
    return text


def _page_limit(value: str | int | None) -> int:
    limit = parse_non_negative(value, name="limit", default=DEFAULT_ITEM_LIMIT)
    if limit < 1:
        raise ValueError("Parametr 'limit' musi wynosić co najmniej 1.")
    return min(limit, MAX_ITEM_LIMIT)


@dataclass(frozen=True, slots=True)
class SearchQuery:
    """A validated search: what is sent to SAOS and what is told back to the agent."""

    phrase: str | None
    signature: str | None
    date_from: date | None
    date_to: date
    date_to_clamped: bool
    court_type: str | None
    judgment_type: str | None

    def api_params(self) -> dict[str, str | int]:
        """Query parameters of `search/judgments`, without paging, in one fixed order.

        The order is part of the cache key, so two calls with the same meaning share a cached page.
        `judgmentDateTo` is always sent: without it the newest-first order starts with records dated
        in the future (typos in the source such as 3013-12-04).
        """
        params: dict[str, str | int] = {}
        if self.phrase:
            params["all"] = self.phrase
        if self.signature:
            params["caseNumber"] = self.signature
        if self.court_type:
            params["courtType"] = self.court_type
        if self.judgment_type:
            params["judgmentTypes"] = self.judgment_type
        if self.date_from:
            params["judgmentDateFrom"] = self.date_from.isoformat()
        params["judgmentDateTo"] = self.date_to.isoformat()
        params["sortingField"] = "JUDGMENT_DATE"
        params["sortingDirection"] = "DESC"
        return params

    def tool_parameters(self) -> dict[str, Any]:
        """The arguments that repeat this search, for a hint that proposes the next call."""
        parameters: dict[str, Any] = {}
        if self.phrase:
            parameters["phrase"] = self.phrase
        if self.signature:
            parameters["signature"] = self.signature
        if self.date_from:
            parameters["date_from"] = self.date_from.isoformat()
        parameters["date_to"] = self.date_to.isoformat()
        if self.court_type:
            parameters["court_type"] = self.court_type
        if self.judgment_type:
            parameters["judgment_type"] = judgment_type_label(self.judgment_type)
        return parameters

    def summary(self) -> str:
        """One line describing the search, stored with the result set."""
        parts = ["saos"]
        if self.phrase:
            parts.append(f"phrase={self.phrase}")
        if self.signature:
            parts.append(f"signature={self.signature}")
        if self.court_type:
            parts.append(f"court_type={self.court_type}")
        if self.judgment_type:
            parts.append(f"judgment_type={judgment_type_label(self.judgment_type)}")
        if self.date_from:
            parts.append(f"date_from={self.date_from.isoformat()}")
        clamped = " (ograniczone do dziś)" if self.date_to_clamped else ""
        parts.append(f"date_to={self.date_to.isoformat()}{clamped}")
        parts.append("sort=data orzeczenia malejąco")
        return " | ".join(parts)


def build_query(
    *,
    phrase: str | None,
    signature: str | None,
    date_from: str | None,
    date_to: str | None,
    court_type: str | None,
    judgment_type: str | None,
    today: date,
) -> SearchQuery:
    """Validate the caller's filters and apply the date bounds.

    Raises:
        ValueError: A filter is malformed, `date_from` lies after today or after `date_to`.
    """
    start = _iso_date(date_from, name="date_from")
    end = _iso_date(date_to, name="date_to")
    if start is not None and start > today:
        raise ValueError(f"Parametr 'date_from' ({start.isoformat()}) jest późniejszy niż dzisiejsza data.")
    clamped = end is not None and end > today
    effective_end = min(end, today) if end is not None else today
    if start is not None and start > effective_end:
        raise ValueError("Parametr 'date_from' nie może być późniejszy niż 'date_to'.")
    return SearchQuery(
        phrase=_text(phrase, name="phrase", max_length=PHRASE_MAX_LENGTH),
        signature=_text(signature, name="signature", max_length=SIGNATURE_MAX_LENGTH),
        date_from=start,
        date_to=effective_end,
        date_to_clamped=clamped,
        court_type=_court_type(court_type),
        judgment_type=_judgment_type(judgment_type),
    )


def search_hints(
    query: SearchQuery,
    records: list[JudgmentSummaryOutput],
    *,
    result_set_id: str | None,
    scope: ResultSetScope | None,
    limit: int,
    offset: int,
    total: int,
    next_offset: int | None,
    filter_max_records: int,
    portal_links: PortalLinks | None = None,
    coverage_gap: str | None = None,
) -> list[Hint]:
    """Next steps for a page of search results; `coverage_gap` is the first-page hint about what SAOS lacks."""
    hints: list[Hint] = []
    if not records:
        if total and offset >= total:
            hints.append(Hint(message=f"Offset {offset} jest poza wynikami: zapytanie ma {total} orzeczeń."))
        else:
            hints.append(
                Hint(
                    message=(
                        "Brak orzeczeń dla tego zapytania. Poluzuj filtry: usuń court_type lub judgment_type, "
                        "rozszerz zakres dat albo skróć frazę."
                    )
                )
            )
    if next_offset is not None:
        hints.append(
            Hint(
                message=f"Kolejna strona wyników zaczyna się od offset={next_offset} (łącznie {total}).",
                tool=TOOL_SEARCH,
                parameters={**query.tool_parameters(), "limit": limit, "offset": next_offset},
            )
        )
    hints.extend(_details_hints(records, portal_links))
    if result_set_id and query.court_type == "SUPREME":
        hints.append(
            Hint(
                message=(
                    "Zawęź zestaw do Izby Pracy Sądu Najwyższego wzorcem na nazwie izby i wydziału. Skan kolejnych "
                    "stron w poszukiwaniu izby pracy robi labour_only=true z court_type='SUPREME'."
                ),
                tool="filter_results",
                parameters={"result_set_id": result_set_id, "pattern": LABOUR_CHAMBER_PATTERN, "field": "division"},
            )
        )
    if result_set_id and query.court_type in (None, "COMMON"):
        hints.append(
            Hint(
                message=(
                    "Zawęź zestaw do wydziałów pracy sądów powszechnych wzorcem na kodzie wydziału "
                    f"({LABOUR_DIVISION_PATTERN}). Wzorzec '(15|21)$' pomija wydziały wyłącznie ubezpieczeń "
                    "społecznych; po nazwie wydziału działa field='division', pattern='Pracy'."
                ),
                tool="filter_results",
                parameters={
                    "result_set_id": result_set_id,
                    "pattern": LABOUR_DIVISION_PATTERN,
                    "field": "division_code",
                },
            )
        )
    hints.extend(
        _set_hints(
            query,
            records,
            result_set_id=result_set_id,
            scope=scope,
            filter_max_records=filter_max_records,
            repeat_parameters={"limit": filter_max_records, "offset": offset},
        )
    )
    if offset == 0 and coverage_gap:
        hints.append(Hint(message=coverage_gap))
    return hints


def _details_hints(
    records: list[JudgmentSummaryOutput], portal_links: PortalLinks | None, *, flag_allowed: bool = True
) -> list[Hint]:
    """Offer the details tool for the first result that has no court portal link.

    `flag_allowed` is false for a labour scan, which cannot be combined with `include_portal_links`.
    """
    without_link = next((r for r in records if r.portal_url is None), None)
    if without_link is None or not (portal_links is None or portal_links.skipped_for_budget or portal_links.failed):
        return []
    how = (
        f"dla pierwszych {PORTAL_LINK_CAP} wyników strony zrobi to też parametr include_portal_links=true."
        if flag_allowed
        else "labour_only nie łączy się z include_portal_links."
    )
    return [
        Hint(
            message=f"Odnośnik do orzeczenia w portalu sądu pobierzesz szczegółami orzeczenia (jedno żądanie do SAOS); {how}",
            tool=TOOL_DETAILS,
            parameters={"source_id": without_link.source_id},
        )
    ]


def _set_hints(
    query: SearchQuery,
    records: list[JudgmentSummaryOutput],
    *,
    result_set_id: str | None,
    scope: ResultSetScope | None,
    filter_max_records: int,
    repeat_parameters: dict[str, Any],
) -> list[Hint]:
    """Hints about the stored set, shared by the plain search and the labour scan.

    `repeat_parameters` are the arguments that, added to the search arguments, repeat the call with the
    smaller `limit` that `filter_results` accepts (the labour scan adds `labour_only` and its SAOS position).
    """
    hints: list[Hint] = []
    if result_set_id and len(records) > filter_max_records:
        hints.append(
            Hint(
                message=(
                    f"Zestaw ma {len(records)} rekordów, a filter_results przyjmuje najwyżej {filter_max_records} "
                    "na jedno wywołanie (większy zestaw kończy się błędem, a nie wynikiem częściowym). Aby go "
                    f"filtrować, powtórz wyszukiwanie z limit={filter_max_records}."
                ),
                tool=TOOL_SEARCH,
                parameters={**query.tool_parameters(), **repeat_parameters},
            )
        )
    if scope is not None and scope.scope is SetScope.PAGE:
        hints.append(
            Hint(
                message=(
                    "Zestaw obejmuje tylko to okno wyników: filter_results przeszuka wyłącznie te rekordy, "
                    "a brak dopasowania nie dowodzi, że szukane orzeczenie nie istnieje w SAOS."
                )
            )
        )
    first = next((r for r in records if r.signature != NO_SIGNATURE), None)
    if first is not None and any(r.judgment_type in ("Wyrok", "Uzasadnienie") for r in records):
        hints.append(
            Hint(
                message=(
                    "Wyrok i jego uzasadnienie to dwa osobne rekordy tej samej sprawy (ta sama sygnatura): "
                    "oba znajdziesz, szukając po sygnaturze."
                ),
                tool=TOOL_SEARCH,
                parameters={"signature": first.signature},
            )
        )
    return hints


def labour_hints(
    query: SearchQuery,
    records: list[JudgmentSummaryOutput],
    scan: LabourScan,
    *,
    limit: int,
    result_set_id: str | None,
    scope: ResultSetScope | None,
    filter_max_records: int,
    coverage_gap: str | None = None,
) -> list[Hint]:
    """Next steps for a labour scan: the exact call that continues it, and what the rule leaves out."""
    hints: list[Hint] = []
    span = f"pozycje SAOS od {scan.scanned_from} do {scan.scanned_to} wyłącznie, {scan.pages_fetched} str. SAOS"
    if not records:
        if scan.scan_complete and scan.scanned_to == scan.scanned_from:
            hints.append(
                Hint(message=f"Offset {scan.scanned_from} leży na końcu albo za końcem listy SAOS dla tego zapytania.")
            )
        elif scan.scan_complete:
            hints.append(
                Hint(
                    message=(
                        f"Skan dotarł do końca listy SAOS ({span}) i nie znalazł orzeczeń wydziałów pracy. "
                        "Poluzuj filtry: rozszerz zakres dat albo skróć frazę."
                    )
                )
            )
        else:
            hints.append(
                Hint(message=f"W przeskanowanym zakresie ({span}) nie było orzeczeń wydziałów pracy; skan trwa dalej.")
            )
    if scan.next_offset is not None:
        interrupted = (
            " Kolejna strona SAOS nie odpowiedziała; to samo wywołanie wznowi skan od tego miejsca."
            if scan.interrupted
            else ""
        )
        hints.append(
            Hint(
                message=(
                    f"Skan zatrzymał się na pozycji {scan.next_offset} listy SAOS (przeskanowano "
                    f"{scan.saos_records_scanned} rekordów). Kolejne orzeczenia wydziałów pracy: offset="
                    f"{scan.next_offset}. W trybie labour_only offset to pozycja na liście SAOS, a nie numer "
                    f"trafienia.{interrupted}"
                ),
                tool=TOOL_SEARCH,
                parameters={**query.tool_parameters(), "labour_only": True, "limit": limit, "offset": scan.next_offset},
            )
        )
    if result_set_id and query.court_type == "COMMON":
        hints.append(
            Hint(
                message=(
                    "Wzorzec '(15|21)$' pominie wydziały wyłącznie ubezpieczeń społecznych (kod kończący się na 18), "
                    "które skan zalicza do spraw pracy."
                ),
                tool="filter_results",
                parameters={"result_set_id": result_set_id, "pattern": "(15|21)$", "field": "division_code"},
            )
        )
    hints.extend(_details_hints(records, None, flag_allowed=False))
    hints.extend(
        _set_hints(
            query,
            records,
            result_set_id=result_set_id,
            scope=scope,
            filter_max_records=filter_max_records,
            repeat_parameters={"labour_only": True, "limit": filter_max_records, "offset": scan.scanned_from},
        )
    )
    if scan.scanned_from == 0 and coverage_gap:
        hints.append(Hint(message=coverage_gap))
    return hints


@dataclass(frozen=True, slots=True)
class ScanResult:
    """What a scan found and where it stopped; positions are positions in the SAOS result list."""

    hits: list[SaosSearchItem]
    scanned_from: int
    scanned_to: int
    pages_fetched: int
    complete: bool
    interrupted: bool


class SaosSearchService:
    """Plain search over SAOS with any `limit`/`offset`."""

    def __init__(
        self,
        client: SaosClient,
        result_store: ResultStore,
        *,
        today: Callable[[], date] | None = None,
        filter_max_records: int | None = None,
        coverage: CoverageRegistry | None = None,
    ) -> None:
        self._client = client
        self._result_store = result_store
        self._today = today
        self._filter_max_records = filter_max_records
        self._coverage = coverage

    async def _coverage_gap(self, query: SearchQuery, *, first_page: bool) -> str | None:
        """The hint about a gap in SAOS for the court type of `query`, on the first page of a search only.

        Coverage is read through the registry (memo, lock and fallback), never from the provider. It is best
        effort: whatever goes wrong here never costs the caller the search result.
        """
        if not first_page or query.court_type is None or self._coverage is None:
            return None
        try:
            coverage = await self._coverage.get(COVERAGE_KEY)
            segment = next((s for s in coverage.segments if s.key == query.court_type), None) if coverage else None
            if coverage is None or segment is None:
                return None
            # Measured, not assumed: the "no administrative judgments" sentence holds only while the
            # measurement (or its fallback) still counts none.
            if query.court_type == ADMINISTRATIVE and segment.count == 0:
                return (
                    ADMINISTRATIVE_MESSAGE
                    if coverage.origin == "measured"
                    else f"{ADMINISTRATIVE_MESSAGE} {FALLBACK_NOTE}"
                )
            return court_gap_message(
                segment, date_to=query.date_to, origin=coverage.origin, measured_on=coverage.measured_on
            )
        except Exception as error:
            logger.warning("SAOS coverage check failed (%s: %s)", type(error).__name__, error)
            return None

    async def _window(self, query: SearchQuery, *, limit: int, offset: int) -> tuple[list[SaosSearchItem], int, int]:
        """Fetch the records at `offset..offset+limit` of the SAOS list, the list's size and the request count.

        SAOS pages by `pageNumber` (zero-based) and `pageSize`, so an offset that is not a multiple of
        the limit lies across two SAOS pages. Then the window is cut from both; the second request is
        skipped when the first page already ended the list. Records are de-duplicated at the seam: SAOS
        may insert a record between the two requests, which would otherwise show up twice.
        """
        first, skip = divmod(offset, limit)
        base = query.api_params()
        page = await self._client.search({"pageSize": limit, "pageNumber": first, **base})
        items = list(page.items)
        total = page.info.total_results
        requests = 1
        if skip and len(page.items) == limit and (first + 1) * limit < total:
            follow = await self._client.search({"pageSize": limit, "pageNumber": first + 1, **base})
            seen = {item.id for item in items}
            items.extend(item for item in follow.items if item.id not in seen)
            requests = 2
        return items[skip : skip + limit], total, requests

    async def _scan(self, query: SearchQuery, rule: LabourRule, *, limit: int, offset: int) -> ScanResult:
        """Walk the SAOS list from `offset` and keep the records the rule accepts.

        Reads whole pages of `SCAN_PAGE_SIZE` records, at most `SCAN_MAX_PAGES` of them, and stops earlier
        when it holds `limit` hits or the list ends. The position it reports is the SAOS position right
        after the last record it looked at: after the last hit when it stopped on `limit` (so the next call
        neither skips nor repeats a record), after the last page otherwise. The first page failing fails
        the call; a later page failing ends the scan with what was found (`interrupted`).
        """
        base = query.api_params()
        first_page, skip = divmod(offset, SCAN_PAGE_SIZE)
        hits: list[SaosSearchItem] = []
        seen: set[int] = set()
        position = offset
        pages = 0
        complete = interrupted = False
        for number in range(first_page, first_page + SCAN_MAX_PAGES):
            try:
                page = await self._client.search({"pageSize": SCAN_PAGE_SIZE, "pageNumber": number, **base})
            except (SejmApiError, EgressRedirectRefusedError):
                if pages == 0:
                    raise
                interrupted = True
                break
            pages += 1
            start = skip if number == first_page else 0
            for index in range(start, len(page.items)):
                item = page.items[index]
                position = number * SCAN_PAGE_SIZE + index + 1
                if item.id in seen:  # SAOS inserted a record between two requests
                    continue
                seen.add(item.id)
                if rule.matches(item):
                    hits.append(item)
                    if len(hits) == limit:
                        break
            if len(hits) == limit:
                complete = position >= page.info.total_results
                break
            # A short page is the end of the list, whatever the total says.
            if position >= page.info.total_results or len(page.items) < SCAN_PAGE_SIZE:
                complete = True
                break
        return ScanResult(
            hits=hits,
            scanned_from=offset,
            scanned_to=position,
            pages_fetched=pages,
            complete=complete,
            interrupted=interrupted,
        )

    async def _labour_search(
        self, query: SearchQuery, *, limit: int, offset: int, rule: LabourRule
    ) -> EnrichedResponse[SearchSaosJudgmentsOutput]:
        """The `labour_only` mode: a bounded scan, stored as a window, with a cursor instead of page numbers."""
        result = await self._scan(query, rule, limit=limit, offset=offset)
        records = [to_judgment(item) for item in result.hits]
        scan = LabourScan(
            rule=rule.description,
            scanned_from=result.scanned_from,
            scanned_to=result.scanned_to,
            pages_fetched=result.pages_fetched,
            saos_records_scanned=result.scanned_to - result.scanned_from,
            next_offset=None if result.complete else result.scanned_to,
            scan_complete=result.complete,
            interrupted=result.interrupted,
        )
        summary = (
            f"{query.summary()} | labour_only=skan pozycji SAOS od {scan.scanned_from} do {scan.scanned_to} (wyłącznie)"
        )
        result_set_id: str | None = None
        scope: ResultSetScope | None = None
        if records:
            # A scan reads a window of an unknown-size list: PAGE scope, so `filter_results` never calls it complete.
            result_set_id, scope = await self._result_store.store(
                records,
                summary,
                len(records),
                window_offset=offset,
                scope=SetScope.PAGE,
                document_kind=DocumentKind.JUDGMENT,
            )
        output = SearchSaosJudgmentsOutput(
            source=SOURCE,
            results=records,
            total_count=None,
            returned_count=len(records),
            query_summary=summary,
            result_set_id=result_set_id,
            result_set_scope=scope,
            page_info=None,
            portal_links=None,
            scan=scan,
        )
        hints = labour_hints(
            query,
            records,
            scan,
            limit=limit,
            result_set_id=result_set_id,
            scope=scope,
            filter_max_records=self._filter_max_records or settings.effective_filter_max_records,
            coverage_gap=await self._coverage_gap(query, first_page=scan.scanned_from == 0),
        )
        return EnrichedResponse[SearchSaosJudgmentsOutput](data=output, hints=hints)

    async def _with_portal_links(
        self, records: list[JudgmentSummaryOutput], *, search_requests: int
    ) -> tuple[list[JudgmentSummaryOutput], PortalLinks]:
        """Ask for the detail of the first results, within the request budget of the whole call.

        Sequential on purpose: SAOS is an academic service, and five requests in a burst are not needed
        for five links. A failing detail leaves that result without a link and the search result stands.
        The first outage (host down, breaker open, redirect refused) or refusal (401, 403, 429, 451) ends the
        loop: asking the remaining results would only hammer a host that has just said it cannot, or will not,
        answer.
        """
        targets = records[: min(PORTAL_LINK_CAP, REQUEST_BUDGET - search_requests)]
        enriched = list(records)
        resolved = without_link = failed = 0
        stopped = False
        for index, record in enumerate(targets):
            if stopped:
                failed += 1
                continue
            try:
                detail = await self._client.judgment(record.source_id)
            except (ApiUnavailableError, EgressRedirectRefusedError):
                failed += 1
                stopped = True
                continue
            except (SejmApiError, ValueError) as error:
                failed += 1
                if is_refusal(error):
                    stopped = True
                continue
            address = to_detail(detail.data)[0].portal_url
            if address is None:
                without_link += 1
            else:
                enriched[index] = record.model_copy(update={"portal_url": address})
                resolved += 1
        return enriched, PortalLinks(
            requested=len(targets),
            resolved=resolved,
            without_link=without_link,
            failed=failed,
            stopped_after_outage=stopped,
            skipped_for_budget=len(records) - len(targets),
        )

    async def search(
        self,
        *,
        phrase: str | None = None,
        signature: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        court_type: str | None = None,
        judgment_type: str | None = None,
        limit: str | int | None = None,
        offset: str | int | None = None,
        include_portal_links: bool | str | None = False,
        labour_only: bool | str | None = False,
    ) -> EnrichedResponse[SearchSaosJudgmentsOutput]:
        """Validate, fetch the window (or scan for labour judgments), store it as a judgment set, build the response."""
        today = (self._today or current_date)()
        query = build_query(
            phrase=phrase,
            signature=signature,
            date_from=date_from,
            date_to=date_to,
            court_type=court_type,
            judgment_type=judgment_type,
            today=today,
        )
        page_limit = _page_limit(limit)
        page_offset = parse_non_negative(offset, name="offset", default=0)
        with_links = _flag(include_portal_links, name="include_portal_links")
        if _flag(labour_only, name="labour_only"):
            if with_links:
                raise ValueError(
                    "Parametry 'labour_only' i 'include_portal_links' nie łączą się (skan i pobieranie odnośników "
                    "razem przekroczyłyby budżet żądań do SAOS). Odnośniki pobierzesz get_saos_judgment_details."
                )
            # A scan without a court type means common courts: they are the only ones with division codes.
            scan_query = replace(query, court_type=query.court_type or "COMMON")
            rule = labour_rule_for(scan_query.court_type or "COMMON")
            return await self._labour_search(scan_query, limit=page_limit, offset=page_offset, rule=rule)

        items, saos_total, search_requests = await self._window(query, limit=page_limit, offset=page_offset)
        records = [to_judgment(item) for item in items]
        portal_links: PortalLinks | None = None
        if with_links and records:
            records, portal_links = await self._with_portal_links(records, search_requests=search_requests)
        elif with_links:
            portal_links = PortalLinks(
                requested=0, resolved=0, without_link=0, failed=0, stopped_after_outage=False, skipped_for_budget=0
            )
        # Never below the records held: a count smaller than the payload would fail `PageInfo`.
        total = max(saos_total, page_offset + len(records)) if records else saos_total
        summary = query.summary()
        if with_links:
            summary = f"{summary} | portal_links=pierwsze {PORTAL_LINK_CAP}"

        result_set_id: str | None = None
        scope: ResultSetScope | None = None
        if records:
            result_set_id, scope = await self._result_store.store(
                records, summary, total, window_offset=page_offset, document_kind=DocumentKind.JUDGMENT
            )
        page_info = item_page_info(limit=page_limit, offset=page_offset, returned=len(records), total=total)
        output = SearchSaosJudgmentsOutput(
            source=SOURCE,
            results=records,
            total_count=total,
            returned_count=len(records),
            query_summary=summary,
            result_set_id=result_set_id,
            result_set_scope=scope,
            page_info=page_info,
            portal_links=portal_links,
        )
        hints = search_hints(
            query,
            records,
            result_set_id=result_set_id,
            scope=scope,
            limit=page_limit,
            offset=page_offset,
            total=total,
            next_offset=page_info.next_offset,
            filter_max_records=self._filter_max_records or settings.effective_filter_max_records,
            portal_links=portal_links,
            coverage_gap=await self._coverage_gap(query, first_page=page_offset == 0),
        )
        return EnrichedResponse[SearchSaosJudgmentsOutput](data=output, hints=hints)


async def search_saos_judgments(
    app_context: AppContext,
    *,
    phrase: str | None = None,
    signature: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    court_type: str | None = None,
    judgment_type: str | None = None,
    limit: str | int | None = None,
    offset: str | int | None = None,
    include_portal_links: bool | str | None = False,
    labour_only: bool | str | None = False,
) -> EnrichedResponse[SearchSaosJudgmentsOutput]:
    """Entry point of the tool: builds the service from the module state and the shared result store."""
    state = saos_state(app_context)
    service = SaosSearchService(state.client, app_context.result_store, coverage=state.coverage)
    return await service.search(
        phrase=phrase,
        signature=signature,
        date_from=date_from,
        date_to=date_to,
        court_type=court_type,
        judgment_type=judgment_type,
        limit=limit,
        offset=offset,
        include_portal_links=include_portal_links,
        labour_only=labour_only,
    )


def details_hints(output: SaosJudgmentDetailsOutput, *, pair_hint: bool = True) -> list[Hint]:
    """Next steps for one judgment: its portal link (or why there is none) and its other record.

    A loaded judgment already says where its reasoning is, so `pair_hint=False` leaves the pointer to the
    other record out.
    """
    record = output.judgment
    hints: list[Hint] = []
    if record.portal_url:
        hints.append(
            Hint(
                message=(
                    f"Odnośnik do orzeczenia w portalu sądu: {record.portal_url}. Adres pochodzi od sądu i bywa "
                    "adresem API jego systemu, a nie stroną dla człowieka; serwer go nie pobiera."
                )
            )
        )
    else:
        hints.append(Hint(message="SAOS nie podaje dla tego orzeczenia poprawnego odnośnika do portalu sądu."))
    searchable = _search_signature(record.signature)
    if pair_hint and record.signature != NO_SIGNATURE and searchable:
        hints.append(
            Hint(
                message=(
                    "Wyrok i jego uzasadnienie to dwa osobne rekordy tej samej sprawy; drugi znajdziesz po sygnaturze."
                ),
                tool=TOOL_SEARCH,
                parameters={"signature": searchable},
            )
        )
    return hints


ANONYMISATION_HINT = (
    "Fragmenty typu „(...)”, inicjały i oznaczenia w nawiasach (np. „D. P. (1)”) to dane zanonimizowane przez SAOS "
    "i część tekstu orzeczenia. Tekst może zawierać dane osobowe, które sąd w nim zostawił (np. nazwiska "
    "pracowników sądu): cytuj z rozwagą."
)


def loaded_hints(loaded: SaosLoadedJudgment, signature: str) -> list[Hint]:
    """Next steps after a judgment was loaded: read it, search it, what the markers are, where the reasoning is."""
    key = loaded.document_id
    hints = [
        Hint(
            message=(
                f"Orzeczenie wczytano jako dokument {key}: spis treści i sekcje czytasz read_act_content(eli='{key}'). "
                "Parametr eli przyjmuje tu identyfikator dokumentu, nie ELI."
            ),
            tool="read_act_content",
            parameters={"eli": key},
        ),
        Hint(
            message=f"Szukanie w treści: search_in_act(eli='{key}', query=...).",
            tool="search_in_act",
            parameters={"eli": key},
        ),
        Hint(message=ANONYMISATION_HINT),
        Hint(message=loaded.reasoning_note),
    ]
    if loaded.reasoning is ReasoningStatus.AMBIGUOUS:
        hints.extend(
            Hint(
                message=f"Wczytaj rekord uzasadnienia {candidate} osobno.",
                tool=TOOL_DETAILS,
                parameters={"source_id": candidate, "load_content": True},
            )
            for candidate in loaded.candidate_source_ids
        )
    searchable = _search_signature(signature)
    if loaded.reasoning is ReasoningStatus.REASONING_RECORD and signature != NO_SIGNATURE and searchable:
        hints.append(
            Hint(
                message="Wyrok tej sprawy znajdziesz wyszukując po sygnaturze.",
                tool=TOOL_SEARCH,
                parameters={"signature": searchable},
            )
        )
    return hints


async def get_saos_judgment_details(
    app_context: AppContext, *, source_id: str | int, load_content: bool | str | None = False
) -> EnrichedResponse[SaosJudgmentDetailsOutput]:
    """Entry point of the details tool.

    Without `load_content`: one request to SAOS, metadata and the court portal address only. With it: the
    judgment is also loaded into the document store (see `SaosReadingService`), with at most three calls to
    SAOS in all, and the answer says what was loaded.

    Raises:
        ValueError: `source_id` is not a number, `load_content` is not a flag, SAOS has no such judgment, or
            (with `load_content`) it has no text.
    """
    identifier = _source_id(source_id)
    loading = _flag(load_content, name="load_content")
    if not loading:
        detail = await saos_state(app_context).client.judgment(identifier)
        record, division_type = to_detail(detail.data)
        output = SaosJudgmentDetailsOutput(source=SOURCE, judgment=record, division_type=division_type)
        return EnrichedResponse[SaosJudgmentDetailsOutput](data=output, hints=details_hints(output))

    reading = SaosReadingService(saos_state(app_context).client, app_context.document_store)
    loaded, data = await reading.load_with_data(identifier)
    record, division_type = to_detail(data)
    output = SaosJudgmentDetailsOutput(source=SOURCE, judgment=record, division_type=division_type, loaded=loaded)
    hints = [*loaded_hints(loaded, record.signature), *details_hints(output, pair_hint=False)]
    return EnrichedResponse[SaosJudgmentDetailsOutput](data=output, hints=hints)


# Loading a judgment sends at most this many calls to SAOS: the detail, a search by case number for the
# reasons record, and the detail of that record. Both extra calls are paced and counted by the host like
# every other call; a search answered from the cache still counts here.
MAX_READ_REQUESTS = 3
PAIRING_PAGE_SIZE = 10
SENTENCE = "SENTENCE"
REASONS = "REASONS"
COMMON_COURT = "COMMON"
# What the agent is told when the extra calls cannot settle the question. None of these is a claim that the
# reasoning is missing: only `ReasoningStatus.ABSENT` says that.
NOTE_IN_RECORD = "Uzasadnienie jest w tekście tego rekordu (sekcja UZASADNIENIE)."
NOTE_ABSENT = (
    "Brak uzasadnienia w źródle: rekord nie ma sekcji uzasadnienia, a SAOS nie ma osobnego rekordu uzasadnienia "
    "tej sprawy w tym sądzie. Do tekstu dodano oznaczoną uwagę serwera."
)
NOTE_REASONING_RECORD = "To rekord uzasadnienia; wyrok tej sprawy znajdziesz wyszukując po sygnaturze."
NOTE_OTHER_TYPE = (
    "Nie oceniano uzasadnienia: wyrok i uzasadnienie jako osobne rekordy SAOS sprawdzono tylko dla wyroków sądów "
    "powszechnych, a to orzeczenie jest innego rodzaju lub sądu."
)
NOTE_NO_SIGNATURE = "Nie oceniano uzasadnienia: rekord nie ma sygnatury, więc nie ma po czym szukać osobnego rekordu."
NOTE_SEARCH_FAILED = "Nie udało się sprawdzić uzasadnienia (SAOS nie odpowiedział na wyszukiwanie); wczytano sam wyrok."
NOTE_SIBLING_FAILED = "Nie udało się pobrać rekordu uzasadnienia (SAOS nie odpowiedział lub rekord jest nieprawidłowy); wczytano sam wyrok."
NOTE_TRUNCATED = (
    "Nie wiadomo, czy uzasadnienie istnieje: SAOS ma więcej rekordów o tej sygnaturze, niż mieści jedna strona "
    "wyszukiwania, a żaden z nich nie jest uzasadnieniem tego sądu. Wczytano sam wyrok."
)
# The heading the module recognises is a level-2 `UZASADNIENIE`; the same word elsewhere in the text (a bold
# line, a deeper heading, inline) may mark reasoning that is in the record. Then `absent` would be false.
REASONING_WORD = "UZASADNIENIE"
NOTE_REASONING_WORD = (
    "Nie wiadomo, czy uzasadnienie istnieje: tekst wyroku zawiera słowo UZASADNIENIE poza nagłówkiem, który serwer "
    "rozpoznaje, a SAOS nie ma osobnego rekordu uzasadnienia tej sprawy. Przejrzyj tekst. Wczytano sam wyrok."
)
NOTE_COURT_UNKNOWN = (
    "Nie wiadomo, czy uzasadnienie istnieje: SAOS ma rekord uzasadnienia o tej sygnaturze, ale nie podaje sądu "
    "wyroku lub tego rekordu, więc nie da się ustalić, czy dotyczy tej sprawy. Wczytano sam wyrok."
)


def document_id(source_id: str) -> str:
    """The key a judgment is stored and read under, e.g. `SAOS/554697` (not an ELI)."""
    return f"{SOURCE.upper()}/{source_id}"


def _same_court(first: SaosDivision | None, second: SaosDivision | None) -> bool | None:
    """Whether two divisions belong to one court: by court id, or by name when an id is missing.

    `None` when the two cannot be compared (a division without a court, or no id and no name on both
    sides). That is not "a different court": a reasons record that cannot be placed must not be read as
    evidence that the sentence has none.
    """
    court_a = first.court if first else None
    court_b = second.court if second else None
    if court_a is None or court_b is None:
        return None
    if court_a.id is not None and court_b.id is not None:
        return court_a.id == court_b.id
    if court_a.name is not None and court_b.name is not None:
        return court_a.name == court_b.name
    return None


_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f-\x9f]+")


def _echoed(value: str) -> str:
    """Upstream text as it may appear in a note or a hint: control characters out, no longer than a search takes."""
    return _CONTROL_CHARACTERS.sub(" ", value).strip()[:SIGNATURE_MAX_LENGTH]


def _search_signature(value: str) -> str | None:
    """The signature when `search_saos_judgments` would accept it as it is, otherwise `None` (no hint then)."""
    return value if value and value == _echoed(value) else None


@dataclass(frozen=True, slots=True)
class _Reasoning:
    """What was settled about the reasoning of one judgment, and what to store."""

    status: ReasoningStatus
    note: str
    markdown: str
    requests: int
    sibling_source_id: str | None = None
    candidates: tuple[str, ...] = ()


class SaosReadingService:
    """Loads a judgment into the document store, with its reasons when they are a separate SAOS record."""

    def __init__(self, client: SaosClient, document_store: DocumentStore) -> None:
        self._client = client
        self._document_store = document_store

    async def load(self, source_id: str | int) -> SaosLoadedJudgment:
        """Fetch the judgment, settle where its reasoning is, and store the text as one document.

        Same as `load_with_data`, without the detail the answer was built from.
        """
        loaded, _data = await self.load_with_data(source_id)
        return loaded

    async def load_with_data(self, source_id: str | int) -> tuple[SaosLoadedJudgment, SaosTextData]:
        """Like `load`, and also hand back the detail, so the caller needs no second request for its metadata.

        The first call must succeed; the later ones (a search, the reasons record) may fail without
        losing the judgment, which is then stored alone with a status that makes no claim. After a refusal
        (`is_refusal`) no further call is made: the search and the reasons record are each the last call
        of their branch.

        Raises:
            ValueError: `source_id` is not a number, SAOS has no such judgment, or it has no text.
            SejmApiError: The detail cannot be fetched (the text of the error says which source failed).
        """
        identifier = _source_id(source_id)
        detail = await self._client.judgment_with_text(identifier)
        data = detail.data
        no_text = ValueError(f"Orzeczenie {identifier} nie ma w SAOS tekstu, więc nie ma czego wczytać.")
        if not data.text_content or not data.text_content.strip():
            raise no_text
        markdown = await to_markdown_async(data.text_content)
        # A text of tags alone (`<br>`) is not blank until it is converted.
        if not markdown.strip():
            raise no_text
        reasoning = await self._settle(data, markdown, requests=1)

        key = document_id(identifier)
        sections = index_sections(reasoning.markdown)
        await self._document_store.load(key, reasoning.markdown, sections, kind=DocumentKind.JUDGMENT)
        loaded = SaosLoadedJudgment(
            document_id=key,
            size_bytes=len(reasoning.markdown.encode("utf-8")),
            section_count=len(sections),
            reasoning=reasoning.status,
            reasoning_note=reasoning.note,
            requests_made=reasoning.requests,
            sibling_source_id=reasoning.sibling_source_id,
            candidate_source_ids=list(reasoning.candidates),
        )
        return loaded, data

    async def _settle(self, data: SaosTextData, markdown: str, *, requests: int) -> _Reasoning:
        """Decide where the reasoning is; only a common-court sentence without its own heading needs more calls."""
        if data.judgment_type == REASONS:
            return _Reasoning(ReasoningStatus.REASONING_RECORD, NOTE_REASONING_RECORD, markdown, requests)
        if data.judgment_type != SENTENCE or data.court_type != COMMON_COURT:
            return _Reasoning(ReasoningStatus.NOT_ASSESSED, NOTE_OTHER_TYPE, markdown, requests)
        if has_reasoning_heading(markdown):
            return _Reasoning(ReasoningStatus.IN_RECORD, NOTE_IN_RECORD, markdown, requests)
        return await self._pair(data, markdown, requests=requests)

    async def _pair(self, data: SaosTextData, markdown: str, *, requests: int) -> _Reasoning:
        """Look for the reasons record of a sentence by its case number: same court, type `REASONS`, exactly one."""
        number = next((case.case_number for case in data.court_cases if case.case_number), None)
        if number is None:
            return _Reasoning(ReasoningStatus.NOT_ASSESSED, NOTE_NO_SIGNATURE, markdown, requests)
        requests += 1
        try:
            page = await self._client.search({"pageSize": PAIRING_PAGE_SIZE, "caseNumber": number})
        except (ApiUnavailableError, EgressRedirectRefusedError, SejmApiError):
            return _Reasoning(ReasoningStatus.NOT_ASSESSED, NOTE_SEARCH_FAILED, markdown, requests)

        # By id: SAOS may list a record twice when a record is inserted between the pages of one search.
        same_number = {
            item.id: item
            for item in page.items
            if item.judgment_type == REASONS and any(case.case_number == number for case in item.court_cases)
        }.values()
        candidates = [item for item in same_number if _same_court(item.division, data.division) is True]
        if len(candidates) > 1:
            ids = tuple(str(item.id) for item in candidates)
            note = (
                f"SAOS ma kilka rekordów uzasadnienia o sygnaturze {_echoed(number)} w tym sądzie ({', '.join(ids)}); "
                "żadnego nie dołączono. Wczytaj wybrany po jego source_id."
            )
            return _Reasoning(ReasoningStatus.AMBIGUOUS, note, markdown, requests, candidates=ids)
        if not candidates:
            if page.info.total_results > len(page.items):
                return _Reasoning(ReasoningStatus.NOT_ASSESSED, NOTE_TRUNCATED, markdown, requests)
            if any(_same_court(item.division, data.division) is None for item in same_number):
                return _Reasoning(ReasoningStatus.NOT_ASSESSED, NOTE_COURT_UNKNOWN, markdown, requests)
            if REASONING_WORD in markdown:
                return _Reasoning(ReasoningStatus.NOT_ASSESSED, NOTE_REASONING_WORD, markdown, requests)
            return _Reasoning(ReasoningStatus.ABSENT, NOTE_ABSENT, with_no_reasoning_note(markdown), requests)
        return await self._merge(markdown, str(candidates[0].id), requests=requests)

    async def _merge(self, markdown: str, sibling_id: str, *, requests: int) -> _Reasoning:
        """Fetch the reasons record found for a sentence and put its text after the sentence.

        The last of the `MAX_READ_REQUESTS` calls: the detail and the search come before it.
        """
        requests += 1
        try:
            sibling = await self._client.judgment_with_text(sibling_id)
        except (ApiUnavailableError, EgressRedirectRefusedError, ResponseTooLargeError, SejmApiError, ValueError):
            return _Reasoning(ReasoningStatus.NOT_ASSESSED, NOTE_SIBLING_FAILED, markdown, requests)
        text = sibling.data.text_content
        if sibling.data.judgment_type != REASONS or not text or not text.strip():
            return _Reasoning(ReasoningStatus.NOT_ASSESSED, NOTE_SIBLING_FAILED, markdown, requests)
        note = (
            f"Dołączono uzasadnienie z osobnego rekordu SAOS {sibling_id} (ta sama sygnatura i sąd); "
            "to sekcja UZASADNIENIE."
        )
        reasons = await to_markdown_async(text)
        if not reasons.strip():
            return _Reasoning(ReasoningStatus.NOT_ASSESSED, NOTE_SIBLING_FAILED, markdown, requests)
        merged = with_reasons(markdown, reasons)
        return _Reasoning(ReasoningStatus.MERGED, note, merged, requests, sibling_source_id=sibling_id)

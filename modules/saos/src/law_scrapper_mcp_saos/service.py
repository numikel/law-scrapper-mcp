"""Search service of the SAOS module: validation, SAOS paging, result storage and hints.

Everything an agent reads in a response is built here, so the tool stays a thin adapter.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from law_scrapper_mcp.client.exceptions import ApiUnavailableError, EgressRedirectRefusedError, SejmApiError
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
from law_scrapper_mcp.services.pagination import item_page_info, parse_non_negative
from law_scrapper_mcp.services.result_store import ResultStore
from law_scrapper_mcp_saos.client import SaosClient
from law_scrapper_mcp_saos.mapping import (
    COURT_TYPES,
    JUDGMENT_TYPE_LABELS,
    NO_SIGNATURE,
    SOURCE,
    judgment_type_label,
    saos_judgment_type,
    to_detail,
    to_judgment,
)
from law_scrapper_mcp_saos.models import (
    PortalLinks,
    SaosJudgmentDetailsOutput,
    SaosSearchItem,
    SearchSaosJudgmentsOutput,
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
_SOURCE_ID = re.compile(r"[0-9]{1,12}")
PHRASE_MAX_LENGTH = 500
SIGNATURE_MAX_LENGTH = 100
LABOUR_DIVISION_PATTERN = "(15|18|21)$"
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

# What SAOS held on the day it was measured (tests/fixtures/saos/PROVENANCE.md). Static on purpose and
# dated, so the sentence cannot pass for a live count; `modules/saos/tests/test_saos_coverage.py` ties every
# number and date to the recording it came from.
COVERAGE_AS_OF = "2026-11-02"
COVERAGE_SENTENCE = (
    f"Zasięg SAOS (pomiar z {COVERAGE_AS_OF}): sądy powszechne 474 859 orzeczeń (najnowsze z 2026-10-27), "
    "Sąd Najwyższy 38 081 (najnowsze z 2016-06-22), Trybunał Konstytucyjny 9 503 (najnowsze z 2015-12-09), "
    "Krajowa Izba Odwoławcza 22 168 (najnowsze z 2018-09-06), sądy administracyjne 0. "
    "Brak wyniku w SAOS nie dowodzi, że orzeczenie nie istnieje."
)


def current_date() -> date:
    """Today's date; looked up at call time so tests can replace it."""
    return date.today()


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
) -> list[Hint]:
    """Next steps for a page of search results."""
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
    without_link = next((r for r in records if r.portal_url is None), None)
    if without_link is not None and (portal_links is None or portal_links.skipped_for_budget or portal_links.failed):
        hints.append(
            Hint(
                message=(
                    "Odnośnik do orzeczenia w portalu sądu pobierzesz szczegółami orzeczenia (jedno żądanie do SAOS); "
                    f"dla pierwszych {PORTAL_LINK_CAP} wyników strony zrobi to też parametr include_portal_links=true."
                ),
                tool=TOOL_DETAILS,
                parameters={"source_id": without_link.source_id},
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
    if result_set_id and len(records) > filter_max_records:
        hints.append(
            Hint(
                message=(
                    f"Zestaw ma {len(records)} rekordów, a filter_results przyjmuje najwyżej {filter_max_records} "
                    "na jedno wywołanie (większy zestaw kończy się błędem, a nie wynikiem częściowym). Aby go "
                    f"filtrować, powtórz wyszukiwanie z limit={filter_max_records}."
                ),
                tool=TOOL_SEARCH,
                parameters={**query.tool_parameters(), "limit": filter_max_records, "offset": offset},
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
    if offset == 0:
        hints.append(Hint(message=COVERAGE_SENTENCE))
    return hints


class SaosSearchService:
    """Plain search over SAOS with any `limit`/`offset`."""

    def __init__(
        self,
        client: SaosClient,
        result_store: ResultStore,
        *,
        today: Callable[[], date] | None = None,
        filter_max_records: int | None = None,
    ) -> None:
        self._client = client
        self._result_store = result_store
        self._today = today
        self._filter_max_records = filter_max_records

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

    async def _with_portal_links(
        self, records: list[JudgmentSummaryOutput], *, search_requests: int
    ) -> tuple[list[JudgmentSummaryOutput], PortalLinks]:
        """Ask for the detail of the first results, within the request budget of the whole call.

        Sequential on purpose: SAOS is an academic service, and five requests in a burst are not needed
        for five links. A failing detail leaves that result without a link and the search result stands.
        The first outage (host down, breaker open, redirect refused) ends the loop: asking the remaining
        results would only hammer a host that has just said it cannot answer.
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
            except (SejmApiError, ValueError):
                failed += 1
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
    ) -> EnrichedResponse[SearchSaosJudgmentsOutput]:
        """Validate, fetch the window, store it as a judgment set and build the response."""
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
) -> EnrichedResponse[SearchSaosJudgmentsOutput]:
    """Entry point of the tool: builds the service from the module state and the shared result store."""
    service = SaosSearchService(saos_state(app_context).client, app_context.result_store)
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
    )


def details_hints(output: SaosJudgmentDetailsOutput) -> list[Hint]:
    """Next steps for one judgment: its portal link (or why there is none) and its other record."""
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
    if record.signature != NO_SIGNATURE:
        hints.append(
            Hint(
                message=(
                    "Wyrok i jego uzasadnienie to dwa osobne rekordy tej samej sprawy; drugi znajdziesz po sygnaturze."
                ),
                tool=TOOL_SEARCH,
                parameters={"signature": record.signature},
            )
        )
    return hints


async def get_saos_judgment_details(
    app_context: AppContext, *, source_id: str | int
) -> EnrichedResponse[SaosJudgmentDetailsOutput]:
    """Entry point of the details tool: one request to SAOS, metadata and the court portal address only.

    Raises:
        ValueError: `source_id` is not a number, or SAOS has no such judgment.
    """
    identifier = _source_id(source_id)
    detail = await saos_state(app_context).client.judgment(identifier)
    record, division_type = to_detail(detail.data)
    output = SaosJudgmentDetailsOutput(source=SOURCE, judgment=record, division_type=division_type)
    return EnrichedResponse[SaosJudgmentDetailsOutput](data=output, hints=details_hints(output))

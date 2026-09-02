"""Changes tracking service for legal acts."""

import logging
from datetime import datetime
from typing import Any

from law_scrapper_mcp.client.sejm_client import SejmApiClient
from law_scrapper_mcp.config import settings
from law_scrapper_mcp.models.pagination import DEFAULT_ITEM_LIMIT, MAX_ITEM_LIMIT
from law_scrapper_mcp.models.tool_outputs import ActSummaryOutput, ChangesOutput, ResultSetScope
from law_scrapper_mcp.services.pagination import effective_limit, item_page_info, parse_non_negative
from law_scrapper_mcp.services.result_store import ResultStore

logger = logging.getLogger(__name__)


class ChangesService:
    """Track legal changes using search endpoint (workaround for WAF-blocked /eli/changes/acts)."""

    def __init__(self, client: SejmApiClient, result_store: ResultStore) -> None:
        self._client = client
        self._result_store = result_store

    async def _output(
        self,
        *,
        results: list[ActSummaryOutput],
        total_count: int,
        date_range: str,
        publisher: str,
        keywords: list[str],
        limit: int,
        offset: int,
    ) -> ChangesOutput:
        """Build one page of tracked changes together with truthful page metadata.

        `results` is the window the Sejm API already built for `limit`/`offset`, so
        there is no local slice to apply beyond trimming the one record fetched on
        behalf of `limit=0`. Mirrors `SearchService._output`: `total` is clamped up
        to the records actually held so a quirky upstream `totalCount` cannot fail
        `PageInfo` validation, and the store is told where the window sits in the
        corpus so it can derive COMPLETE or PAGE on its own (#54).
        """
        query_summary = f"changes: {date_range} | publisher={publisher}"
        if keywords:
            query_summary += f" | keywords={','.join(keywords)}"
        page = results[:limit]
        total = max(total_count, offset + len(page)) if page else total_count
        result_set_id: str | None = None
        result_set_scope: ResultSetScope | None = None
        if page:
            result_set_id, result_set_scope = await self._result_store.store(
                page,
                query_summary,
                total,
                window_offset=offset,
            )
        return ChangesOutput(
            date_range=date_range,
            publisher=publisher,
            keywords=keywords,
            changes=page,
            total_count=total,
            result_set_id=result_set_id,
            result_set_scope=result_set_scope,
            page_info=item_page_info(limit=limit, offset=offset, returned=len(page), total=total),
        )

    async def track_changes(
        self,
        publisher: str = "DU",
        date_from: str = "",
        date_to: str | None = None,
        keywords: list[str] | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> ChangesOutput:
        """Track changes in legal acts within date range, one upstream page at a time."""
        if not date_to:
            date_to = datetime.now().strftime("%Y-%m-%d")

        # Parsed before the request because both values reach the wire. Until #54 the
        # range was downloaded unbounded — the API's own default page, measured at
        # 500 records (tests/fixtures/search_default_page.provenance.md) — stored
        # whole and sliced locally, which made every stored set "complete" by
        # construction and left records past the default page unreachable.
        page_limit = effective_limit(limit, default=DEFAULT_ITEM_LIMIT, maximum=MAX_ITEM_LIMIT)
        page_offset = parse_non_negative(offset, name="offset", default=0)

        params: dict[str, Any] = {
            "publisher": publisher,
            "dateFrom": date_from,
            "dateTo": date_to,
        }
        if keywords:
            params["keyword"] = ",".join(keywords)
        # Floored at 1 for the same reason `SearchService` floors it: a zero-item page
        # still owes the caller a truthful `totalCount`, and what the endpoint does with
        # `limit=0` is unverified. `_output` trims the record away.
        params["limit"] = max(page_limit, 1)
        if page_offset:
            params["offset"] = page_offset

        data = await self._client.get_json("acts/search", params=params, cache_ttl=settings.cache_changes_ttl)

        items = data.get("items", [])
        # `totalCount` is the size of the date range, `count` the size of this page.
        total_count = data.get("totalCount", len(items))
        results = []
        for item in items:
            results.append(
                ActSummaryOutput(
                    eli=item.get("ELI", ""),
                    publisher=item.get("publisher", ""),
                    year=item.get("year", 0),
                    pos=item.get("pos", 0),
                    title=item.get("title", ""),
                    status=item.get("status", ""),
                    type=item.get("type"),
                    promulgation_date=item.get("promulgation"),
                    effective_date=item.get("entryIntoForce"),
                    in_force=item.get("inForce"),
                )
            )

        date_range = f"{date_from} to {date_to}"
        keyword_list = keywords or []

        return await self._output(
            results=results,
            total_count=total_count,
            date_range=date_range,
            publisher=publisher,
            keywords=keyword_list,
            limit=page_limit,
            offset=page_offset,
        )

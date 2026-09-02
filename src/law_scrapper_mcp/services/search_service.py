"""Search service for legal acts."""

import logging
from typing import Any

from law_scrapper_mcp.client.sejm_client import SejmApiClient
from law_scrapper_mcp.config import settings
from law_scrapper_mcp.models.enums import DetailLevel
from law_scrapper_mcp.models.pagination import DEFAULT_ITEM_LIMIT
from law_scrapper_mcp.models.tool_outputs import ActSummaryOutput, ResultSetScope, SearchOutput
from law_scrapper_mcp.services.pagination import item_page_info
from law_scrapper_mcp.services.result_store import ResultStore

logger = logging.getLogger(__name__)


class SearchService:
    """Service for searching and browsing legal acts."""

    def __init__(self, client: SejmApiClient, result_store: ResultStore) -> None:
        self._client = client
        self._result_store = result_store

    async def _output(
        self,
        results: list[ActSummaryOutput],
        *,
        total_count: int,
        query_summary: str,
        limit: int | None,
        offset: int = 0,
        window_offset: int = 0,
    ) -> SearchOutput:
        """Build one page of results together with truthful page metadata.

        `window_offset` is the offset the Sejm API already applied server-side. Both
        callers now pass the request offset, because both go through `acts/search`,
        which skips those records upstream. A zero here would mean the caller received
        an unwindowed payload and wants the slice done locally.

        `total` is clamped up to the records actually held. A `count` smaller than the
        payload would otherwise fail `PageInfo` validation and turn a quirky upstream
        response into a tool error.
        """
        page_limit = max(limit if limit is not None else DEFAULT_ITEM_LIMIT, 0)
        page_offset = max(offset, 0)
        window_offset = max(window_offset, 0)
        local_offset = max(page_offset - window_offset, 0)
        page = results[local_offset : local_offset + page_limit]
        total = max(total_count, page_offset + len(page)) if page else total_count
        result_set_id: str | None = None
        result_set_scope: ResultSetScope | None = None
        if page:
            # `page_offset`, not the `window_offset` parameter above: the store wants the
            # window's position in the corpus, while this method's `window_offset` means
            # "how much the API already skipped". They are equal for both current callers,
            # which is exactly why passing the wrong one would go unnoticed.
            result_set_id, result_set_scope = await self._result_store.store(
                page,
                query_summary,
                total,
                window_offset=page_offset,
            )
        return SearchOutput(
            results=page,
            total_count=total,
            query_summary=query_summary,
            returned_count=len(page),
            result_set_id=result_set_id,
            result_set_scope=result_set_scope,
            page_info=item_page_info(
                limit=page_limit,
                offset=page_offset,
                returned=len(page),
                total=total,
            ),
        )

    async def search(
        self,
        publisher: str = "DU",
        year: int | None = None,
        keywords: list[str] | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        title: str | None = None,
        act_type: str | None = None,
        pub_date_from: str | None = None,
        pub_date_to: str | None = None,
        in_force: bool | None = None,
        limit: int | None = None,
        offset: int | None = None,
        detail_level: DetailLevel = DetailLevel.STANDARD,
    ) -> SearchOutput:
        """Search for legal acts and return a stored result page."""
        params: dict[str, Any] = {"publisher": publisher}

        summary_parts = [f"publisher={publisher}"]

        if year:
            params["year"] = year
            summary_parts.append(f"year={year}")
        if keywords:
            params["keyword"] = ",".join(keywords)
            summary_parts.append(f"keywords={','.join(keywords)}")
        if date_from:
            params["dateEffectFrom"] = date_from
            summary_parts.append(f"effective_from={date_from}")
        if date_to:
            params["dateEffectTo"] = date_to
            summary_parts.append(f"effective_to={date_to}")
        if title:
            params["title"] = title
            summary_parts.append(f"title={title}")
        if act_type:
            params["type"] = act_type
            summary_parts.append(f"type={act_type}")
        if pub_date_from:
            params["dateFrom"] = pub_date_from
        if pub_date_to:
            params["dateTo"] = pub_date_to
        if in_force is not None:
            params["inForce"] = in_force
            summary_parts.append(f"in_force={in_force}")
        # Always sent now, the default included (D7). Without it the API builds a page
        # of its own choosing: a measured 709 437 B and 500 records for DU/2024
        # (tests/fixtures/search_default_page.provenance.md), of which `_output` keeps
        # twenty and discards the rest locally. An explicit `limit` is still not clamped
        # — `search_legal_acts` remains the one list tool without a ceiling, pinned by
        # `test_limit_above_the_shared_maximum_is_not_clamped`.
        #
        # Floored at 1 for the same reason `browse()` floors it: what the search endpoint
        # does with `limit=0` is unverified, and a zero-item page still owes the caller a
        # truthful `totalCount`. `_output` slices the record away either way.
        params["limit"] = max(limit if limit is not None else DEFAULT_ITEM_LIMIT, 1)
        if offset:
            params["offset"] = offset

        data = await self._client.get_json("acts/search", params=params, cache_ttl=settings.cache_search_ttl)

        items = data.get("items", [])
        # `totalCount` is the size of the corpus, `count` the size of the returned page.
        # Reading `count` here made a search matching 1984 acts report twenty, and with it
        # `was_truncated=False` and no pagination hint at all — the model was told "that is
        # everything there is". The product decision Klaster 8 deferred was taken in
        # Klaster 9 (D1): docs/superpowers/specs/2026-09-01-klaster-9.md.
        #
        # The fallback chain is not decoration. Responses without `totalCount` keep their
        # previous meaning, which is what lets every existing pagination test pass
        # unmodified — the change shows up where the API supplies the truth and nowhere else.
        total_count = data.get("totalCount", data.get("count", len(items)))

        results = [self._format_act(item, detail_level) for item in items]
        query_summary = " | ".join(summary_parts)

        return await self._output(
            results,
            total_count=total_count,
            query_summary=query_summary,
            limit=limit,
            offset=offset or 0,
            window_offset=offset or 0,
        )

    async def browse(
        self,
        publisher: str,
        year: int,
        detail_level: DetailLevel = DetailLevel.STANDARD,
        limit: int | None = None,
        offset: int | None = None,
    ) -> SearchOutput:
        """Browse acts by publisher and year and return a stored result page.

        Goes through `acts/search` rather than `acts/{publisher}/{year}`: the year
        endpoint ignores `limit` and `offset` and answers every page with the entire
        year (1 093 224 B and 1984 records for DU/2024), while the search endpoint
        honours both and preserves the year endpoint's order and `totalCount`.

        The cache key `json:acts/search:{params}` is now shared with `search()`
        whenever the parameters match exactly. That is deliberate (D8), not an
        oversight: both calls ask the API the same question and get the same answer,
        and giving `browse` its own key prefix would fetch the same year twice — more
        outbound traffic, which is what this whole change exists to reduce. Because
        the cache key does not include TTL (`cache_search_ttl=600` vs
        `cache_browse_ttl=3600`), whichever call stores the entry first decides
        freshness for both — the 6× TTL difference stops applying to whichever method
        writes the cache entry second.

        `limit` is clamped by the calling tool, not here, and that clamp is load-bearing
        now rather than cosmetic: `acts/search` honours `limit`, so it decides how wide a
        page the API builds, and its records carry more fields than the year endpoint's
        did. Unclamped, a large `limit` would cost more bytes per record than the old
        `acts/{publisher}/{year}` behaviour did, not less.
        """
        page_limit = DEFAULT_ITEM_LIMIT if limit is None else max(limit, 0)
        page_offset = max(offset or 0, 0)
        params: dict[str, Any] = {
            "publisher": publisher,
            "year": year,
            # A zero-item page still owes the caller a truthful `totalCount`, and what
            # the search endpoint does with `limit=0` is unverified. Asking for one
            # record and letting `_output` slice it away keeps the answer identical to
            # the pre-change one without probing that corner.
            "limit": max(page_limit, 1),
        }
        if page_offset:
            params["offset"] = page_offset
        data = await self._client.get_json("acts/search", params=params, cache_ttl=settings.cache_browse_ttl)

        items = data.get("items", [])
        # `count` is the size of the returned page on this endpoint; the size of the
        # year is `totalCount`. Copying the `count` reading from `search()` above would
        # report total_count=20 and was_truncated=False for a 1984-act year.
        total_count = data.get("totalCount", len(items))

        results = [self._format_act(item, detail_level) for item in items]
        query_summary = f"publisher={publisher} | year={year}"

        return await self._output(
            results,
            total_count=total_count,
            query_summary=query_summary,
            limit=limit,
            offset=page_offset,
            # The API skipped these records itself, so `_output` must not skip them
            # again — the same contract `search()` has used since pagination landed.
            window_offset=page_offset,
        )

    def _format_act(self, item: dict[str, Any], detail_level: DetailLevel) -> ActSummaryOutput:
        """Format an act item based on detail level."""
        output = ActSummaryOutput(
            eli=item.get("ELI", ""),
            publisher=item.get("publisher", ""),
            year=item.get("year", 0),
            pos=item.get("pos", 0),
            title=item.get("title", ""),
            status=item.get("status", ""),
        )

        if detail_level in (DetailLevel.STANDARD, DetailLevel.FULL):
            output.type = item.get("type")
            output.promulgation_date = item.get("promulgation")
            # `entryIntoForce`, not `dateEffect`: the latter is a key neither endpoint
            # returns (tests/fixtures/browse_page.provenance.md), so it read None on
            # every record ever formatted (#52).
            output.effective_date = item.get("entryIntoForce")
            output.in_force = item.get("inForce")

        return output

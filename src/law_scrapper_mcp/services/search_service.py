"""Search service for legal acts."""

import logging
from typing import Any

from law_scrapper_mcp.client.sejm_client import SejmApiClient
from law_scrapper_mcp.config import settings
from law_scrapper_mcp.models.enums import DetailLevel
from law_scrapper_mcp.models.tool_outputs import ActSummaryOutput

logger = logging.getLogger(__name__)


class SearchService:
    """Service for searching and browsing legal acts."""

    def __init__(self, client: SejmApiClient):
        self._client = client

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
    ) -> tuple[list[ActSummaryOutput], int, str]:
        """Search for legal acts. Returns (results, total_count, query_summary)."""
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
        if limit:
            params["limit"] = limit
        if offset:
            params["offset"] = offset

        data = await self._client.get_json(
            "acts/search", params=params, cache_ttl=settings.cache_search_ttl
        )

        items = data.get("items", [])
        total_count = data.get("count", len(items))

        results = [self._format_act(item, detail_level) for item in items]

        return results, total_count, " | ".join(summary_parts)

    async def browse(
        self, publisher: str, year: int, detail_level: DetailLevel = DetailLevel.STANDARD
    ) -> tuple[list[ActSummaryOutput], int]:
        """Browse acts by publisher and year. Returns (results, total_count)."""
        path = f"acts/{publisher}/{year}"
        data = await self._client.get_json(path, cache_ttl=settings.cache_browse_ttl)

        items = data.get("items", [])
        total_count = data.get("totalCount", len(items))

        results = [self._format_act(item, detail_level) for item in items]

        return results, total_count

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
            output.effective_date = item.get("dateEffect")
            output.in_force = item.get("inForce")

        return output

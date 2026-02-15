"""Changes tracking service for legal acts."""

import logging
from datetime import datetime

from law_scrapper_mcp.client.sejm_client import SejmApiClient
from law_scrapper_mcp.config import settings
from law_scrapper_mcp.models.tool_outputs import ActSummaryOutput

logger = logging.getLogger(__name__)


class ChangesService:
    """Track legal changes using search endpoint (workaround for WAF-blocked /eli/changes/acts)."""

    def __init__(self, client: SejmApiClient):
        self._client = client

    async def track_changes(
        self,
        publisher: str = "DU",
        date_from: str = "",
        date_to: str | None = None,
        keywords: list[str] | None = None,
    ) -> tuple[list[ActSummaryOutput], str]:
        """Track changes in legal acts within date range."""
        if not date_to:
            date_to = datetime.now().strftime("%Y-%m-%d")

        params = {
            "publisher": publisher,
            "dateFrom": date_from,
            "dateTo": date_to,
        }
        if keywords:
            params["keyword"] = ",".join(keywords)

        data = await self._client.get_json(
            "acts/search", params=params, cache_ttl=settings.cache_changes_ttl
        )

        items = data.get("items", [])
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
                    effective_date=item.get("dateEffect"),
                    in_force=item.get("inForce"),
                )
            )

        date_range = f"{date_from} to {date_to}"
        return results, date_range

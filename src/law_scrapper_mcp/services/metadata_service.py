"""Metadata service for legal acts system information."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from law_scrapper_mcp.client.sejm_client import SejmApiClient
from law_scrapper_mcp.config import settings
from law_scrapper_mcp.models.enums import MetadataCategory
from law_scrapper_mcp.models.pagination import DEFAULT_ITEM_LIMIT, MAX_ITEM_LIMIT
from law_scrapper_mcp.models.tool_outputs import MetadataOutput
from law_scrapper_mcp.services.pagination import effective_limit, paginate_items, parse_non_negative

logger = logging.getLogger(__name__)


class MetadataService:
    """Service for retrieving system metadata (keywords, publishers, statuses, types, institutions)."""

    METADATA_ORDER = (
        MetadataCategory.KEYWORDS,
        MetadataCategory.PUBLISHERS,
        MetadataCategory.STATUSES,
        MetadataCategory.TYPES,
        MetadataCategory.INSTITUTIONS,
    )

    def __init__(self, client: SejmApiClient):
        self._client = client

    async def get_metadata(self, category: MetadataCategory) -> dict[str, Any]:
        """Retrieve metadata for the given category or all categories."""
        ttl = settings.cache_metadata_ttl

        if category == MetadataCategory.ALL:
            results, _ = await self._fetch_all(ttl)
            return results

        return {category.value: await self._fetch_category(category, ttl)}

    async def _fetch_all(self, ttl: int) -> tuple[dict[str, Any], tuple[str, ...]]:
        """Fetch every category concurrently, preserving METADATA_ORDER.

        Concurrency is bounded by the client's semaphore, so this shortens the
        cold path without raising the ceiling of simultaneous requests against
        the public Sejm API.
        """
        outcomes = await asyncio.gather(
            *(self._fetch_category(category, ttl) for category in self.METADATA_ORDER),
            return_exceptions=True,
        )

        results: dict[str, Any] = {}
        failed: list[str] = []
        for category, outcome in zip(self.METADATA_ORDER, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                logger.warning("Failed to fetch metadata for %s: %s", category.value, outcome)
                results[category.value] = []
                failed.append(category.value)
            else:
                results[category.value] = outcome

        return results, tuple(failed)

    async def get_metadata_page(
        self,
        category: MetadataCategory,
        *,
        limit: str | int | None = DEFAULT_ITEM_LIMIT,
        offset: str | int | None = 0,
    ) -> MetadataOutput:
        """Return one deterministic metadata page across categories."""
        raw = await self.get_metadata(category)
        categories = self.METADATA_ORDER if category == MetadataCategory.ALL else (category,)
        flattened = [(current.value, item) for current in categories for item in raw.get(current.value, [])]
        page_limit = effective_limit(limit, default=DEFAULT_ITEM_LIMIT, maximum=MAX_ITEM_LIMIT)
        page_offset = parse_non_negative(offset, name="offset", default=0)
        page, page_info = paginate_items(flattened, limit=page_limit, offset=page_offset)
        metadata: dict[str, list[Any]] = {current.value: [] for current in categories}
        for key, item in page:
            metadata[key].append(item)
        return MetadataOutput(
            category=category.value,
            metadata=metadata,
            count=page_info.returned_count,
            page_info=page_info,
        )

    async def _fetch_category(self, category: MetadataCategory, ttl: int) -> Any:
        """Fetch a specific metadata category from the API."""
        endpoint_map = {
            MetadataCategory.KEYWORDS: "keywords",
            MetadataCategory.PUBLISHERS: "acts",
            MetadataCategory.STATUSES: "statuses",
            MetadataCategory.TYPES: "types",
            MetadataCategory.INSTITUTIONS: "institutions",
        }
        endpoint = endpoint_map[category]
        return await self._client.get_json(endpoint, cache_ttl=ttl)

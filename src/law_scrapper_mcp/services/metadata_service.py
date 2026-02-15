"""Metadata service for legal acts system information."""

import logging
from typing import Any

from law_scrapper_mcp.client.sejm_client import SejmApiClient
from law_scrapper_mcp.config import settings
from law_scrapper_mcp.models.enums import MetadataCategory

logger = logging.getLogger(__name__)


class MetadataService:
    """Service for retrieving system metadata (keywords, publishers, statuses, types, institutions)."""

    def __init__(self, client: SejmApiClient):
        self._client = client

    async def get_metadata(self, category: MetadataCategory) -> dict[str, Any]:
        """Retrieve metadata for the given category or all categories."""
        ttl = settings.cache_metadata_ttl

        if category == MetadataCategory.ALL:
            results = {}
            for cat in MetadataCategory:
                if cat == MetadataCategory.ALL:
                    continue
                try:
                    results[cat.value] = await self._fetch_category(cat, ttl)
                except Exception as e:
                    logger.warning(f"Failed to fetch metadata for {cat.value}: {e}")
                    results[cat.value] = []
            return results

        return {category.value: await self._fetch_category(category, ttl)}

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

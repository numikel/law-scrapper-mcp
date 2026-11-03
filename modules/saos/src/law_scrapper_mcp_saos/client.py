"""The module's view of the SAOS API, on top of the egress client the core hands out.

All traffic goes through `EgressClient`, so it is paced, retried, cached and counted by the host
breaker like every other upstream; this module builds no HTTP client of its own.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import ValidationError

from law_scrapper_mcp.client.egress import EgressClient
from law_scrapper_mcp.client.exceptions import SejmApiError
from law_scrapper_mcp_saos.models import SaosSearchPage

SEARCH_PATH = "search/judgments"


class SaosClient:
    """Parses SAOS answers into the module's API models."""

    def __init__(self, egress: EgressClient, *, search_ttl: int) -> None:
        self._egress = egress
        self._search_ttl = search_ttl

    async def search(self, params: Mapping[str, str | int]) -> SaosSearchPage:
        """Fetch one page of `search/judgments`; the page is cached under its own parameters.

        Raises:
            SejmApiError: The answer is not a search page (named after the SAOS host, so the tool
                layer says which source failed). Transport and status errors come from the
                egress client unchanged.
        """
        raw = await self._egress.get_json(SEARCH_PATH, dict(params), cache_ttl=self._search_ttl)
        try:
            return SaosSearchPage.model_validate(raw)
        except ValidationError as error:
            raise SejmApiError(
                "Odpowiedź SAOS nie jest stroną wyników wyszukiwania.", source=self._egress.host.name
            ) from error

"""The module's view of the SAOS API, on top of the egress client the core hands out.

All traffic goes through `EgressClient`, so it is paced, retried, cached and counted by the host
breaker like every other upstream; this module builds no HTTP client of its own.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from law_scrapper_mcp.client.egress import EgressClient, EgressHost
from law_scrapper_mcp.client.exceptions import ActNotFoundError, SejmApiError
from law_scrapper_mcp_saos.models import SaosDetail, SaosSearchPage, SaosTextDetail

SEARCH_PATH = "search/judgments"
DETAIL_PATH = "judgments"


class SaosClient:
    """Parses SAOS answers into the module's API models."""

    def __init__(self, egress: EgressClient, *, search_ttl: int, detail_max_bytes: int | None = None) -> None:
        """`detail_max_bytes` bounds the body of a judgment detail while it streams in (`None` = unbounded).

        A detail carries the whole text of the judgment, and the document store's size gate only sees it
        after the body has been parsed and converted; the host sets this to that gate's limit.
        """
        self._egress = egress
        self._search_ttl = search_ttl
        self._detail_max_bytes = detail_max_bytes

    @property
    def host(self) -> EgressHost:
        """The host this client talks to, with the pace it was registered with."""
        return self._egress.host

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

    async def _detail(self, source_id: str) -> Any:
        """The body of `judgments/{id}`, not cached; a missing judgment is a Polish `ValueError`."""
        try:
            return await self._egress.get_json(
                f"{DETAIL_PATH}/{source_id}", cache_ttl=None, max_bytes=self._detail_max_bytes
            )
        except ActNotFoundError as error:
            raise ValueError(f"Nie znaleziono orzeczenia o identyfikatorze {source_id} w SAOS.") from error

    async def judgment(self, source_id: str) -> SaosDetail:
        """Fetch one judgment detail (up to about 60 kB, most of it text this model does not parse).

        Not cached: a detail is read once per request, and keeping 60 kB bodies for a link is not worth
        the memory. `source_id` must already be validated as digits, because it becomes a path segment.

        Raises:
            ValueError: SAOS has no judgment with this identifier (Polish message).
            SejmApiError: The answer is not a judgment detail (named after the SAOS host). Transport
                and status errors come from the egress client unchanged.
        """
        raw = await self._detail(source_id)
        try:
            return SaosDetail.model_validate(raw)
        except ValidationError as error:
            raise SejmApiError(
                "Odpowiedź SAOS nie jest szczegółami orzeczenia.", source=self._egress.host.name
            ) from error

    async def judgment_with_text(self, source_id: str) -> SaosTextDetail:
        """Fetch one judgment detail and read its text as well (the same request as `judgment`).

        The text is what the judgment-reading path loads into the document store. Not cached, for the
        same reason as `judgment`. Same errors.
        """
        raw = await self._detail(source_id)
        try:
            return SaosTextDetail.model_validate(raw)
        except ValidationError as error:
            raise SejmApiError(
                "Odpowiedź SAOS nie jest szczegółami orzeczenia.", source=self._egress.host.name
            ) from error

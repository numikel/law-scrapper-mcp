"""The act source for the voivodeship journals, over one egress client per journal host."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from law_scrapper_mcp.client.egress import EgressClient
from law_scrapper_mcp.client.exceptions import ActNotFoundError, ApiUnavailableError, SejmApiError
from law_scrapper_mcp.config import Settings
from law_scrapper_mcp.models.tool_outputs import ActDetailOutput
from law_scrapper_mcp.services.act_sources import SourceSearchPage, SourceSearchQuery
from law_scrapper_mcp_voivodeship_journals import normalise
from law_scrapper_mcp_voivodeship_journals.catalog import Journal


class JournalActSource:
    """Serves the `POL_WOJ_*` publisher codes by asking the journal host of each code.

    Every journal has its own client (own pacing budget, breaker, cache space and identity), so an
    outage of one never slows or opens another. The module builds no HTTP client itself.
    """

    name = "POL_WOJ"

    def __init__(self, clients: Mapping[str, EgressClient], catalog: Sequence[Journal], settings: Settings) -> None:
        self._clients = dict(clients)
        self._catalog = {journal.code: journal for journal in catalog}
        self._search_ttl = settings.cache_search_ttl
        self._details_ttl = settings.cache_details_ttl

    def _client(self, publisher: str) -> EgressClient:
        """The client of a served journal, or the named refusal for a withheld or unknown code."""
        client = self._clients.get(publisher)
        if client is not None:
            return client
        journal = self._catalog.get(publisher)
        if journal is not None and journal.withheld is not None:
            raise ApiUnavailableError(
                f"Dziennik {publisher} ({journal.short_name}) jest wyłączony z obsługi: {journal.withheld}.",
                status_code=503,
                source=publisher,
            )
        served = ", ".join(sorted(self._clients))
        raise ValueError(f"Nieznany kod dziennika wojewódzkiego: {publisher}. Obsługiwane kody: {served}.")

    @staticmethod
    def _keywords(publisher: str, query: SourceSearchQuery) -> str | None:
        """The single keyword to send, or `None`; several keywords are refused.

        A recorded probe showed that a comma-joined list does not narrow the result the way the
        Sejm's AND does (`tests/fixtures/edziennik/PROVENANCE.md`), so passing it on would answer a
        different question than the one asked.
        """
        keywords = [keyword.strip() for keyword in query.keywords if keyword.strip()]
        if len(keywords) > 1:
            raise ValueError(
                f"Dziennik {publisher}: wiele słów kluczowych naraz nie jest obsługiwane — lista po przecinku "
                "nie zawęża wyników jak w DU i MP. Podaj jedno słowo kluczowe; dla logiki OR wykonaj "
                "osobne wyszukiwania."
            )
        return keywords[0] if keywords else None

    @staticmethod
    def _refuse_unsupported_filters(publisher: str, query: SourceSearchQuery) -> None:
        if query.in_force is not None:
            raise ValueError(
                f"Dziennik {publisher}: filtr 'in_force' nie jest obsługiwany — dziennik nie podaje, czy akt "
                "obowiązuje. Usuń ten parametr."
            )
        if query.date_from or query.date_to:
            names = " i ".join(
                name for name, value in (("date_from", query.date_from), ("date_to", query.date_to)) if value
            )
            raise ValueError(
                f"Dziennik {publisher}: filtr {names} (data wejścia w życie) nie jest obsługiwany. "
                "Okno czasu ustaw datami ogłoszenia: pub_date_from i pub_date_to."
            )

    async def search(self, publisher: str, query: SourceSearchQuery) -> SourceSearchPage:
        client = self._client(publisher)
        self._refuse_unsupported_filters(publisher, query)
        keyword = self._keywords(publisher, query)

        # `publisher` is never sent: the hosts ignore it, and every host answers for its own acts.
        params: dict[str, Any] = {"limit": query.limit}
        if query.offset:
            params["offset"] = query.offset
        if query.year is not None:
            params["year"] = query.year
        if query.title:
            params["title"] = query.title
        if query.act_type:
            params["type"] = query.act_type
        if keyword:
            params["keyword"] = keyword
        if query.pub_date_from:
            params["pubDateFrom"] = query.pub_date_from
        if query.pub_date_to:
            params["pubDateTo"] = query.pub_date_to

        data = normalise.camelise(await client.get_json("search", params=params, cache_ttl=self._search_ttl))
        if not isinstance(data, dict) or not isinstance(data.get("items", []), list):
            raise SejmApiError(
                f"Dziennik {publisher} zwrócił odpowiedź wyszukiwania w nieoczekiwanym kształcie.",
                source=publisher,
            )
        items = [normalise.summary(item) for item in data.get("items", []) if isinstance(item, dict)]
        total = data.get("totalCount")
        return SourceSearchPage(items=items, total_count=total if isinstance(total, int) else len(items))

    async def get_details(self, publisher: str, year: int, pos: int) -> ActDetailOutput:
        client = self._client(publisher)
        # A missing act is HTTP 204 with an empty body, not a 404.
        raw = await client.get_json(
            f"acts/{publisher}/{year}/{pos}", cache_ttl=self._details_ttl, none_on_no_content=True
        )
        if raw is None:
            raise ActNotFoundError(normalise.canonical_eli(publisher, year, pos), source=publisher)
        if not isinstance(raw, dict):
            raise SejmApiError(
                f"Dziennik {publisher} zwrócił szczegóły aktu w nieoczekiwanym kształcie.", source=publisher
            )
        return normalise.details(normalise.camelise(raw), publisher, year, pos)

    async def get_relationships(self, publisher: str, year: int, pos: int) -> dict[str, list[Any]]:
        client = self._client(publisher)
        raw = await client.get_json(f"acts/{publisher}/{year}/{pos}/references", none_on_no_content=True)
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            # A changed contract must be loud: an empty answer here would read as "no relations".
            raise SejmApiError(
                f"Dziennik {publisher} zwrócił powiązania aktu w nieoczekiwanym kształcie.", source=publisher
            )
        return normalise.relations(raw)

    async def get_pdf(self, publisher: str, year: int, pos: int, *, max_bytes: int) -> bytes:
        client = self._client(publisher)
        return await client.get_bytes(f"acts/{publisher}/{year}/{pos}/text.pdf", max_bytes=max_bytes)

    def pdf_url(self, publisher: str, year: int, pos: int) -> str:
        journal = self._catalog.get(publisher)
        if journal is None:
            raise ValueError(f"Nieznany kod dziennika wojewódzkiego: {publisher}.")
        return journal.pdf_url(publisher, year, pos)

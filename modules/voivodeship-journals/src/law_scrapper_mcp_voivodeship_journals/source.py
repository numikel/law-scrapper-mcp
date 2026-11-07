"""The act source for the voivodeship journals, over one egress client per journal host."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from law_scrapper_mcp.client.egress import EgressClient
from law_scrapper_mcp.client.exceptions import ActNotFoundError, ApiUnavailableError, SejmApiError
from law_scrapper_mcp.config import Settings
from law_scrapper_mcp.models.tool_outputs import ActDetailOutput
from law_scrapper_mcp.services.act_sources import (
    SourceSearchPage,
    SourceSearchQuery,
    UnknownPublisherError,
    fold_publisher_name,
    unknown_publisher_message,
)
from law_scrapper_mcp_voivodeship_journals import normalise
from law_scrapper_mcp_voivodeship_journals.catalog import Journal, names_index


class JournalActSource:
    """Serves the `POL_WOJ_*` publisher codes by asking the journal host of each code.

    Every journal has its own client (own pacing budget, breaker, cache space and identity), so an
    outage of one never slows or opens another. The module builds no HTTP client itself.
    """

    name = "POL_WOJ"

    def __init__(self, clients: Mapping[str, EgressClient], catalog: Sequence[Journal], settings: Settings) -> None:
        self._clients = dict(clients)
        self._catalog = {journal.code: journal for journal in catalog}
        self._names = {name: journal.code for name, journal in names_index(catalog).items()}
        self._infos = tuple(journal.publisher_info() for journal in catalog)
        self._search_ttl = settings.cache_search_ttl
        self._details_ttl = settings.cache_details_ttl

    def _resolve(self, publisher: str, *, by_name: bool) -> str:
        """The journal code `publisher` stands for, or the refusal for a value that stands for none.

        A code is taken as it is. A voivodeship name is accepted only where `by_name` is set, i.e. in
        the `publisher` parameter of the search tools. An ELI must carry the code: the core keys a
        loaded document by the ELI as typed, while every answer carries the canonical one, so an ELI
        with a name would load a document the next call (with the canonical ELI) cannot find.
        """
        if publisher in self._catalog:
            return publisher
        code = self._names.get(fold_publisher_name(publisher))
        if code is None:
            raise UnknownPublisherError(unknown_publisher_message(publisher, self._infos))
        if by_name:
            return code
        raise ValueError(
            f"W ELI podaj kod dziennika, a nie nazwę województwa: {self._catalog[code].voivodeship} = {code}, "
            f"np. {code}/2025/1."
        )

    def _client(self, publisher: str, *, by_name: bool = False) -> tuple[str, EgressClient]:
        """The journal code and the client of a served journal, or the named refusal for any other value."""
        code = self._resolve(publisher, by_name=by_name)
        client = self._clients.get(code)
        if client is not None:
            return code, client
        journal = self._catalog[code]
        raise ApiUnavailableError(
            f"Dziennik {code} ({journal.short_name}) jest wyłączony z obsługi: {journal.withheld}.",
            status_code=503,
            source=code,
        )

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
        code, client = self._client(publisher, by_name=True)
        self._refuse_unsupported_filters(code, query)
        keyword = self._keywords(code, query)

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
                f"Dziennik {code} zwrócił odpowiedź wyszukiwania w nieoczekiwanym kształcie.",
                source=code,
            )
        items = [normalise.summary(item) for item in data.get("items", []) if isinstance(item, dict)]
        total = data.get("totalCount")
        return SourceSearchPage(items=items, total_count=total if isinstance(total, int) else len(items))

    async def get_details(self, publisher: str, year: int, pos: int) -> ActDetailOutput:
        code, client = self._client(publisher)
        # A missing act is HTTP 204 with an empty body, not a 404.
        raw = await client.get_json(f"acts/{code}/{year}/{pos}", cache_ttl=self._details_ttl, none_on_no_content=True)
        if raw is None:
            raise ActNotFoundError(normalise.canonical_eli(code, year, pos), source=code)
        if not isinstance(raw, dict):
            raise SejmApiError(f"Dziennik {code} zwrócił szczegóły aktu w nieoczekiwanym kształcie.", source=code)
        return normalise.details(normalise.camelise(raw), code, year, pos)

    async def get_relationships(self, publisher: str, year: int, pos: int) -> dict[str, list[Any]]:
        code, client = self._client(publisher)
        raw = await client.get_json(f"acts/{code}/{year}/{pos}/references", none_on_no_content=True)
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            # A changed contract must be loud: an empty answer here would read as "no relations".
            raise SejmApiError(f"Dziennik {code} zwrócił powiązania aktu w nieoczekiwanym kształcie.", source=code)
        return normalise.relations(raw)

    async def get_pdf(self, publisher: str, year: int, pos: int, *, max_bytes: int) -> bytes:
        code, client = self._client(publisher)
        return await client.get_bytes(f"acts/{code}/{year}/{pos}/text.pdf", max_bytes=max_bytes)

    def pdf_url(self, publisher: str, year: int, pos: int) -> str:
        code = self._resolve(publisher, by_name=False)
        return self._catalog[code].pdf_url(code, year, pos)

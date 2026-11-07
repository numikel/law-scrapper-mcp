"""Pluggable sources of legal acts.

The core serves the Sejm's Dziennik Ustaw (`DU`) and Monitor Polski (`MP`) itself. A source module
can claim further publisher codes by registering an `ActSource`; the services then ask the registry
once per call and hand the request to the source instead of the Sejm client. A source can also
describe its publishers (`PublisherInfo`): the name it gives is accepted in place of the code, and
the descriptions feed the metadata listing and the refusal of an unknown publisher. With nothing
registered every service takes its original code path.

The protocol lives in `services/` because it returns `models` types and must be importable by the
services without a cycle through `source_modules`.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from law_scrapper_mcp.client.exceptions import LawScrapperError
from law_scrapper_mcp.models.tool_outputs import ActDetailOutput, ActSummaryOutput

# Publisher codes the core serves through the Sejm client. A source cannot take them over.
RESERVED_PUBLISHERS: frozenset[str] = frozenset({"DU", "MP"})

# The caller's value is echoed in a refusal; the whole message must still fit the tool error
# budget (`error_message_max_chars`, 500 by default) next to a full list of valid publishers.
UNKNOWN_PUBLISHER_VALUE_LIMIT = 40

# A publisher name longer than this is neither registered nor looked up: a longer value cannot be a
# name, so it is not even normalised (the fold would otherwise run over an unbounded caller value).
MAX_PUBLISHER_NAME_LENGTH = 100

_NAME_SEPARATORS = re.compile(r"[\s_\-]+")


class ActSourceConflictError(LawScrapperError):
    """A source tried to claim a publisher code or prefix that is reserved or already claimed."""


class UnknownPublisherError(ValueError):
    """The publisher is neither DU/MP nor claimed by any registered source.

    A `ValueError`, so the tool layer reports it as a validation error with the Polish text below.
    """


def fold_publisher_name(text: str) -> str:
    """Reduce a publisher name to the form names are compared in.

    Case-folded, without Polish diacritics (`ł` has no decomposition, so it is mapped by hand),
    and with every run of whitespace, underscores and hyphens turned into a single hyphen. So
    `Łódzkie`, `lodzkie` and `KUJAWSKO_POMORSKIE` match `łódzkie` and `kujawsko-pomorskie`.
    """
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return _NAME_SEPARATORS.sub("-", stripped.replace("ł", "l")).strip("-")


@dataclass(frozen=True, slots=True)
class PublisherInfo:
    """What a source tells the agent about one publisher code it serves.

    Attributes:
        code: The publisher code; must be one the source registers.
        label: Human name of the publisher. Also accepted in place of the code, and shown in the
            refusal for an unknown publisher.
        available: `False` when the code is listed but the source does not serve it right now.
            Such a publisher is never offered as a valid choice.
        item: The entry merged unchanged into the `publishers` metadata category. The source
            chooses its keys; the core does not interpret them.
    """

    code: str
    label: str
    available: bool
    item: Mapping[str, Any]


def unknown_publisher_message(value: str, infos: Iterable[PublisherInfo], *, undescribed: Iterable[str] = ()) -> str:
    """The Polish refusal for an unknown publisher, listing what would have been valid.

    Only available publishers are offered. `undescribed` are codes a source serves without having
    described them; they are valid too, and are listed as bare codes after the described ones. The
    echoed `value` is whitespace-collapsed, stripped of non-printable characters (control and
    zero-width marks) and capped at `UNKNOWN_PUBLISHER_VALUE_LIMIT` characters, and the hint comes
    before the list, so a cut by the tool layer loses the tail of the list and never the hint.
    """
    available = sorted((info for info in infos if info.available), key=lambda info: info.code)
    shown = "".join(char for char in " ".join(value.split()) if char.isprintable())
    if len(shown) > UNKNOWN_PUBLISHER_VALUE_LIMIT:
        shown = f"{shown[:UNKNOWN_PUBLISHER_VALUE_LIMIT]}…"
    parts = [f"Nieznany wydawca: {shown}."]
    if available:
        parts.append(f"Zamiast kodu można podać nazwę, np. {available[0].label}.")
    valid = [
        *sorted(RESERVED_PUBLISHERS),
        *(f"{info.code} ({info.label})" for info in available),
        *sorted(undescribed),
    ]
    parts.append(f"Poprawne: {', '.join(valid)}.")
    return " ".join(parts)


@dataclass(frozen=True, slots=True)
class SourceSearchQuery:
    """One search the services ask a source to run.

    Field names follow the tool parameters. `date_from`/`date_to` filter by the date an act entered
    into force, `pub_date_from`/`pub_date_to` by the date it was announced. A source that cannot
    honour a filter raises a Polish `ValueError` naming it, instead of ignoring the filter.

    Attributes:
        year: Publication year, if any.
        keywords: Keywords as the caller gave them.
        title: Substring of the title.
        act_type: Exact document type.
        date_from: Entry-into-force date, lower bound (`YYYY-MM-DD`).
        date_to: Entry-into-force date, upper bound.
        pub_date_from: Announcement date, lower bound.
        pub_date_to: Announcement date, upper bound.
        in_force: Filter on whether the act is in force.
        limit: Page size, 1..100 (the services enforce the range).
        offset: Number of records to skip.
    """

    year: int | None = None
    keywords: tuple[str, ...] = ()
    title: str | None = None
    act_type: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    pub_date_from: str | None = None
    pub_date_to: str | None = None
    in_force: bool | None = None
    limit: int = 20
    offset: int = 0


@dataclass(frozen=True, slots=True)
class SourceSearchPage:
    """One page of a source's search answer.

    Attributes:
        items: The records of the requested window, already in the tool output shape.
        total_count: Size of the whole answer, not of this page.
    """

    items: list[ActSummaryOutput]
    total_count: int


class ActSource(Protocol):
    """What a source of acts must answer.

    Raises, by convention: `ValueError` (Polish text) for an input the source cannot honour,
    `ActNotFoundError` when the act does not exist, and the `ApiUnavailableError` family with
    `source=` set when the host fails.
    """

    name: str
    """Label of the egress host the source talks to; used in messages."""

    async def search(self, publisher: str, query: SourceSearchQuery) -> SourceSearchPage:
        """Run a search and return the requested window."""
        ...

    async def get_details(self, publisher: str, year: int, pos: int) -> ActDetailOutput:
        """Return the metadata of one act; content flags and the table of contents stay default."""
        ...

    async def get_relationships(self, publisher: str, year: int, pos: int) -> dict[str, list[Any]]:
        """Return the relations of one act under the source's own keys; `{}` for none."""
        ...

    async def get_pdf(self, publisher: str, year: int, pos: int, *, max_bytes: int) -> bytes:
        """Download the act's PDF, aborting once the body passes `max_bytes`."""
        ...

    def pdf_url(self, publisher: str, year: int, pos: int) -> str:
        """Public address of the act's PDF."""
        ...


class ActSourceRegistry:
    """Who serves which publisher code.

    Lookup is exact code first, then a source's publisher name (see `PublisherInfo`), then prefix
    (longest prefix wins). Once any source is registered, a value that none of them claims and that
    is not `DU`/`MP` is refused with the list of valid publishers; with nothing registered every
    value is left to the Sejm as before. Registration happens in the server lifespan before the
    first request and unregistration at shutdown, with no `await` in between, so plain dicts are
    enough: no lock is needed.
    """

    def __init__(self) -> None:
        self._by_code: dict[str, ActSource] = {}
        self._by_prefix: dict[str, ActSource] = {}
        self._info: dict[str, PublisherInfo] = {}
        self._aliases: dict[str, str] = {}

    def register(
        self,
        source: ActSource,
        *,
        publishers: Collection[str],
        prefixes: Collection[str] = (),
        info: Collection[PublisherInfo] = (),
    ) -> None:
        """Claim publisher codes and prefixes for `source`, and optionally describe the codes.

        Raises:
            ActSourceConflictError: A code is `DU` or `MP`, a code or prefix is already claimed, a
                prefix would swallow a reserved code, an `info` entry describes a code that is not
                registered here, or a label is empty, repeated, reserved or already in use. Nothing
                is registered in that case.
        """
        codes = list(publishers)
        prefix_list = list(prefixes)
        for code in codes:
            if code in RESERVED_PUBLISHERS:
                raise ActSourceConflictError(
                    f"Kod wydawcy {code} jest obsługiwany przez rdzeń i nie może być przejęty."
                )
            if code in self._by_code:
                raise ActSourceConflictError(
                    f"Kod wydawcy {code} jest już zajęty przez źródło {self._by_code[code].name}."
                )
            if fold_publisher_name(code) in self._aliases:
                raise ActSourceConflictError(
                    f"Kod wydawcy {code} pokrywa się z nazwą innego wydawcy i nie może jej zasłonić."
                )
        for prefix in prefix_list:
            if not prefix:
                raise ActSourceConflictError("Prefiks kodu wydawcy nie może być pusty.")
            if any(reserved.startswith(prefix) for reserved in RESERVED_PUBLISHERS):
                raise ActSourceConflictError(
                    f"Prefiks {prefix} obejmuje kod obsługiwany przez rdzeń ({', '.join(sorted(RESERVED_PUBLISHERS))})."
                )
            if prefix in self._by_prefix:
                raise ActSourceConflictError(
                    f"Prefiks {prefix} jest już zajęty przez źródło {self._by_prefix[prefix].name}."
                )
        descriptions = list(info)
        aliases = self._checked_aliases(codes, descriptions)
        for code in codes:
            self._by_code[code] = source
        for prefix in prefix_list:
            self._by_prefix[prefix] = source
        for entry in descriptions:
            self._info[entry.code] = entry
        self._aliases.update(aliases)

    def _checked_aliases(self, codes: list[str], info: list[PublisherInfo]) -> dict[str, str]:
        """Validate the descriptions of one registration and return their `folded label -> code` aliases."""
        taken_codes = {fold_publisher_name(code) for code in [*self._by_code, *codes, *RESERVED_PUBLISHERS]}
        aliases: dict[str, str] = {}
        for entry in info:
            if entry.code not in codes:
                raise ActSourceConflictError(
                    f"Opis wydawcy dotyczy kodu {entry.code}, którego to źródło nie zgłosiło do rejestracji."
                )
            if entry.code in aliases.values():
                raise ActSourceConflictError(f"Kod wydawcy {entry.code} ma w rejestracji więcej niż jeden opis.")
            if len(entry.label) > MAX_PUBLISHER_NAME_LENGTH:
                raise ActSourceConflictError(
                    f"Nazwa wydawcy {entry.code} jest dłuższa niż {MAX_PUBLISHER_NAME_LENGTH} znaków."
                )
            folded = fold_publisher_name(entry.label)
            if not folded:
                raise ActSourceConflictError(f"Nazwa wydawcy {entry.code} nie może być pusta.")
            if folded in taken_codes:
                raise ActSourceConflictError(
                    f"Nazwa wydawcy {entry.label} ({entry.code}) pokrywa się z kodem wydawcy i nie może być aliasem."
                )
            if folded in self._aliases or folded in aliases:
                raise ActSourceConflictError(f"Nazwa wydawcy {entry.label} ({entry.code}) jest już zajęta.")
            aliases[folded] = entry.code
        return aliases

    def unregister(self, source: ActSource) -> None:
        """Release every code, prefix, description and name `source` holds. Unknown sources are ignored."""
        released = {code for code, held in self._by_code.items() if held is source}
        self._by_code = {code: held for code, held in self._by_code.items() if held is not source}
        self._by_prefix = {prefix: held for prefix, held in self._by_prefix.items() if held is not source}
        self._info = {code: entry for code, entry in self._info.items() if code not in released}
        self._aliases = {name: code for name, code in self._aliases.items() if code not in released}

    def for_publisher(self, code: str) -> ActSource | None:
        """Return the source serving `code`, or `None` when the Sejm path applies.

        Raises:
            UnknownPublisherError: Some source is registered, and `code` is neither `DU`/`MP` (in
                any letter case, ignoring surrounding whitespace) nor a code, name or prefix one of
                them claims.
        """
        if not self._by_code and not self._by_prefix:
            return None
        source = self._by_code.get(code)
        if source is not None:
            return source
        if len(code) <= MAX_PUBLISHER_NAME_LENGTH:
            alias_target = self._aliases.get(fold_publisher_name(code))
            if alias_target is not None:
                return self._by_code[alias_target]
        for prefix in sorted(self._by_prefix, key=len, reverse=True):
            if code.startswith(prefix):
                return self._by_prefix[prefix]
        if code.strip().upper() in RESERVED_PUBLISHERS:
            return None
        undescribed = [registered for registered in self._by_code if registered not in self._info]
        raise UnknownPublisherError(unknown_publisher_message(code, self.describe(), undescribed=undescribed))

    def publishers(self) -> tuple[str, ...]:
        """Exact publisher codes currently registered, sorted."""
        return tuple(sorted(self._by_code))

    def describe(self) -> tuple[PublisherInfo, ...]:
        """The descriptions registered sources gave of their publishers, sorted by code."""
        return tuple(self._info[code] for code in sorted(self._info))

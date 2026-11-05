"""Conversion of SAOS search items into the core's source-neutral judgment record."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from urllib.parse import urlsplit

from law_scrapper_mcp.models.tool_outputs import JudgmentSummaryOutput
from law_scrapper_mcp_saos.models import SaosDetailData, SaosRecord, SaosSearchItem

SOURCE = "saos"
PORTAL_BASE = "https://www.saos.org.pl/judgments/"
EXCERPT_LIMIT = 800
PORTAL_URL_LIMIT = 2048
NO_SIGNATURE = "brak sygnatury"
NO_DATE = "brak daty"
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

# SAOS court types, in the order they are documented to the agent.
COURT_TYPES: tuple[str, ...] = (
    "COMMON",
    "SUPREME",
    "CONSTITUTIONAL_TRIBUNAL",
    "NATIONAL_APPEAL_CHAMBER",
    "ADMINISTRATIVE",
)

# Court name for the types whose records carry no court of their own. Common courts name theirs in
# `division.court.name`; `COMMON_COURT_FALLBACK` covers a record that lacks it.
COURT_LABELS: dict[str, str] = {
    "SUPREME": "Sąd Najwyższy",
    "CONSTITUTIONAL_TRIBUNAL": "Trybunał Konstytucyjny",
    "NATIONAL_APPEAL_CHAMBER": "Krajowa Izba Odwoławcza",
    "ADMINISTRATIVE": "Sąd administracyjny",
}
COMMON_COURT_FALLBACK = "Sąd powszechny"

# SAOS `judgmentType` values and the Polish label a record shows. `REASONS` is a separate record of
# the same case, holding the written reasoning; the operative part is the `SENTENCE`.
JUDGMENT_TYPE_LABELS: dict[str, str] = {
    "SENTENCE": "Wyrok",
    "DECISION": "Postanowienie",
    "RESOLUTION": "Uchwała",
    "REGULATION": "Zarządzenie",
    "REASONS": "Uzasadnienie",
}


def judgment_type_label(saos_type: str) -> str:
    """Polish label of a SAOS judgment type; an unknown value is kept as it came."""
    return JUDGMENT_TYPE_LABELS.get(saos_type, saos_type)


def saos_judgment_type(value: str) -> str | None:
    """SAOS name of a judgment type given as a Polish label or as a SAOS name, or `None` if unknown."""
    folded = value.strip().casefold()
    for saos_name, label in JUDGMENT_TYPE_LABELS.items():
        if folded in (saos_name.casefold(), label.casefold()):
            return saos_name
    return None


# Common-court divisions are numbered by type: the code ends in 15 (`Pracy`), 21 (`Pracy i Ubezpieczeń
# Społecznych`) or 18 (`Ubezpieczeń Społecznych`, deliberately counted as labour). The pattern is
# the one the agent is told to give `filter_results` (field `division_code`), and `is_labour_division` is the
# same rule as code; a test pins that the two agree on every recorded division code.
LABOUR_DIVISION_PATTERN = "(15|18|21)$"
LABOUR_DIVISION_SUFFIXES: tuple[str, ...] = ("15", "18", "21")


def is_labour_division(division_code: str | None) -> bool:
    """Whether a common-court division code denotes a labour (or social insurance) division."""
    return division_code is not None and division_code.endswith(LABOUR_DIVISION_SUFFIXES)


@dataclass(frozen=True, slots=True)
class LabourRule:
    """How one court type decides that a record is a labour judgment.

    A court type has a rule here or it is refused by `labour_rule_for`; the scan itself knows nothing about
    divisions or chambers. Adding a court type means adding one entry to `LABOUR_RULES`.
    """

    description: str
    matches: Callable[[SaosSearchItem], bool]


def _common_court_is_labour(item: SaosSearchItem) -> bool:
    return is_labour_division(item.division.code if item.division else None)


# The Supreme Court has no division codes; its records name chambers. The chamber SAOS stores for labour cases is
# `Izba Pracy, Ubezpieczeń Społecznych i Spraw Publicznych` (recorded in `supreme_chambers_metadata_2016.json`),
# so the rule is a prefix: a chamber whose name starts with `Izba Pracy`. The prefix also covers the spelling
# `Izba Pracy i Ubezpieczeń Społecznych`, which no recorded SAOS record carries. The mapped `division` of a
# Supreme Court record lists the chamber names first, so `filter_results` narrows with the same words.
LABOUR_CHAMBER_PATTERN = "Izba Pracy"


def is_labour_chamber(chamber_names: Iterable[str]) -> bool:
    """Whether any chamber name of a Supreme Court record starts with the labour chamber's name."""
    prefix = LABOUR_CHAMBER_PATTERN.casefold()
    return any(name.casefold().startswith(prefix) for name in chamber_names)


def _supreme_court_is_labour(item: SaosSearchItem) -> bool:
    return item.division is not None and is_labour_chamber(c.name for c in item.division.chambers if c.name)


LABOUR_RULES: dict[str, LabourRule] = {
    "COMMON": LabourRule(
        description=(
            "Sądy powszechne: orzeczenie zaliczone, gdy kod wydziału kończy się na 15 (pracy), 21 (pracy i "
            f"ubezpieczeń społecznych) lub 18 (ubezpieczeń społecznych), czyli wzorzec {LABOUR_DIVISION_PATTERN} "
            "na polu division_code w filter_results. Wzorzec (15|21)$ pomija wydziały wyłącznie ubezpieczeń "
            "społecznych. Rekord bez kodu wydziału nie jest zaliczany."
        ),
        matches=_common_court_is_labour,
    ),
    "SUPREME": LabourRule(
        description=(
            f"Sąd Najwyższy: orzeczenie zaliczone, gdy nazwa którejkolwiek izby zaczyna się od '{LABOUR_CHAMBER_PATTERN}' "
            "(w SAOS: 'Izba Pracy, Ubezpieczeń Społecznych i Spraw Publicznych'; ta izba rozpoznaje też sprawy "
            f"publiczne). W filter_results to field='division', pattern='{LABOUR_CHAMBER_PATTERN}'. SAOS ma orzeczenia "
            "Sądu Najwyższego tylko do 2016-06-22."
        ),
        matches=_supreme_court_is_labour,
    ),
}


def labour_rule_for(court_type: str) -> LabourRule:
    """The labour rule of a court type.

    Raises:
        ValueError: The court type has no rule (Polish message): SAOS gives the other courts neither a
            division type nor a chamber to classify by.
    """
    rule = LABOUR_RULES.get(court_type)
    if rule is not None:
        return rule
    raise ValueError(
        f"Parametr 'labour_only' działa tylko dla sądów powszechnych (court_type='COMMON' lub brak) i Sądu "
        f"Najwyższego (court_type='SUPREME'): dla '{court_type}' SAOS nie podaje rodzaju wydziału ani izby, więc "
        "orzeczeń pracy nie da się wybrać."
    )


def portal_url_of(source_id: int | str) -> str:
    """Address of a judgment on the SAOS site (a documented pattern, not a field of the API)."""
    return f"{PORTAL_BASE}{source_id}"


def _signature(item: SaosRecord) -> str:
    numbers = [case.case_number for case in item.court_cases if case.case_number]
    return ", ".join(numbers) if numbers else NO_SIGNATURE


def _court(item: SaosRecord) -> str:
    if item.court_type == "COMMON":
        court = item.division.court if item.division else None
        return court.name if court is not None and court.name else COMMON_COURT_FALLBACK
    return COURT_LABELS.get(item.court_type, item.court_type or COMMON_COURT_FALLBACK)


def _division(item: SaosRecord) -> str | None:
    """Division name; for the Supreme Court the chamber names come first (`Izba Karna / Wydział III`)."""
    division = item.division
    if division is None:
        return None
    chambers = [chamber.name for chamber in division.chambers if chamber.name]
    if chambers and division.name:
        return f"{', '.join(chambers)} / {division.name}"
    if chambers:
        return ", ".join(chambers)
    return division.name


def _date(item: SaosRecord) -> str | None:
    value = item.judgment_date
    return value if value is not None and _ISO_DATE.fullmatch(value) else None


def _record(item: SaosRecord, *, excerpt: str | None, portal_url: str | None = None) -> JudgmentSummaryOutput:
    signature = _signature(item)
    court = _court(item)
    label = judgment_type_label(item.judgment_type)
    date = _date(item)
    return JudgmentSummaryOutput(
        source=SOURCE,
        source_id=str(item.id),
        signature=signature,
        title=f"{signature} | {label} | {court} | {date or NO_DATE}",
        court=court,
        court_type=item.court_type or None,
        division=_division(item),
        division_code=item.division.code if item.division and item.division.code else None,
        judgment_type=label,
        judgment_date=date,
        source_url=portal_url_of(item.id),
        portal_url=portal_url,
        ecli=None,
        excerpt=excerpt,
    )


def to_judgment(item: SaosSearchItem) -> JudgmentSummaryOutput:
    """Map one search item. Judges are never mapped: the record carries an excerpt, not the people."""
    return _record(item, excerpt=item.text_content[:EXCERPT_LIMIT] if item.text_content else None)


def portal_url_from(address: str | None) -> str | None:
    """The court portal address as an output, or `None` when it is not safe to hand on.

    The address is remote data. It is only ever passed on as text and never fetched, and only an `http`
    or `https` address of at most `PORTAL_URL_LIMIT` characters, without whitespace or control
    characters, is passed on.
    """
    if address is None or not address or len(address) > PORTAL_URL_LIMIT:
        return None
    if any(char.isspace() or ord(char) < 0x20 or ord(char) == 0x7F for char in address):
        return None
    try:
        parts = urlsplit(address)
        _ = parts.port  # raises ValueError for a malformed port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.netloc or not parts.hostname:
        return None
    return address


def to_detail(data: SaosDetailData) -> tuple[JudgmentSummaryOutput, str | None]:
    """Map a judgment detail to the record (no excerpt, with the portal address) and the division type."""
    address = portal_url_from(data.source.judgment_url if data.source else None)
    division_type = data.division.type if data.division and data.division.type else None
    return _record(data, excerpt=None, portal_url=address), division_type

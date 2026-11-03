"""Conversion of SAOS search items into the core's source-neutral judgment record."""

from __future__ import annotations

import re
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

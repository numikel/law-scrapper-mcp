"""Conversion of SAOS search items into the core's source-neutral judgment record."""

from __future__ import annotations

import re

from law_scrapper_mcp.models.tool_outputs import JudgmentSummaryOutput
from law_scrapper_mcp_saos.models import SaosSearchItem

SOURCE = "saos"
PORTAL_BASE = "https://www.saos.org.pl/judgments/"
EXCERPT_LIMIT = 800
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


def _signature(item: SaosSearchItem) -> str:
    numbers = [case.case_number for case in item.court_cases if case.case_number]
    return ", ".join(numbers) if numbers else NO_SIGNATURE


def _court(item: SaosSearchItem) -> str:
    if item.court_type == "COMMON":
        court = item.division.court if item.division else None
        return court.name if court is not None and court.name else COMMON_COURT_FALLBACK
    return COURT_LABELS.get(item.court_type, item.court_type or COMMON_COURT_FALLBACK)


def _division(item: SaosSearchItem) -> str | None:
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


def _date(item: SaosSearchItem) -> str | None:
    value = item.judgment_date
    return value if value is not None and _ISO_DATE.fullmatch(value) else None


def to_judgment(item: SaosSearchItem) -> JudgmentSummaryOutput:
    """Map one search item. Judges are never mapped: the record carries an excerpt, not the people."""
    signature = _signature(item)
    court = _court(item)
    label = judgment_type_label(item.judgment_type)
    date = _date(item)
    excerpt = item.text_content[:EXCERPT_LIMIT] if item.text_content else None
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
        ecli=None,
        excerpt=excerpt,
    )

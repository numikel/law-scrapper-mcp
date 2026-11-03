"""Wire quirks of the e-Dziennik ELI engine, in one place and without I/O.

Every fact below was recorded in `tests/fixtures/edziennik/` (see its `PROVENANCE.md`):

- Two JSON casings: most hosts answer in camelCase, some in PascalCase. Lower-casing the first
  letter of every key makes them equal.
- `/search` and the act detail swap the meaning of `promulgation` and `announcementDate`. In a
  search item `announcementDate` is the publication timestamp and `promulgation` the act date;
  the act detail keeps the Sejm order (`promulgation` is the publication timestamp,
  `announcementDate` the act date).
- Missing dates are the sentinel `0001-01-01T00:00:00`, an empty string or `null`.
- The act detail carries no `eli` (or a `null` one), so the canonical ELI is built from the
  request, never from the host's `eli` or `address` fields.
- The journals keep no legal-status data, so the effective date and `in_force` stay empty.
"""

from __future__ import annotations

import re
from typing import Any

from law_scrapper_mcp.models.tool_outputs import ActDetailOutput, ActSummaryOutput

_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_SENTINEL_YEAR = "0001-"


def camelise(value: Any) -> Any:
    """Lower-case the first letter of every key, recursively through dicts and lists."""
    if isinstance(value, dict):
        return {
            (key[:1].lower() + key[1:] if isinstance(key, str) else key): camelise(item) for key, item in value.items()
        }
    if isinstance(value, list):
        return [camelise(item) for item in value]
    return value


def to_date(value: Any) -> str | None:
    """Calendar day (`YYYY-MM-DD`) of a wire timestamp; `None` for a sentinel, an empty or a bad value."""
    if not isinstance(value, str) or value.startswith(_SENTINEL_YEAR):
        return None
    day = value[:10]
    return day if _DATE.fullmatch(day) else None


def canonical_eli(publisher: str, year: int, pos: int) -> str:
    """The ELI in the form every tool takes: `{publisher}/{year}/{pos}`."""
    return f"{publisher}/{year}/{pos}"


def summary(item: dict[str, Any]) -> ActSummaryOutput:
    """One `/search` item (already camelised) as a tool summary.

    The publication date goes to `promulgation_date`, which is what the field means for the Sejm.
    The journal's own `promulgation` is the act date and is not exposed.
    """
    publisher = str(item.get("publisher") or "")
    year = int(item.get("year") or 0)
    pos = int(item.get("pos") or 0)
    return ActSummaryOutput(
        eli=canonical_eli(publisher, year, pos),
        publisher=publisher,
        year=year,
        pos=pos,
        title=str(item.get("title") or ""),
        status=str(item.get("status") or ""),
        type=item.get("type") or None,
        promulgation_date=to_date(item.get("announcementDate")),
        effective_date=None,
        in_force=None,
    )


def details(raw: dict[str, Any], publisher: str, year: int, pos: int) -> ActDetailOutput:
    """An act detail (already camelised) as a tool detail, keyed by the requested act."""
    keywords = raw.get("keywords")
    volume = raw.get("volume")
    return ActDetailOutput(
        eli=canonical_eli(publisher, year, pos),
        publisher=publisher,
        year=year,
        pos=pos,
        title=str(raw.get("title") or ""),
        status=str(raw.get("status") or ""),
        type=raw.get("type") or None,
        announcement_date=to_date(raw.get("announcementDate")),
        promulgation_date=to_date(raw.get("promulgation")),
        entry_into_force=to_date(raw.get("entryIntoForce")),
        valid_from=to_date(raw.get("validFrom")),
        repeal_date=to_date(raw.get("repealDate")),
        change_date=to_date(raw.get("changeDate")),
        keywords=[str(keyword) for keyword in keywords] if isinstance(keywords, list) else [],
        references=None,
        volume=volume if isinstance(volume, int) else None,
        has_pdf=bool(raw.get("textPDF")),
        has_html=bool(raw.get("textHTML")),
    )


def _relation_entry(entry: Any) -> Any:
    """One relation entry: its nested `act` is an act record, so it is camelised and its dates cleaned."""
    if not isinstance(entry, dict):
        return entry
    cleaned = {key[:1].lower() + key[1:]: value for key, value in entry.items()}
    act = cleaned.get("act")
    if isinstance(act, dict):
        act = camelise(act)
        for key in ("promulgation", "announcementDate", "changeDate"):
            if key in act:
                act[key] = to_date(act[key])
        cleaned["act"] = act
    if "date" in cleaned:
        cleaned["date"] = to_date(cleaned["date"])
    return cleaned


def relations(raw: dict[str, Any]) -> dict[str, list[Any]]:
    """Relations of an act, keyed by the journal's own relation description (kept verbatim)."""
    return {
        key: [_relation_entry(entry) for entry in (value if isinstance(value, list) else [value])]
        for key, value in raw.items()
    }

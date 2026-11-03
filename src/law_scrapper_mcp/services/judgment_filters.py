"""Shared `filter_results` filters for sets of court judgments.

The result store keeps acts and judgments side by side but filters them with different field
vocabularies. Keeping the judgment side here means `result_store.py` and `response_enrichment.py`
carry no judgment logic, and the act code path stays exactly as it was.

Unlike the act path, which silently falls back to `title` for an unknown field, every invalid
choice here is a `ValueError` that lists the valid values: a judgment set is new, so there is no
legacy behaviour to preserve and a typo should not look like "no match".
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from law_scrapper_mcp.models.tool_outputs import FilterOutput, Hint, JudgmentSummaryOutput
from law_scrapper_mcp.services.pattern_matching import CompiledPattern

__all__ = [
    "JUDGMENT_DATE_FIELDS",
    "JUDGMENT_SEARCHABLE_FIELDS",
    "JUDGMENT_SORT_FIELDS",
    "apply_judgment_filters",
    "judgment_filter_hints",
    "reject_act_only_filters",
]

# Public field name -> attribute on `JudgmentSummaryOutput`. `type` is the name `filter_results`
# already uses for the kind of document, so it is kept as an alias of `judgment_type`.
_FIELD_ATTRIBUTES: dict[str, str] = {
    "title": "title",
    "signature": "signature",
    "court": "court",
    "type": "judgment_type",
    "division": "division",
    "division_code": "division_code",
    "excerpt": "excerpt",
    "judgment_date": "judgment_date",
}

JUDGMENT_SEARCHABLE_FIELDS: tuple[str, ...] = (
    "title",
    "signature",
    "court",
    "type",
    "division",
    "division_code",
    "excerpt",
)
JUDGMENT_DATE_FIELDS: tuple[str, ...] = ("judgment_date",)
JUDGMENT_SORT_FIELDS: tuple[str, ...] = ("title", "signature", "court", "type", "judgment_date")


def _quoted(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _require_choice(value: str, valid: Sequence[str], *, parameter: str) -> str:
    if value not in valid:
        raise ValueError(
            f"Parametr '{parameter}' ma wartość '{value}', która nie dotyczy orzeczeń. Dozwolone: {_quoted(valid)}."
        )
    return value


def reject_act_only_filters(status_equals: str | None) -> None:
    """Refuse the filters that only make sense for acts.

    Raises:
        ValueError: `status_equals` was given for a set of judgments.
    """
    if status_equals:
        raise ValueError(
            "Parametr 'status_equals' dotyczy tylko aktów prawnych (status obowiązywania), a ten zestaw "
            "zawiera orzeczenia. Użyj 'type_equals' (rodzaj orzeczenia), 'division' albo 'court'."
        )


def _field_value(record: JudgmentSummaryOutput, field: str) -> Any:
    return getattr(record, _FIELD_ATTRIBUTES[field])


def _year_of(judgment_date: str | None) -> int | None:
    if not judgment_date or len(judgment_date) < 4 or not judgment_date[:4].isdigit():
        return None
    return int(judgment_date[:4])


def apply_judgment_filters(
    records: Sequence[JudgmentSummaryOutput],
    *,
    compiled_pattern: CompiledPattern | None = None,
    field: str = "title",
    type_equals: str | None = None,
    year_equals: int | None = None,
    date_field: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    sort_by: str | None = None,
    sort_desc: bool = False,
) -> list[JudgmentSummaryOutput]:
    """Apply every shared filter to a set of judgments, in memory.

    The order matches the act path: exact matches, then the pattern, then the date range, then the
    sort. A record missing the filtered value never matches (a `None` date is outside every range).

    Args:
        records: The stored judgments. Never mutated.
        compiled_pattern: Already compiled and validated by the caller (the store owns the limits).
        field: Field the pattern is matched against. Only checked when a pattern is given.
        type_equals: Exact `judgment_type` label, for example 'Wyrok'.
        year_equals: Year of `judgment_date`.
        date_field: Date field for the range. Only `judgment_date` exists for judgments.
        date_from: Inclusive lower bound (ISO date).
        date_to: Inclusive upper bound (ISO date).
        sort_by: One of `JUDGMENT_SORT_FIELDS`.
        sort_desc: Sort descending.

    Raises:
        ValueError: `field`, `date_field` or `sort_by` is not valid for judgments.
    """
    if compiled_pattern is not None:
        _require_choice(field, JUDGMENT_SEARCHABLE_FIELDS, parameter="field")
    if date_field:
        _require_choice(date_field, JUDGMENT_DATE_FIELDS, parameter="date_field")
    if sort_by:
        _require_choice(sort_by, JUDGMENT_SORT_FIELDS, parameter="sort_by")

    filtered = list(records)

    if type_equals is not None:
        filtered = [r for r in filtered if r.judgment_type == type_equals]

    if year_equals is not None:
        filtered = [r for r in filtered if _year_of(r.judgment_date) == year_equals]

    if compiled_pattern is not None:
        filtered = [
            r
            for r in filtered
            if (value := _field_value(r, field)) is not None and compiled_pattern.search(str(value)) is not None
        ]

    if date_field and (date_from or date_to):
        in_range: list[JudgmentSummaryOutput] = []
        for record in filtered:
            value = record.judgment_date
            if value is None:
                continue
            if date_from and value < date_from:
                continue
            if date_to and value > date_to:
                continue
            in_range.append(record)
        filtered = in_range

    if sort_by:
        sort_field: str = sort_by
        filtered = sorted(filtered, key=lambda r: _field_value(r, sort_field) or "", reverse=sort_desc)

    return filtered


def judgment_filter_hints(output: FilterOutput, *, filter_max_records: int) -> list[Hint]:
    """Hints for a filtered set of judgments.

    Names no source tool on purpose: the core cannot know which module produced the set. The record's
    own `source` field says which source's tools handle it.
    """
    hints: list[Hint] = []
    if output.judgments:
        first = output.judgments[0]
        hints.append(
            Hint(
                message=(
                    f"Szczegóły orzeczenia (m.in. odnośnik do portalu sądu) pobierzesz narzędziem szczegółów "
                    f"źródła '{first.source}', podając source_id rekordu, np. '{first.source_id}'."
                ),
            )
        )
        if output.result_set_id:
            hints.append(
                Hint(
                    message=f"Możesz dalej filtrować te wyniki używając result_set_id='{output.result_set_id}'.",
                    tool="filter_results",
                    parameters={"result_set_id": output.result_set_id},
                )
            )
    if output.no_match_is_inconclusive:
        scope = output.source_scope
        seen = (
            f"{scope.stored_count} rekordów z {scope.corpus_count} dopasowań"
            if scope.corpus_count is not None
            else f"{scope.stored_count} rekordów"
        )
        hints.append(
            Hint(
                message=(
                    f"Brak dopasowań, ale filtrowano OKNO: {seen}. Ten wynik NIE dowodzi, że takie orzeczenie "
                    f"nie istnieje — pozostałe rekordy zbioru nie zostały sprawdzone. Zawęź kryteria wyszukiwania "
                    f"albo pobierz kolejne okno wyników, tak aby zbiór zmieścił się w limicie filter_results "
                    f"({filter_max_records} rekordów), i filtruj ponownie."
                )
            )
        )
    return hints

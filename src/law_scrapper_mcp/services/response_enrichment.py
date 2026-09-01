"""Response enrichment with contextual hints for next steps."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from law_scrapper_mcp.models.tool_outputs import (
    Hint,
    LoadedDocumentInfo,
    ResultSetInfo,
    ResultSetScope,
    SetScope,
)


def _filter_results_hint(result_set_id: str, scope: ResultSetScope | None) -> Hint:
    """Say what filtering actually narrows — the set, which may be a window."""
    if scope is not None and scope.scope is SetScope.PAGE and scope.corpus_count is not None:
        message = (
            f"Użyj filter_results aby zawęzić wyniki. UWAGA: zawężaniu podlega zestaw "
            f"{scope.stored_count} rekordów, który jest OKNEM z {scope.corpus_count} dopasowań. "
            f"Brak dopasowania w filtrze nie dowodzi, że akt nie istnieje w zbiorze."
        )
    else:
        message = (
            "Użyj filter_results aby zawęzić wyniki, np. po typie dokumentu "
            "(Ustawa, Rozporządzenie) lub wzorcem regex w tytule."
        )
    return Hint(message=message, tool="filter_results", parameters={"result_set_id": result_set_id})


def _complete_set_hint(
    tool_name: str,
    next_call_params: dict[str, Any],
    corpus_count: int,
    filter_max_records: int,
    max_result_limit: int | None = None,
) -> Hint:
    """Tell the model how to get a complete set — or that it cannot, and what to do instead.

    The suggested limit is clamped to whichever ceiling actually binds (D9): the
    `filter_results` ceiling, or the calling tool's own hard cap on `limit`
    (`max_result_limit`, e.g. `browse_acts`'s page-size clamp), whichever is lower.
    Suggesting a `limit` above either would be unexecutable — either
    `ResultSetTooLargeError` on the first `filter_results` call, or the calling tool
    silently clamping the value before it ever reaches the result store, handing back
    another PAGE instead of the COMPLETE set the hint promised. Both are the same
    defect as F48, one level up. `max_result_limit=None` means the calling tool's
    `limit` is genuinely unbounded (true for `search_legal_acts`).
    """
    effective_ceiling = filter_max_records if max_result_limit is None else min(filter_max_records, max_result_limit)
    if corpus_count <= effective_ceiling:
        return Hint(
            message=(
                f"Aby zestaw objął CAŁY zbiór ({corpus_count} dopasowań) i filtrowanie było "
                f"rozstrzygające, powtórz to wywołanie z limit={corpus_count}."
            ),
            tool=tool_name,
            # No `offset` key on purpose. Zero is the default, so omitting it keeps the
            # call correct — and it keeps this hint distinguishable from the pagination
            # one, which is defined by carrying an offset.
            parameters={**next_call_params, "limit": corpus_count},
        )
    if max_result_limit is not None and max_result_limit < filter_max_records:
        limiting_reason = f"limit narzędzia {tool_name} ({max_result_limit} rekordów)"
    else:
        limiting_reason = f"limit filter_results ({filter_max_records} rekordów)"
    return Hint(
        message=(
            f"Zbiór liczy {corpus_count} dopasowań i przekracza {limiting_reason}, "
            f"więc powiększenie okna nie da zestawu kompletnego — taki zestaw zostanie "
            f"odrzucony lub przycięty. Zawęź kryteria wyszukiwania (tytuł, typ aktu, "
            f"słowa kluczowe albo zakres dat)."
        ),
        tool=tool_name,
    )


def search_hints(
    total_count: int,
    has_results: bool,
    eli: str | None = None,
    result_set_id: str | None = None,
    *,
    tool_name: str,
    next_call_params: dict[str, Any],
    filter_max_records: int,
    scope: ResultSetScope | None = None,
    offset: int = 0,
    returned_count: int = 0,
    applied_limit: int | None = None,
    max_result_limit: int | None = None,
) -> list[Hint]:
    """Generate hints for search and browse results.

    `tool_name` and `next_call_params` are required and have no defaults on purpose.
    This function used to hard-code `search_legal_acts`, so every hint `browse_acts`
    produced pointed the model at a different tool than the one it had called (F48).
    A default would let that come back. Only the calling tool knows which parameters
    it accepts, so it builds `next_call_params` itself.
    """
    hints = []
    if has_results and eli:
        hints.append(
            Hint(
                message="Użyj get_act_details aby zobaczyć szczegóły wybranego aktu.",
                tool="get_act_details",
                parameters={"eli": eli},
            )
        )
    if has_results and result_set_id:
        hints.append(_filter_results_hint(result_set_id, scope))

    was_truncated = offset + returned_count < total_count
    if was_truncated and applied_limit:
        # A hint the model can copy and run: every source criterion, plus the window.
        # The previous version said "użyj limit/offset" without saying which values,
        # while `PageInfo.next_offset` had already computed one of them.
        hints.append(
            Hint(
                message=(
                    f"Zwrócono {returned_count} z {total_count} dopasowań. "
                    f"Następna strona to wywołanie z offset={offset + returned_count}."
                ),
                tool=tool_name,
                parameters={
                    **next_call_params,
                    "limit": applied_limit,
                    "offset": offset + returned_count,
                },
            )
        )

    if scope is not None and scope.scope is SetScope.PAGE and scope.corpus_count is not None:
        hints.append(
            _complete_set_hint(
                tool_name,
                next_call_params,
                scope.corpus_count,
                filter_max_records,
                max_result_limit,
            )
        )

    if not has_results:
        if next_call_params.get("keywords"):
            hints.append(
                Hint(
                    message="Brak wyników. UWAGA: Słowa kluczowe API działają z logiką AND — "
                    "wszystkie muszą wystąpić jednocześnie. Spróbuj mniej słów kluczowych "
                    "lub szukaj każdego osobno (logika OR).",
                    tool=tool_name,
                )
            )
        hints.append(
            Hint(
                message="Spróbuj poszerzyć kryteria: usuń filtry dat, zmień typ dokumentu lub rok.",
                tool=tool_name,
            )
        )
        hints.append(
            Hint(
                message="Sprawdź dostępne słowa kluczowe, typy lub statusy w metadanych systemu.",
                tool="get_system_metadata",
                parameters={"category": "keywords"},
            )
        )
    return hints


def act_details_hints(
    eli: str,
    is_loaded: bool,
    has_html: bool,
    *,
    just_loaded: bool = False,
) -> list[Hint]:
    """Generate hints for act details."""
    hints = []
    if not is_loaded and has_html:
        hints.append(
            Hint(
                message="Załaduj pełną treść aby czytać sekcje lub przeszukiwać akt.",
                tool="get_act_details",
                parameters={"eli": eli, "load_content": True},
            )
        )
    if is_loaded:
        if just_loaded:
            hints.append(
                Hint(
                    message="Dokument załadowany do pamięci. TTL: 2h. "
                    "Po tym czasie wymagane ponowne załadowanie (load_content=true).",
                )
            )
        hints.append(
            Hint(
                message="Przeczytaj wybraną sekcję aktu.",
                tool="read_act_content",
                parameters={"eli": eli},
            )
        )
        hints.append(
            Hint(
                message="Wyszukaj konkretne terminy w treści aktu.",
                tool="search_in_act",
                parameters={"eli": eli},
            )
        )
    hints.append(
        Hint(
            message="Przeanalizuj powiązania i referencje tego aktu z innymi aktami.",
            tool="analyze_act_relationships",
            parameters={"eli": eli},
        )
    )
    return hints


def metadata_hints(category: str, failed_categories: Sequence[str] = ()) -> list[Hint]:
    """Generate hints for metadata results."""
    hints = []
    if category in ("all", "keywords"):
        hints.append(
            Hint(
                message="Użyj pobranych słów kluczowych do wyszukiwania aktów prawnych.",
                tool="search_legal_acts",
            )
        )
    if category in ("all", "types"):
        hints.append(
            Hint(
                message="Filtruj wyniki wyszukiwania po typie dokumentu (np. 'Ustawa', 'Rozporządzenie').",
                tool="search_legal_acts",
            )
        )
    if failed_categories:
        hints.append(
            Hint(
                message=(
                    "Nie udało się pobrać kategorii: "
                    f"{', '.join(failed_categories)}. Wynik jest niepełny — "
                    "ponów wywołanie, aby uzupełnić brakujące wartości."
                ),
                tool="get_system_metadata",
                parameters={"category": failed_categories[0]},
            )
        )
    return hints


def content_hints(eli: str, has_sections: bool) -> list[Hint]:
    """Generate hints for content reading."""
    hints = []
    if has_sections:
        hints.append(
            Hint(
                message="Wyszukaj konkretne terminy w treści tego aktu.",
                tool="search_in_act",
                parameters={"eli": eli},
            )
        )
    return hints


def relationships_hints(eli: str, relationship_types: list[str]) -> list[Hint]:
    """Generate hints for relationships analysis."""
    hints = [
        Hint(
            message="Sprawdź szczegóły tego aktu.",
            tool="get_act_details",
            parameters={"eli": eli},
        ),
        Hint(
            message="Załaduj treść aby przeczytać akt.",
            tool="get_act_details",
            parameters={"eli": eli, "load_content": True},
        ),
    ]
    if any(t in relationship_types for t in ("Akty zmieniające", "Akty zmienione")):
        hints.append(
            Hint(
                message="Śledź zmiany prawne w czasie.",
                tool="track_legal_changes",
            )
        )
    return hints


def date_hints() -> list[Hint]:
    """Generate hints for date calculations."""
    return [
        Hint(
            message="Użyj obliczonej daty jako filtra w wyszukiwaniu aktów prawnych.",
            tool="search_legal_acts",
        ),
        Hint(
            message="Śledź zmiany prawne w zakresie dat.",
            tool="track_legal_changes",
        ),
    ]


def loaded_documents_hints(documents: Sequence[LoadedDocumentInfo]) -> list[Hint]:
    """Generate hints for the loaded-document listing."""
    if not documents:
        return []
    return [
        Hint(
            message=f"Użyj read_act_content(eli='{documents[0].eli}') aby czytać treść.",
            tool="read_act_content",
            parameters={"eli": documents[0].eli},
        )
    ]


def result_sets_hints(sets: Sequence[ResultSetInfo]) -> list[Hint]:
    """Generate hints for the result-set listing."""
    if not sets:
        return []
    return [
        Hint(
            message=f"Użyj filter_results(result_set_id='{sets[0].result_set_id}') aby filtrować wyniki.",
            tool="filter_results",
            parameters={"result_set_id": sets[0].result_set_id},
        )
    ]


def search_in_act_hints(requested: int, applied: int) -> list[Hint]:
    """Report a clamped context window; stay silent when nothing was clamped."""
    if requested <= applied:
        return []
    return [
        Hint(
            message=(
                f"Parametr context_chars={requested} przekracza granicę {applied} znaków "
                f"i został przycięty do {applied}. Zwrócony kontekst odpowiada wartości {applied}."
            ),
            tool="search_in_act",
            parameters={"context_chars": applied},
        )
    ]


def compare_hints(eli_a: str, eli_b: str) -> list[Hint]:
    """Generate hints for act comparison."""
    return [
        Hint(
            message="Załaduj treść pierwszego aktu aby przeczytać szczegóły.",
            tool="get_act_details",
            parameters={"eli": eli_a, "load_content": True},
        ),
        Hint(
            message="Załaduj treść drugiego aktu aby przeczytać szczegóły.",
            tool="get_act_details",
            parameters={"eli": eli_b, "load_content": True},
        ),
        Hint(
            message="Przeanalizuj powiązania pierwszego aktu.",
            tool="analyze_act_relationships",
            parameters={"eli": eli_a},
        ),
        Hint(
            message="Przeanalizuj powiązania drugiego aktu.",
            tool="analyze_act_relationships",
            parameters={"eli": eli_b},
        ),
    ]

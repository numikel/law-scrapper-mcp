"""Response enrichment with contextual hints for next steps."""

from __future__ import annotations

from law_scrapper_mcp.models.tool_outputs import Hint


def search_hints(
    total_count: int,
    has_results: bool,
    eli: str | None = None,
    result_set_id: str | None = None,
    *,
    was_truncated: bool = False,
    applied_limit: int | None = None,
) -> list[Hint]:
    """Generate hints for search results."""
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
        hints.append(
            Hint(
                message="Użyj filter_results aby zawęzić wyniki, np. po typie dokumentu (Ustawa, Rozporządzenie) lub wzorcem regex w tytule.",
                tool="filter_results",
                parameters={"result_set_id": result_set_id},
            )
        )
    if was_truncated and applied_limit:
        hints.append(
            Hint(
                message=f"Wyniki ograniczone do {applied_limit} (z {total_count} dostępnych). "
                f"Użyj limit/offset do paginacji lub filter_results do zawężenia.",
                tool="search_legal_acts",
            )
        )
    elif total_count > 20:
        hints.append(
            Hint(
                message="Użyj parametrów 'limit' i 'offset' do paginacji wyników.",
                tool="search_legal_acts",
            )
        )
    if not has_results:
        hints.append(
            Hint(
                message="Brak wyników. UWAGA: Słowa kluczowe API działają z logiką AND — "
                "wszystkie muszą wystąpić jednocześnie. Spróbuj mniej słów kluczowych "
                "lub szukaj każdego osobno (logika OR).",
                tool="search_legal_acts",
            )
        )
        hints.append(
            Hint(
                message="Spróbuj poszerzyć kryteria: usuń filtry dat, zmień typ dokumentu lub rok.",
                tool="search_legal_acts",
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


def metadata_hints(category: str) -> list[Hint]:
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

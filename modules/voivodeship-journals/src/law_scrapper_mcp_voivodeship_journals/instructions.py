"""The Polish block the core appends to the server instructions while the module is active."""

from __future__ import annotations

from law_scrapper_mcp_voivodeship_journals.catalog import SERVED, WITHHELD


def build_instructions() -> str:
    """Compose the block from the catalogue, so the listed codes cannot drift from the served ones."""
    served = ", ".join(journal.code for journal in SERVED)
    withheld = "; ".join(f"{journal.code} ({journal.withheld})" for journal in WITHHELD)
    return (
        "Dzienniki urzędowe województw (publikatory POL_WOJ_*) działają przez te same narzędzia co DU i MP; "
        "moduł nie dodaje własnych narzędzi.\n"
        f"- Obsługiwane kody wydawców ({len(SERVED)}): {served}. "
        "Kod podajesz w parametrze publisher (search_legal_acts, browse_acts, track_legal_changes) "
        "albo w ELI (get_act_details, read_act_content, search_in_act, analyze_act_relationships, compare_acts), "
        "np. POL_WOJ_PM/2025/5051.\n"
        "- Każdy dziennik ma własny serwer i zna tylko swoje akty. Zawsze podawaj year: wyszukiwanie bez roku "
        "trwa kilka do kilkunastu sekund.\n"
        "- Nie są obsługiwane i kończą się błędem: filtr in_force oraz date_from/date_to (data wejścia w życie). "
        "Okno czasu ustawiasz datami ogłoszenia: pub_date_from/pub_date_to. W wynikach brak daty wejścia "
        "w życie i statusu obowiązywania; promulgation_date to data ogłoszenia w dzienniku.\n"
        "- Słowo kluczowe podaj jedno: lista po przecinku nie zawęża wyników jak w DU i MP. Dla OR zrób osobne "
        "wyszukiwania.\n"
        "- W search_legal_acts limit powyżej 100 jest odrzucany (kolejne strony pobieraj przez offset).\n"
        "- track_legal_changes zwraca akty ogłoszone w oknie date_from..date_to.\n"
        "- Treść jest czytana z PDF. Skan bez tekstu daje content_status=unavailable i link do PDF. "
        "Starsze akty (sprzed ok. 2006 r.) mogą mieć zniekształconą warstwę tekstu, np. znaki (cid:N); "
        "porównaj wtedy z PDF.\n"
        "- analyze_act_relationships zwraca powiązania pod kluczami dziennika (opisami relacji), "
        "nie pod kluczami DU i MP.\n"
        f"- Wyłączone z obsługi: {withheld}. Zapytanie o taki kod kończy się błędem z powodem."
    )

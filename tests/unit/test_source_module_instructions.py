"""Server instructions: the no-module text is pinned byte for byte, modules add a count and a block."""

from __future__ import annotations

import pytest

from fake_source_modules import fake_module
from law_scrapper_mcp.server import CORE_TOOL_COUNT, SERVER_INSTRUCTIONS
from law_scrapper_mcp.source_modules.instructions import compose_instructions

# The text the server sent before the module mechanism existed (a2922dd), copied verbatim. Do not
# edit it to make a test pass: a change here is a change of what every agent reads at `initialize`.
PRE_CHANGE_INSTRUCTIONS = """Jesteś specjalistycznym asystentem do analizy polskiego prawa.
Odpowiadaj użytkownikowi w jego języku. Dane z narzędzi (tytuły aktów, statusy, typy) są po polsku.

DOSTĘPNE NARZĘDZIA (13):

1. WYSZUKIWANIE I PRZEGLĄDANIE:
   - search_legal_acts — wyszukuj akty po słowach kluczowych, typie (Ustawa, Rozporządzenie itp.), datach, statusie
   - browse_acts — przeglądaj wszystkie akty z danego roku i wydawcy
   - filter_results — filtruj wyniki wyszukiwania wzorcem regex, typem, datami (wymaga result_set_id z search/browse)

2. ANALIZA AKTÓW:
   - get_act_details — szczegóły aktu (status, daty, spis treści). Użyj load_content=True aby załadować treść
   - read_act_content — czytaj sekcje załadowanego aktu (Art., Rozdział)
   - search_in_act — szukaj terminów w załadowanym akcie
   - compare_acts — porównaj metadane dwóch aktów (tytuły, statusy, daty, słowa kluczowe)

3. POWIĄZANIA I ZMIANY:
   - analyze_act_relationships — powiązania między aktami (co zmienia, podstawa prawna, akty uchylone)
   - track_legal_changes — śledzenie nowych aktów/zmian w zakresie dat

4. NARZĘDZIA POMOCNICZE:
   - get_system_metadata — dostępne słowa kluczowe, typy dokumentów, statusy, wydawcy
   - calculate_legal_date — obliczanie terminów prawnych (dni/miesiące/lata od daty)
   - list_result_sets — wyświetl aktywne zestawy wyników w pamięci
   - list_loaded_documents — wyświetl załadowane dokumenty w pamięci

WORKFLOW — CZYTANIE TREŚCI AKTU:
1. get_act_details(eli="DU/2024/1692", load_content=True) → załaduj akt
2. read_act_content(eli="DU/2024/1692") → spis treści
3. read_act_content(eli="DU/2024/1692", section="Art. 1") → czytaj sekcję
4. search_in_act(eli="DU/2024/1692", query="termin") → szukaj w treści

WORKFLOW — ZAAWANSOWANE WYSZUKIWANIE:
1. search_legal_acts(year=2024, act_type="Rozporządzenie") → szerokie wyszukiwanie
2. filter_results(result_set_id="rs_1", pattern="zdrow|apteka|lekar") → zawęź regex na tytule
3. get_act_details(eli=...) → szczegóły wybranych aktów

WORKFLOW — ANALIZA ZMIAN AKTU:
1. search_legal_acts(title="ochrona przeciwpożarowa") → znajdź akt
2. analyze_act_relationships(eli=...) → sprawdź co go zmienia
3. get_act_details(eli=<zmieniający akt>, load_content=True) → załaduj treść zmiany

WORKFLOW — MONITORING ZMIAN PRAWNYCH:
1. track_legal_changes(date_from="2025-01-01", keywords=["podatek"]) → nowe akty podatkowe
2. filter_results(result_set_id=..., type_equals="Ustawa") → tylko ustawy
3. get_act_details(eli=...) → szczegóły wybranego aktu

WORKFLOW — PORÓWNYWANIE AKTÓW:
1. search_legal_acts(title="ochrona danych") → znajdź akty
2. compare_acts(eli_a="DU/2018/1000", eli_b="DU/2024/1692") → porównaj metadane
3. get_act_details(eli=..., load_content=True) → załaduj treść wybranego aktu

WORKFLOW — OBLICZANIE TERMINÓW:
1. calculate_legal_date(days=14, base_date="2025-02-01") → termin odwołania
2. search_legal_acts(title="kodeks postępowania administracyjnego") → znajdź KPA

UWAGI:
- Identyfikator ELI: wydawca/rok/pozycja (np. DU/2024/1692, MP/2023/500)
- Wydawcy: DU = Dziennik Ustaw, MP = Monitor Polski
- Słowa kluczowe API używają logiki AND. Dla OR szukaj każdego osobno.
- Każda odpowiedź zawiera 'hints' z sugerowanymi kolejnymi krokami.
- Dane w systemie (typy, statusy, słowa kluczowe) są po polsku."""


def test_core_instructions_are_unchanged_by_the_mechanism() -> None:
    assert SERVER_INSTRUCTIONS == PRE_CHANGE_INSTRUCTIONS


def test_no_module_leaves_the_instructions_byte_identical() -> None:
    assert compose_instructions(PRE_CHANGE_INSTRUCTIONS, 13, []) == PRE_CHANGE_INSTRUCTIONS


def test_core_tool_count_matches_the_literal_in_the_header() -> None:
    assert f"DOSTĘPNE NARZĘDZIA ({CORE_TOOL_COUNT}):" in SERVER_INSTRUCTIONS


def test_modules_raise_the_count_and_append_one_block_each() -> None:
    alpha = fake_module("alpha", ["alpha_one", "alpha_two"], instructions="Opis alfa.\n")
    beta = fake_module("beta", ["beta_one"], instructions="Opis beta.")

    text = compose_instructions(PRE_CHANGE_INSTRUCTIONS, 13, [alpha, beta])

    assert "DOSTĘPNE NARZĘDZIA (16):" in text
    assert "DOSTĘPNE NARZĘDZIA (13):" not in text
    assert text.startswith(PRE_CHANGE_INSTRUCTIONS.replace("(13)", "(16)"))
    assert text.endswith(
        "\n\nMODUŁ ŹRÓDŁA — alpha (narzędzi: 2)\nOpis alfa.\n\nMODUŁ ŹRÓDŁA — beta (narzędzi: 1)\nOpis beta."
    )


def test_one_module_of_two_tools_makes_the_header_say_fifteen() -> None:
    text = compose_instructions(PRE_CHANGE_INSTRUCTIONS, 13, [fake_module("alpha", ["a_one", "a_two"])])

    assert "DOSTĘPNE NARZĘDZIA (15):" in text
    assert "MODUŁ ŹRÓDŁA — alpha (narzędzi: 2)" in text


def test_a_header_that_cannot_be_updated_is_an_error_not_a_silent_miscount() -> None:
    with pytest.raises(ValueError, match="DOSTĘPNE NARZĘDZIA"):
        compose_instructions("brak nagłówka", 13, [fake_module("alpha")])
    with pytest.raises(ValueError, match="DOSTĘPNE NARZĘDZIA"):
        compose_instructions(PRE_CHANGE_INSTRUCTIONS, 12, [fake_module("alpha")])

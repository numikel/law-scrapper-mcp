"""Testy kompilacji wzorców dostarczonych przez klienta (klaster 1 — F01)."""

from __future__ import annotations

from time import perf_counter

import pytest

# Wzorzec z PoC audytu: pod silnikiem `re` nie wraca dla łańcuchów tej długości.
CATASTROPHIC_PATTERN = "(.+)+!"

# Tytuł o realnej długości. Audyt zmierzył na 50 aktach z 2024 r.:
# min 70, śr. 135, maks. 495 znaków.
LONG_TITLE = (
    "Rozporządzenie Ministra Rozwoju i Technologii z dnia 12 kwietnia 2024 r. "
    "zmieniające rozporządzenie w sprawie szczegółowego zakresu i formy projektu "
    "budowlanego oraz warunków technicznych, jakim powinny odpowiadać budynki "
    "i ich usytuowanie, w zakresie wymagań ochrony przeciwpożarowej"
)


def test_long_title_is_representative() -> None:
    """Test PoC ma sens tylko na łańcuchu powyżej progu wykrywalnego zamrożenia."""
    assert len(LONG_TITLE) > 250


@pytest.mark.timeout(5)  # druga warstwa
def test_re2_engine_is_available_and_linear() -> None:
    """Silnik RE2 kompiluje wzorzec z PoC i zwraca brak dopasowania natychmiast."""
    import re2

    options = re2.Options()
    options.case_sensitive = False
    options.log_errors = False
    options.max_mem = 8 * 1024 * 1024

    compiled = re2.compile(CATASTROPHIC_PATTERN, options)

    # `re` trzyma GIL podczas backtrackingu, więc wątek-timer pytest-timeout
    # (metoda "thread", domyślna na Windows) nigdy nie dostaje sterowania —
    # marker sam w sobie nie jest wiarygodną bramką dla tej klasy awarii.
    # Mierzymy czas jawnie wewnątrz testu jako pierwszą, rozstrzygającą warstwę.
    start = perf_counter()
    assert compiled.search(LONG_TITLE) is None
    assert perf_counter() - start < 0.5

    # Kontrola pozytywna na tych samych options — bez niej test przeszedłby
    # także dla silnika, który nigdy niczego nie dopasowuje.
    assert re2.compile("Ministra", options).search(LONG_TITLE) is not None

"""Czysta polityka klasyfikacji błędów żądań do API Sejmu.

Moduł nie dotyka sieci, zegara ani stanu współdzielonego — dzięki temu cała
polityka ponawiania jest testowalna offline, łącznie z przemiataniem wszystkich
kodów statusu. Warstwa sieciowa (`sejm_client`) tylko odczytuje werdykt.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

DEFAULT_BACKOFF_BASE = 1.0
DEFAULT_BACKOFF_CAP = 10.0


@dataclass(frozen=True)
class Verdict:
    """Werdykt polityki dla pojedynczej nieudanej próby.

    Attributes:
        retryable: Czy próbę wolno powtórzyć.
        breaker_failure: Czy zdarzenie liczy się jako awaria wyłącznika obwodu.
        retry_after: Czas oczekiwania narzucony przez serwer, w sekundach.
        rate_limited: Czy serwer zgłosił przekroczenie limitu (HTTP 429).
    """

    retryable: bool
    breaker_failure: bool
    retry_after: float | None = None
    rate_limited: bool = False


def _parse_retry_after(raw: str | None) -> float | None:
    """Odczytaj nagłówek Retry-After wyrażony w sekundach.

    Wariant z datą HTTP jest świadomie pomijany — API Sejmu go nie zwraca,
    a nieodczytany nagłówek bezpiecznie degraduje się do zwykłego backoffu.
    """
    if raw is None:
        return None
    try:
        seconds = float(raw.strip())
    except ValueError:
        return None
    if seconds < 0:
        return None
    return seconds


def classify_failure(exc: httpx.HTTPError) -> Verdict:
    """Zaklasyfikuj nieudaną próbę żądania.

    Args:
        exc: Wyjątek podniesiony przez warstwę `httpx`.

    Returns:
        Werdykt mówiący, czy ponawiać i czy liczyć awarię wyłącznika.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        retry_after = _parse_retry_after(exc.response.headers.get("retry-after"))
        if status == 429:
            return Verdict(
                retryable=True,
                breaker_failure=False,
                retry_after=retry_after,
                rate_limited=True,
            )
        if 500 <= status <= 599:
            return Verdict(retryable=True, breaker_failure=True, retry_after=retry_after)
        return Verdict(retryable=False, breaker_failure=False)

    # UnsupportedProtocol to błąd konfiguracji, nie sieciowy — nie ponawiaj.
    if isinstance(exc, httpx.UnsupportedProtocol):
        return Verdict(retryable=False, breaker_failure=False)

    # TimeoutException jest w httpx podklasą TransportError, więc F11 i dotychczasowe
    # zachowanie dla timeoutów obsługuje jedna gałąź.
    if isinstance(exc, httpx.TransportError):
        return Verdict(retryable=True, breaker_failure=True)

    return Verdict(retryable=False, breaker_failure=False)


def backoff(
    attempt: int,
    *,
    base: float = DEFAULT_BACKOFF_BASE,
    cap: float = DEFAULT_BACKOFF_CAP,
) -> float:
    """Zwróć opóźnienie wykładnicze przed próbą numer `attempt` + 1.

    Args:
        attempt: Numer właśnie zakończonej próby, liczony od 1.
        base: Opóźnienie po pierwszej nieudanej próbie, w sekundach.
        cap: Górne ograniczenie opóźnienia, w sekundach.

    Returns:
        Liczba sekund oczekiwania.
    """
    return min(base * (2 ** (attempt - 1)), cap)

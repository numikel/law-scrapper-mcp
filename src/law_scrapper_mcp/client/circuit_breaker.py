"""Simple circuit breaker for API protection."""

from __future__ import annotations

import logging
from enum import StrEnum
from time import monotonic

logger = logging.getLogger(__name__)


class CircuitState(StrEnum):
    """Circuit breaker states."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Simple circuit breaker to prevent cascading failures.

    States:
        CLOSED  — normal operation, failures are counted
        OPEN    — all requests rejected immediately
        HALF_OPEN — limited test requests allowed to probe recovery

    Synchronization:
        Sekcje krytyczne nie zawierają `await`, więc pętla zdarzeń nie może ich
        przerwać w połowie — stan jest atomowy z konstrukcji i nie wymaga
        `asyncio.Lock`. Warunek jest przypięty testem; dodanie `await` do
        którejkolwiek z metod `try_acquire` / `release_*` unieważnia to założenie.

        Acquire/release pairing limitation (known, accepted):
        `release_success()`, `release_failure()`, and `release_probe()` determine
        their behavior based on the breaker's *current* state at release time, not
        the state when `try_acquire()` was called. A request admitted while CLOSED
        but completing after the breaker transitions to HALF_OPEN will be treated
        as a HALF_OPEN release, decrementing a probe slot it never held and — for
        `release_success()` — counting toward recovery without having passed through
        HALF_OPEN admission. This is a known architectural limitation, not a bug to
        silently hide.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        half_open_max_calls: int = 3,
    ):
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._half_open_max_calls = half_open_max_calls

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: float = 0.0
        self._half_open_successes = 0
        self._half_open_in_flight = 0

    @property
    def state(self) -> CircuitState:
        """Bieżący stan wyłącznika.

        Odczyt jest wolny od efektów ubocznych — przejście OPEN → HALF_OPEN
        wykonuje wyłącznie `try_acquire()`.
        """
        return self._state

    @property
    def failure_count(self) -> int:
        """Bieżąca liczba zarejestrowanych awarii."""
        return self._failure_count

    def try_acquire(self) -> bool:
        """Poproś o zgodę na wykonanie żądania.

        W stanie HALF_OPEN licznik sond rośnie w chwili wpuszczenia, nie po
        zakończeniu żądania — dzięki temu nie istnieje moment, w którym sonda
        została dopuszczona, ale nie jest policzona.

        Returns:
            True, jeśli żądanie wolno wysłać.
        """
        if self._state == CircuitState.OPEN and monotonic() - self._last_failure_time >= self._recovery_timeout:
            self._state = CircuitState.HALF_OPEN
            self._half_open_in_flight = 0
            self._half_open_successes = 0
            logger.info("Circuit breaker transitioning to HALF_OPEN")

        if self._state == CircuitState.CLOSED:
            return True

        if self._state == CircuitState.HALF_OPEN:
            if self._half_open_in_flight >= self._half_open_max_calls:
                return False
            self._half_open_in_flight += 1
            return True

        return False

    def release_success(self) -> None:
        """Zwolnij sondę po udanym żądaniu."""
        if self._state == CircuitState.HALF_OPEN:
            self._release_slot()
            self._half_open_successes += 1
            if self._half_open_successes >= self._half_open_max_calls:
                self._state = CircuitState.CLOSED
                self._failure_count = 0
                self._half_open_in_flight = 0
                self._half_open_successes = 0
                logger.info("Circuit breaker CLOSED after successful recovery")
        elif self._state == CircuitState.CLOSED:
            self._failure_count = 0

    def release_failure(self) -> None:
        """Zwolnij sondę i zarejestruj awarię operacji.

        Wołane najwyżej raz na operację użytkownika, po wyczerpaniu prób —
        próg awarii liczy nieudane żądania, nie nieudane próby sieciowe.
        """
        self._failure_count += 1
        self._last_failure_time = monotonic()

        if self._state == CircuitState.HALF_OPEN:
            self._release_slot()
            self._state = CircuitState.OPEN
            logger.warning("Circuit breaker re-OPENED from HALF_OPEN after failure")
        elif self._failure_count >= self._failure_threshold:
            self._state = CircuitState.OPEN
            logger.warning(
                "Circuit breaker OPENED after %d failures (threshold: %d)",
                self._failure_count,
                self._failure_threshold,
            )

    def release_probe(self) -> None:
        """Zwolnij sondę bez werdyktu.

        Używane tam, gdzie zdarzenie nie jest ani sukcesem, ani awarią serwera —
        na przykład przy HTTP 429 albo po próbie, po której nastąpi ponowienie.
        """
        self._release_slot()

    def _release_slot(self) -> None:
        if self._state == CircuitState.HALF_OPEN:
            self._half_open_in_flight = max(0, self._half_open_in_flight - 1)

    def can_execute(self) -> bool:
        """Przestarzałe: delegat do `try_acquire()`, usuwany w Task 3."""
        return self.try_acquire()

    def record_success(self) -> None:
        """Przestarzałe: delegat do `release_success()`, usuwany w Task 3."""
        self.release_success()

    def record_failure(self) -> None:
        """Przestarzałe: delegat do `release_failure()`, usuwany w Task 3."""
        self.release_failure()

    def reset(self) -> None:
        """Przywróć wyłącznik do stanu zamkniętego."""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._half_open_successes = 0
        self._half_open_in_flight = 0

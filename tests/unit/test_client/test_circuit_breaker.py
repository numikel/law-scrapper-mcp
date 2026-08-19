"""Testy tranzycji i wyścigu wyłącznika obwodu (F14, F21, D10; kryteria 5.4-5.6)."""

from __future__ import annotations

import asyncio
import dis
import inspect
from typing import Any

import pytest

from law_scrapper_mcp.client import circuit_breaker as breaker_module
from law_scrapper_mcp.client.circuit_breaker import CircuitBreaker, CircuitState


def _open_the_breaker(breaker: CircuitBreaker, failures: int = 5) -> None:
    for _ in range(failures):
        assert breaker.try_acquire() is True
        breaker.release_failure()


def test_starts_closed_and_admits_requests() -> None:
    breaker = CircuitBreaker()
    assert breaker.state is CircuitState.CLOSED
    assert breaker.try_acquire() is True


def test_closed_to_open_after_threshold_failures() -> None:
    """F14: tranzycja CLOSED → OPEN."""
    breaker = CircuitBreaker(failure_threshold=5)
    _open_the_breaker(breaker, failures=4)
    assert breaker.state is CircuitState.CLOSED
    assert breaker.try_acquire() is True
    breaker.release_failure()
    assert breaker.state is CircuitState.OPEN
    assert breaker.failure_count == 5


def test_open_rejects_without_admitting() -> None:
    breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=60.0)
    _open_the_breaker(breaker, failures=1)
    assert breaker.try_acquire() is False


def test_success_in_closed_resets_failure_count() -> None:
    breaker = CircuitBreaker(failure_threshold=5)
    _open_the_breaker(breaker, failures=3)
    assert breaker.failure_count == 3
    assert breaker.try_acquire() is True
    breaker.release_success()
    assert breaker.failure_count == 0


def test_open_to_half_open_after_recovery_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """F14: tranzycja OPEN → HALF_OPEN, sterowana wstrzykniętym zegarem (O6)."""
    now = 1000.0
    monkeypatch.setattr(breaker_module, "monotonic", lambda: now)
    breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=30.0)
    _open_the_breaker(breaker, failures=1)
    assert breaker.try_acquire() is False

    now = 1031.0
    assert breaker.try_acquire() is True
    assert breaker.state is CircuitState.HALF_OPEN


def test_reading_state_does_not_transition(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ustalenie 1.2: getter przestaje być mutatorem — sam odczyt nic nie zmienia."""
    now = 1000.0
    monkeypatch.setattr(breaker_module, "monotonic", lambda: now)
    breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=30.0)
    _open_the_breaker(breaker, failures=1)

    now = 1031.0
    assert breaker.state is CircuitState.OPEN
    assert breaker.state is CircuitState.OPEN
    assert breaker.try_acquire() is True
    assert breaker.state is CircuitState.HALF_OPEN


def test_half_open_to_closed_after_enough_successes() -> None:
    """F14: tranzycja HALF_OPEN → CLOSED."""
    breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=0.0, half_open_max_calls=3)
    _open_the_breaker(breaker, failures=1)
    for _ in range(3):
        assert breaker.try_acquire() is True
        breaker.release_success()
    assert breaker.state is CircuitState.CLOSED
    assert breaker.failure_count == 0


def test_half_open_to_open_on_single_failure() -> None:
    """F14: tranzycja HALF_OPEN → OPEN."""
    breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=0.0, half_open_max_calls=3)
    _open_the_breaker(breaker, failures=1)
    assert breaker.try_acquire() is True
    breaker.release_failure()
    assert breaker.state is CircuitState.OPEN


def test_reset_returns_to_closed() -> None:
    """F14: reset()."""
    breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=0.0, half_open_max_calls=3)
    _open_the_breaker(breaker, failures=1)
    assert breaker.try_acquire() is True
    breaker.reset()
    assert breaker.state is CircuitState.CLOSED
    assert breaker.failure_count == 0
    for _ in range(10):
        assert breaker.try_acquire() is True


def test_half_open_admits_exactly_max_calls() -> None:
    """F21: licznik sond rośnie przy wpuszczeniu, nie przy zakończeniu żądania.

    Pada na kodzie sprzed zmiany: `can_execute()` czyta `_half_open_successes`,
    który rośnie dopiero w `record_success()`, więc dziesięć sprawdzeń z rzędu
    zwraca dziesięć zgód.
    """
    breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=0.0, half_open_max_calls=3)
    _open_the_breaker(breaker, failures=1)

    verdicts = [breaker.try_acquire() for _ in range(10)]
    assert verdicts.count(True) == 3
    assert verdicts.count(False) == 7


@pytest.mark.asyncio
async def test_half_open_admits_exactly_max_calls_under_concurrency() -> None:
    """F21, kryterium 5.5: dziesięć współbieżnych operacji, dokładnie trzy sondy."""
    breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=0.0, half_open_max_calls=3)
    _open_the_breaker(breaker, failures=1)

    async def probe() -> bool:
        await asyncio.sleep(0)
        return breaker.try_acquire()

    verdicts = await asyncio.gather(*(probe() for _ in range(10)))
    assert sum(verdicts) == 3


def test_release_probe_frees_a_slot_without_a_verdict() -> None:
    """D6: 429 zwalnia sondę, ale nie zmienia ani stanu, ani licznika awarii."""
    breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=0.0, half_open_max_calls=1)
    _open_the_breaker(breaker, failures=5)
    assert breaker.try_acquire() is True
    assert breaker.try_acquire() is False

    breaker.release_probe()
    assert breaker.state is CircuitState.HALF_OPEN
    assert breaker.failure_count == 5
    assert breaker.try_acquire() is True


def _has_await(func: Any) -> bool:
    target = inspect.unwrap(func)
    return any(instruction.opname == "GET_AWAITABLE" for instruction in dis.get_instructions(target))


@pytest.mark.parametrize(
    "name",
    ["try_acquire", "release_success", "release_failure", "release_probe", "reset"],
)
def test_critical_section_contains_no_await(name: str) -> None:
    """D10, kryterium 5.4: atomowość bez asyncio.Lock stoi na braku await.

    Dodanie await do sekcji krytycznej otworzyłoby okno przeplotu, którego nie
    strzeże żadna blokada — dlatego warunek jest przypięty testem, a nie komentarzem.
    """
    method = getattr(CircuitBreaker, name)
    assert not inspect.iscoroutinefunction(method)
    assert not _has_await(method)

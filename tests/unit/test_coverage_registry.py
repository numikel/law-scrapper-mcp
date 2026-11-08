"""CoverageRegistry: lazy measurement, the 24 h memory, one measurement at a time, the fallback."""

from __future__ import annotations

import asyncio
import logging

import pytest

from fake_coverage_provider import MEASURED_ON, Clock, FakeCoverageProvider, segment
from law_scrapper_mcp.services.coverage import (
    COVERAGE_TTL_SECONDS,
    FALLBACK_TTL_SECONDS,
    CoverageConflictError,
    CoverageRegistry,
)

pytestmark = pytest.mark.asyncio


def _registry(clock: Clock) -> CoverageRegistry:
    return CoverageRegistry(today=clock.today, monotonic=clock.monotonic)


async def test_registering_measures_nothing() -> None:
    provider = FakeCoverageProvider()
    registry = CoverageRegistry()

    registry.register(provider)

    assert registry.keys() == ("DU",)
    assert provider.measure_calls == []


async def test_the_first_question_measures_with_the_injected_date() -> None:
    clock = Clock()
    provider = FakeCoverageProvider()
    registry = _registry(clock)
    registry.register(provider)

    coverage = await registry.get("DU")

    assert coverage is not None
    assert coverage.origin == "measured"
    assert coverage.measured_on == MEASURED_ON
    assert provider.measure_calls == [MEASURED_ON]
    assert registry.today() == MEASURED_ON


async def test_an_unknown_key_is_none_and_measures_nothing() -> None:
    provider = FakeCoverageProvider()
    registry = CoverageRegistry()
    registry.register(provider)

    assert await registry.get("MP") is None
    assert provider.measure_calls == []


async def test_the_answer_is_remembered_for_a_day_and_not_a_second_longer() -> None:
    clock = Clock()
    provider = FakeCoverageProvider()
    registry = _registry(clock)
    registry.register(provider)

    await registry.get("DU")
    clock.advance(COVERAGE_TTL_SECONDS - 1)
    await registry.get("DU")
    assert len(provider.measure_calls) == 1

    clock.advance(1)
    await registry.get("DU")
    assert len(provider.measure_calls) == 2


async def test_concurrent_questions_share_one_measurement() -> None:
    gate = asyncio.Event()
    provider = FakeCoverageProvider(gate=gate)
    registry = CoverageRegistry()
    registry.register(provider)

    pending = [asyncio.create_task(registry.get("DU")) for _ in range(5)]
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    gate.set()
    answers = await asyncio.gather(*pending)

    assert len(provider.measure_calls) == 1
    assert all(answer is answers[0] for answer in answers)


async def test_all_measures_providers_concurrently_and_in_key_order() -> None:
    gate = asyncio.Event()
    first = FakeCoverageProvider("MP", gate=gate)
    second = FakeCoverageProvider("DU", gate=gate)
    registry = CoverageRegistry()
    registry.register(first)
    registry.register(second)

    task = asyncio.create_task(registry.all())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    # Both providers are inside `measure` before either is released: the series is parallel.
    assert len(first.measure_calls) == len(second.measure_calls) == 1
    gate.set()
    coverages = await task

    assert [c.segments[0].key for c in coverages] == ["DU", "MP"]


async def test_a_failed_measurement_answers_with_the_fallback_and_logs(caplog: pytest.LogCaptureFixture) -> None:
    provider = FakeCoverageProvider(fail_measure=True)
    registry = CoverageRegistry()
    registry.register(provider)

    with caplog.at_level(logging.WARNING, logger="law_scrapper_mcp.services.coverage"):
        coverage = await registry.get("DU")

    assert coverage is not None
    assert coverage.origin == "fallback"
    assert provider.fallback_calls == 1
    assert "host down" in caplog.text


async def test_the_fallback_is_remembered_briefly_so_a_dead_host_is_not_asked_every_time() -> None:
    clock = Clock()
    provider = FakeCoverageProvider(fail_measure=True)
    registry = _registry(clock)
    registry.register(provider)

    await registry.get("DU")
    await registry.get("DU")
    assert len(provider.measure_calls) == 1

    clock.advance(FALLBACK_TTL_SECONDS)
    provider.fail_measure = False
    recovered = await registry.get("DU")

    assert len(provider.measure_calls) == 2
    assert recovered is not None
    assert recovered.origin == "measured"


async def test_a_provider_whose_fallback_fails_too_yields_none_and_is_remembered_briefly() -> None:
    clock = Clock()
    provider = FakeCoverageProvider(fail_measure=True, fail_fallback=True)
    registry = _registry(clock)
    registry.register(provider)

    assert await registry.get("DU") is None
    assert await registry.get("DU") is None
    assert len(provider.measure_calls) == 1

    clock.advance(FALLBACK_TTL_SECONDS)
    assert await registry.get("DU") is None
    assert len(provider.measure_calls) == 2


async def test_all_skips_a_provider_that_cannot_answer_at_all() -> None:
    registry = CoverageRegistry()
    registry.register(FakeCoverageProvider("DU"))
    registry.register(FakeCoverageProvider("MP", fail_measure=True, fail_fallback=True))

    coverages = await registry.all()

    assert [c.segments[0].key for c in coverages] == ["DU"]


async def test_cancellation_is_not_swallowed_and_does_not_poison_the_key() -> None:
    gate = asyncio.Event()
    provider = FakeCoverageProvider(gate=gate)
    registry = CoverageRegistry()
    registry.register(provider)

    task = asyncio.create_task(registry.get("DU"))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    gate.set()
    coverage = await registry.get("DU")
    assert coverage is not None
    assert coverage.origin == "measured"


async def test_a_second_provider_under_the_same_key_is_refused() -> None:
    registry = CoverageRegistry()
    registry.register(FakeCoverageProvider())

    with pytest.raises(CoverageConflictError):
        registry.register(FakeCoverageProvider(source="other"))

    assert registry.keys() == ("DU",)


async def test_unregister_forgets_the_provider_and_what_was_remembered() -> None:
    provider = FakeCoverageProvider()
    registry = CoverageRegistry()
    registry.register(provider)
    await registry.get("DU")

    registry.unregister("DU")
    registry.unregister("DU")  # an unknown key is ignored

    assert registry.keys() == ()
    assert await registry.get("DU") is None
    assert await registry.all() == []


async def test_a_provider_registered_again_is_measured_afresh() -> None:
    registry = CoverageRegistry()
    registry.register(FakeCoverageProvider(segments=(segment(count=1),)))
    await registry.get("DU")
    registry.unregister("DU")
    registry.register(FakeCoverageProvider(segments=(segment(count=2),)))

    coverage = await registry.get("DU")

    assert coverage is not None
    assert coverage.segments[0].count == 2


async def test_a_provider_unregistered_mid_measurement_leaves_nothing_behind() -> None:
    gate = asyncio.Event()
    provider = FakeCoverageProvider(gate=gate)
    registry = CoverageRegistry()
    registry.register(provider)

    task = asyncio.create_task(registry.get("DU"))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    registry.unregister("DU")
    gate.set()
    await task
    replacement = FakeCoverageProvider(segments=(segment(count=7),))
    registry.register(replacement)

    coverage = await registry.get("DU")

    assert coverage is not None
    assert coverage.segments[0].count == 7

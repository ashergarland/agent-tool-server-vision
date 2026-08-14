"""Admission control: concurrency limits, busy rejection, timeouts, draining."""

from __future__ import annotations

import asyncio

import pytest

from vision_server.concurrency import WorkQueue
from vision_server.errors import ErrorCode, VisionError


async def test_concurrency_is_bounded() -> None:
    queue = WorkQueue(2, 10, 5)
    running = 0
    peak = 0
    release = asyncio.Event()

    async def operation() -> int:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await release.wait()
        running -= 1
        return 1

    tasks = [asyncio.create_task(queue.run(operation)) for _ in range(4)]
    await asyncio.sleep(0)
    assert queue.stats["active"] == 2
    release.set()
    assert sum(await asyncio.gather(*tasks)) == 4
    assert peak == 2


async def test_queue_depth_rejects_with_a_retryable_busy_error() -> None:
    queue = WorkQueue(1, 1, 5)
    release = asyncio.Event()

    async def operation() -> None:
        await release.wait()

    first = asyncio.create_task(queue.run(operation))
    await asyncio.sleep(0)
    waiting = asyncio.create_task(queue.run(operation))
    await asyncio.sleep(0)

    with pytest.raises(VisionError) as error:
        await queue.run(operation)
    assert error.value.code is ErrorCode.BUSY
    assert error.value.retryable is True

    release.set()
    await asyncio.gather(first, waiting)


async def test_operations_are_bounded_by_a_timeout() -> None:
    queue = WorkQueue(1, 1, 0.01)

    async def slow() -> None:
        await asyncio.sleep(1)

    with pytest.raises(VisionError) as error:
        await queue.run(slow)
    assert error.value.code is ErrorCode.TIMEOUT
    assert error.value.retryable is True
    assert queue.stats == {"active": 0, "waiting": 0}


async def test_drain_waits_for_in_flight_work_then_closes() -> None:
    queue = WorkQueue(1, 4, 5)
    release = asyncio.Event()
    finished = False

    async def operation() -> None:
        nonlocal finished
        await release.wait()
        finished = True

    task = asyncio.create_task(queue.run(operation))
    await asyncio.sleep(0)
    drain = asyncio.create_task(queue.drain(2))
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(task, drain)
    assert finished is True

    with pytest.raises(VisionError) as error:
        await queue.run(operation)
    assert error.value.code is ErrorCode.BUSY


async def test_drain_gives_up_after_the_grace_period() -> None:
    queue = WorkQueue(1, 4, 5)

    async def operation() -> None:
        await asyncio.sleep(0.2)

    task = asyncio.create_task(queue.run(operation))
    await asyncio.sleep(0)
    await queue.drain(0.01)
    await task

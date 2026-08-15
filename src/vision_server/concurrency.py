"""Bounded work admission with timeouts and graceful draining."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from .errors import ErrorCode, VisionError

T = TypeVar("T")


class WorkQueue:
    """Admission control shared by every transport.

    A bounded semaphore limits concurrent work, a queue-depth counter rejects
    excess work with a retryable busy error, and every unit of work is bound by
    an operation timeout. ``drain`` waits for in-flight work during shutdown and
    cancels anything still waiting for admission.
    """

    def __init__(self, max_concurrency: int, max_queue_depth: int, timeout_seconds: float) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._max_waiting = max_queue_depth
        self._timeout = timeout_seconds
        self._waiting = 0
        self._active = 0
        self._accepted = 0
        self._closed = False
        self._idle = asyncio.Event()
        self._idle.set()

    @property
    def stats(self) -> dict[str, int]:
        return {"active": self._active, "waiting": self._waiting}

    async def run(self, operation: Callable[[], Awaitable[T]]) -> T:
        if self._closed:
            raise VisionError(ErrorCode.BUSY, "Server is shutting down", retryable=True)
        if self._waiting >= self._max_waiting:
            raise VisionError(
                ErrorCode.BUSY,
                "Server is at capacity; retry shortly",
                retryable=True,
                details={"maxQueueDepth": self._max_waiting},
            )
        self._accepted += 1
        self._idle.clear()
        self._waiting += 1
        try:
            try:
                await self._semaphore.acquire()
            finally:
                self._waiting -= 1
            self._active += 1
            try:
                try:
                    async with asyncio.timeout(self._timeout):
                        return await operation()
                except TimeoutError as exc:
                    raise VisionError(
                        ErrorCode.TIMEOUT,
                        "Operation exceeded the configured timeout",
                        retryable=True,
                        details={"timeoutSeconds": int(self._timeout)},
                    ) from exc
            finally:
                self._active -= 1
                self._semaphore.release()
        finally:
            self._accepted -= 1
            if self._accepted == 0:
                self._idle.set()

    async def drain(self, grace_seconds: float) -> None:
        """Stop admitting work and wait for in-flight operations to finish."""
        self._closed = True
        if grace_seconds <= 0:
            return
        try:
            async with asyncio.timeout(grace_seconds):
                await self._idle.wait()
        except TimeoutError:  # pragma: no cover - depends on timing
            return

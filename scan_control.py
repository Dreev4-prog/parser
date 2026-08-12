from __future__ import annotations

import asyncio
from typing import TypeVar

T = TypeVar("T")


class ScanStopRequested(Exception):
    """Raised in a job waiter when the user explicitly stops that scan."""


async def wait_for_task_or_stop(task: asyncio.Task[T], stop_event: asyncio.Event | None) -> T:
    """Wait for a shared task while allowing one consumer to detach immediately.

    The shared task itself is intentionally *not* cancelled here. The caller owns
    subscriber accounting and may cancel the underlying task only when no other
    scan is still waiting for it.
    """
    if stop_event is None:
        return await asyncio.shield(task)
    if stop_event.is_set():
        raise ScanStopRequested()

    stop_waiter = asyncio.create_task(stop_event.wait(), name="scan-stop-waiter")
    try:
        done, _ = await asyncio.wait(
            {task, stop_waiter},
            return_when=asyncio.FIRST_COMPLETED,
        )
        # A user stop wins a race with task completion. This prevents a result from
        # being attached to a scan after the user has already pressed Stop.
        if stop_waiter in done and stop_event.is_set():
            raise ScanStopRequested()
        return await asyncio.shield(task)
    finally:
        if not stop_waiter.done():
            stop_waiter.cancel()
        await asyncio.gather(stop_waiter, return_exceptions=True)

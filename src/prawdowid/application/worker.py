"""Coordinate job handlers without holding a persistence transaction."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Any, Generic, Protocol, TypeVar

Effect = TypeVar("Effect")
QueueEffect_contra = TypeVar("QueueEffect_contra", contravariant=True)


@dataclass(frozen=True)
class JobLease:
    id: str
    kind: str
    dedupe_key: str
    generation: int
    payload: dict[str, Any]
    worker_id: str
    attempt: int
    lease_until: datetime


class JobFailure(Exception):
    def __init__(self, error_code: str, *, retryable: bool = True) -> None:
        super().__init__(error_code)
        self.error_code = error_code
        self.retryable = retryable


class LeaseQueue(Protocol[QueueEffect_contra]):
    async def lease_next(self, worker_id: str) -> JobLease | None: ...

    async def heartbeat(self, lease: JobLease) -> bool: ...

    async def complete(self, lease: JobLease, effect: QueueEffect_contra | None = None) -> bool: ...

    async def fail(self, lease: JobLease, error_code: str, *, retryable: bool) -> bool: ...


class JobWorker(Generic[Effect]):
    def __init__(
        self,
        queue: LeaseQueue[Effect],
        worker_id: str,
        handler: Callable[[JobLease], Awaitable[Effect | None]],
        *,
        heartbeat_seconds: float | None = 10,
    ) -> None:
        if heartbeat_seconds is not None and (
            not isfinite(heartbeat_seconds) or heartbeat_seconds <= 0
        ):
            raise ValueError("heartbeat_seconds must be finite and positive")
        self.queue = queue
        self.worker_id = worker_id
        self.handler = handler
        self.heartbeat_seconds = heartbeat_seconds

    async def run_once(self) -> bool:
        lease = await self.queue.lease_next(self.worker_id)
        if lease is None:
            return False

        if self.heartbeat_seconds is None:
            effect, failure = await self._call_handler(lease)
        else:
            work = asyncio.create_task(self._call_handler(lease))
            renew = asyncio.create_task(self._renew(lease, self.heartbeat_seconds))
            try:
                done, _ = await asyncio.wait({work, renew}, return_when=asyncio.FIRST_COMPLETED)
                if renew in done:
                    await renew
                    return True
                effect, failure = await work
            finally:
                for task in (work, renew):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(work, renew, return_exceptions=True)

        if failure is None:
            await self.queue.complete(lease, effect)
        else:
            await self.queue.fail(lease, failure.error_code, retryable=failure.retryable)
        return True

    async def run_batch(self, max_jobs: int) -> int:
        if max_jobs < 1:
            raise ValueError("max_jobs must be positive")
        for count in range(max_jobs):
            if not await self.run_once():
                return count
        return max_jobs

    async def _call_handler(self, lease: JobLease) -> tuple[Effect | None, JobFailure | None]:
        try:
            return await self.handler(lease), None
        except JobFailure as failure:
            return None, failure

    async def _renew(self, lease: JobLease, interval: float) -> None:
        while True:
            await asyncio.sleep(interval)
            if not await self.queue.heartbeat(lease):
                return

import asyncio
from contextlib import asynccontextmanager

from gateway.metrics import ACTIVE_REQUESTS, QUEUE_WAITING


class QueueFull(Exception):
    pass


class QueueTimeout(Exception):
    pass


class RequestQueue:
    """
    Semaphore-backed concurrency limiter with a bounded waiting queue.
    Callers acquire a slot via the context manager; the semaphore limits
    simultaneous upstream calls while extra requests wait (up to max_queue_size).
    """

    def __init__(self, max_concurrent: int, max_queue_size: int) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._max_queue = max_queue_size
        self._waiting = 0
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def acquire(self, timeout: float):
        async with self._lock:
            if self._waiting >= self._max_queue:
                raise QueueFull("Request queue is full")
            self._waiting += 1
            QUEUE_WAITING.inc()

        try:
            try:
                await asyncio.wait_for(self._semaphore.acquire(), timeout=timeout)
            except asyncio.TimeoutError:
                raise QueueTimeout("Timed out waiting for a free model slot")

            async with self._lock:
                self._waiting -= 1
                QUEUE_WAITING.dec()

            ACTIVE_REQUESTS.inc()
            try:
                yield
            finally:
                ACTIVE_REQUESTS.dec()
                self._semaphore.release()
        except (QueueFull, QueueTimeout):
            raise
        except Exception:
            # Ensure waiting counter stays consistent on unexpected errors
            async with self._lock:
                if self._waiting > 0:
                    self._waiting -= 1
                    QUEUE_WAITING.dec()
            raise

from __future__ import annotations

import asyncio
from typing import Any, Callable, Coroutine


class Scheduler:
    def __init__(self):
        self._tasks: dict[int, asyncio.Task] = {}
        self._counter = 0

    async def schedule(
        self,
        delay: float,
        callback: Callable[..., Coroutine[Any, Any, Any]] | Callable[..., Any],
        *args: Any,
        interval: float = 0.0,
        **kwargs: Any,
    ) -> int:
        self._counter += 1
        task_id = self._counter
        task = asyncio.create_task(self._run(task_id, delay, callback, args, kwargs, interval))
        self._tasks[task_id] = task
        return task_id

    async def schedule_interval(
        self,
        interval: float,
        callback: Callable[..., Coroutine[Any, Any, Any]] | Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> int:
        return await self.schedule(interval, callback, *args, interval=interval, **kwargs)

    async def _run(
        self,
        task_id: int,
        delay: float,
        callback: Callable[..., Coroutine[Any, Any, Any]] | Callable[..., Any],
        args: tuple,
        kwargs: dict,
        interval: float,
    ) -> None:
        await asyncio.sleep(delay)
        while task_id in self._tasks:
            try:
                result = callback(*args, **kwargs)
                if hasattr(result, "__await__"):
                    await result
            except Exception:
                pass
            if interval <= 0:
                break
            await asyncio.sleep(interval)

    def cancel(self, task_id: int) -> None:
        task = self._tasks.pop(task_id, None)
        if task and not task.done():
            task.cancel()

    def stop(self) -> None:
        for tid in list(self._tasks):
            self.cancel(tid)

from __future__ import annotations

import time
from typing import Any

from rpieasy2.core.plugin_task_data import (
    PluginTaskDataBase,
    get_plugin_task_data,
    init_plugin_task_data,
)


class TimingStats(PluginTaskDataBase):
    def __init__(self) -> None:
        self.count: int = 0
        self.min: float = 0.0
        self.max: float = 0.0
        self.total: float = 0.0
        self.last: float = 0.0

    def push(self, elapsed: float) -> None:
        if self.count == 0 or elapsed < self.min:
            self.min = elapsed
        if self.count == 0 or elapsed > self.max:
            self.max = elapsed
        self.total += elapsed
        self.last = elapsed
        self.count += 1

    @property
    def avg(self) -> float:
        if self.count == 0:
            return 0.0
        return self.total / self.count

    def to_dict(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "min": round(self.min, 4),
            "max": round(self.max, 4),
            "avg": round(self.avg, 4),
            "last": round(self.last, 4),
        }


def get_timing_stats(task_index: int) -> TimingStats | None:
    data = get_plugin_task_data(task_index)
    if isinstance(data, TimingStats):
        return data
    return None


def init_timing_stats(task_index: int) -> TimingStats:
    stats = TimingStats()
    init_plugin_task_data(task_index, stats)
    return stats

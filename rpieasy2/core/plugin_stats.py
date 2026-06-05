from __future__ import annotations

from typing import Any

from rpieasy2.core.plugin_task_data import (
    PluginTaskDataBase,
    get_plugin_task_data,
    init_plugin_task_data,
)


class PluginStats(PluginTaskDataBase):
    def __init__(self) -> None:
        self.count: int = 0
        self.min: dict[str, float] = {}
        self.max: dict[str, float] = {}
        self.total: dict[str, float] = {}
        self.last: dict[str, float] = {}

    def push(self, values: dict[str, float | str | int]) -> None:
        for name, val in values.items():
            try:
                v = float(val)
            except (ValueError, TypeError):
                continue
            if name not in self.min or v < self.min[name]:
                self.min[name] = v
            if name not in self.max or v > self.max[name]:
                self.max[name] = v
            self.total[name] = self.total.get(name, 0) + v
            self.last[name] = v
        self.count += 1

    def get_avg(self, name: str) -> float:
        if self.count == 0 or name not in self.total:
            return 0.0
        return self.total[name] / self.count

    def to_dict(self) -> dict[str, Any]:
        keys: set[str] = set()
        keys.update(self.min.keys(), self.max.keys(), self.total.keys(), self.last.keys())
        result: dict[str, Any] = {"count": self.count}
        for k in sorted(keys):
            result[k] = {
                "min": self.min.get(k, 0),
                "max": self.max.get(k, 0),
                "avg": self.get_avg(k),
                "last": self.last.get(k, 0),
            }
        return result


def get_plugin_stats(task_index: int) -> PluginStats | None:
    data = get_plugin_task_data(task_index)
    if isinstance(data, PluginStats):
        return data
    return None


def init_plugin_stats(task_index: int) -> PluginStats:
    stats = PluginStats()
    init_plugin_task_data(task_index, stats)
    return stats

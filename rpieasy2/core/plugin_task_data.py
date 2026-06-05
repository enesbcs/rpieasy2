from __future__ import annotations

from typing import Any

_plugin_task_data: dict[int, "PluginTaskDataBase"] = {}


class PluginTaskDataBase:
    pass


def get_plugin_task_data(task_index: int) -> PluginTaskDataBase | None:
    return _plugin_task_data.get(task_index)


def init_plugin_task_data(task_index: int, data: PluginTaskDataBase) -> None:
    _plugin_task_data[task_index] = data


def clear_plugin_task_data(task_index: int) -> None:
    _plugin_task_data.pop(task_index, None)


def clear_all_plugin_task_data() -> None:
    _plugin_task_data.clear()

from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_QUAD
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p180")


def _parse_val(data: list[int], fmt: str) -> int | float:
    if fmt in ("u8", "b"):
        return data[0] if data else 0
    elif fmt in ("u16",):
        return (data[0] << 8) | data[1] if len(data) >= 2 else 0
    elif fmt in ("u16le",):
        return data[0] | (data[1] << 8) if len(data) >= 2 else 0
    elif fmt in ("u24",):
        return (data[0] << 16) | (data[1] << 8) | data[2] if len(data) >= 3 else 0
    elif fmt in ("u32",):
        return (data[0] << 24) | (data[1] << 16) | (data[2] << 8) | data[3] if len(data) >= 4 else 0
    elif fmt in ("8",):
        v = data[0] if data else 0
        return v - 256 if v > 127 else v
    elif fmt in ("16",):
        v = (data[0] << 8) | data[1] if len(data) >= 2 else 0
        return v - 65536 if v > 32767 else v
    return data[0] if data else 0


class P180I2CGeneric(PluginBase):
    PLUGIN_ID = 180
    PLUGIN_NAME = "Generic - I2C Generic"
    PLUGIN_VALUES = 4
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_I2C,
        vtype=SENSOR_TYPE_QUAD,
        value_count=4,
        send_data_option=True,
        timer_option=True,
        timer_optional=True,
        formula_option=True,
        plugin_stats=True,
    )
    I2C_ADDRESSES: list[int] = []

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._addr: int = 0

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        try:
            self._addr = int(str(self._config.get("address", "0x00")), 16) if isinstance(self._config.get("address"), str) else int(self._config.get("address", 0))
        except (ValueError, TypeError):
            self._addr = 0
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("address", "0x00")
        self._config.setdefault("cmds", ["", "", "", ""])
        self._config.setdefault("fmt", ["u8", "u8", "u8", "u8"])
        self._config.setdefault("reg", [0, 0, 0, 0])
        self._config.setdefault("len", [1, 1, 1, 1])
        return True

    async def _exec_cmd(self, cmd: str, fmt: str, reg: int, length: int) -> int | float:
        if not cmd or not self._hw:
            return 0
        i2c = self._hw.i2c
        parts = cmd.split(".")
        op = parts[0] if parts else "r"
        reg_val = reg
        fmt_val = fmt
        len_val = length
        if op == "r" or op == "read":
            d = await i2c.read_i2c_block_data(self._addr, reg_val, len_val)
            return _parse_val(d, fmt_val)
        elif op == "w" or op == "write":
            d_val = int(parts[-1], 16) if len(parts) > 2 and parts[-1] else 0
            d_bytes = [d_val & 0xFF]
            if fmt_val in ("u16",):
                d_bytes = [(d_val >> 8) & 0xFF, d_val & 0xFF]
            await i2c.write_i2c_block_data(self._addr, reg_val, d_bytes)
            return d_val
        elif op == "g" or op == "get":
            d = await i2c.read_i2c_block_data(self._addr, 0x00, len_val)
            return _parse_val(d, fmt_val)
        elif op == "p" or op == "put":
            d_val = int(parts[-1], 16) if len(parts) > 2 else 0
            d_bytes = [d_val & 0xFF]
            if fmt_val in ("u16",):
                d_bytes = [(d_val >> 8) & 0xFF, d_val & 0xFF]
            await i2c.write_i2c_block_data(self._addr, 0x00, d_bytes)
            return d_val
        return 0

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw or not self._addr:
            return False
        try:
            cmds = self._config.get("cmds", ["", "", "", ""])
            fmts = self._config.get("fmt", ["u8", "u8", "u8", "u8"])
            regs = self._config.get("reg", [0, 0, 0, 0])
            lens = self._config.get("len", [1, 1, 1, 1])
            vals: dict[str, Any] = {}
            for i in range(4):
                if cmds[i]:
                    v = await self._exec_cmd(cmds[i], str(fmts[i]), int(regs[i]), int(lens[i]))
                    vals[f"Value{i + 1}"] = v
                else:
                    vals[f"Value{i + 1}"] = 0
            event.data["values"] = vals
            return True
        except Exception as e:
            logger.error("I2C Generic read failed: %s", e)
            return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        fmt_opts = [
            {"value": "u8", "label": "u8 (1 byte)"},
            {"value": "u16", "label": "u16 (2 bytes)"},
            {"value": "u24", "label": "u24 (3 bytes)"},
            {"value": "u32", "label": "u32 (4 bytes)"},
            {"value": "u16le", "label": "u16 LE"},
            {"value": "b", "label": "bytes"},
            {"value": "8", "label": "int8"},
            {"value": "16", "label": "int16"},
            {"value": "g", "label": "get (read raw)"},
            {"value": "p", "label": "put (write raw)"},
        ]
        form = [
            {"name": "address", "label": "I2C Address (Hex)", "type": "text", "value": self._config.get("address", "0x00")},
        ]
        for i in range(4):
            form.append({"name": f"cmd_{i}", "label": f"Value {i + 1} I2C Cmd", "type": "text", "value": self._config.get("cmds", ["", "", "", ""])[i]})
            form.append({"name": f"fmt_{i}", "label": f"Value {i + 1} Format", "type": "select", "value": self._config.get("fmt", ["u8", "u8", "u8", "u8"])[i], "options": fmt_opts})
            form.append({"name": f"reg_{i}", "label": f"Value {i + 1} Register", "type": "number", "value": self._config.get("reg", [0, 0, 0, 0])[i]})
            form.append({"name": f"len_{i}", "label": f"Value {i + 1} Length", "type": "number", "value": self._config.get("len", [1, 1, 1, 1])[i]})
        event.data["form"] = form
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        fd = event.data.get("form_data", {})
        self._config["address"] = fd.get("address", "0x00")
        cmds = []
        fmts = []
        regs = []
        lens = []
        for i in range(4):
            cmds.append(fd.get(f"cmd_{i}", ""))
            fmts.append(fd.get(f"fmt_{i}", "u8"))
            regs.append(int(fd.get(f"reg_{i}", 0)))
            lens.append(int(fd.get(f"len_{i}", 1)))
        self._config["cmds"] = cmds
        self._config["fmt"] = fmts
        self._config["reg"] = regs
        self._config["len"] = lens
        try:
            self._addr = int(str(self._config["address"]), 16)
        except ValueError:
            self._addr = 0
        return True

    async def on_plugin_i2c_has_address(self, event: Event) -> bool | None:
        return True

    async def on_plugin_i2c_get_address(self, event: Event) -> bool | None:
        event.data["address"] = self._addr
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Value1": 0, "Value2": 0, "Value3": 0, "Value4": 0}

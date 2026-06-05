from __future__ import annotations

import logging
from typing import Any

from rpieasy2.core.events import Event, get_event_bus
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_QUAD
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p050")

TCS34725_ADDR = 0x29
TCS34725_CMD = 0x80
TCS34725_REG_ENABLE = 0x00
TCS34725_REG_ATIME = 0x01
TCS34725_REG_CONTROL = 0x0F
TCS34725_REG_CDATA = 0x14

INTEGRATION_CYCLES = {0: 0xFF, 1: 0xF6, 2: 0xEB, 3: 0xD5, 4: 0xC0, 5: 0x00}
GAIN_VALUES = {0: 0x00, 1: 0x01, 2: 0x02, 3: 0x03}


def _calc_color_temp(r: float, g: float, b: float, c: float, dn40: bool = False) -> float:
    if c == 0:
        return 0.0
    ir = (r + g + b - c) / 2 if c > (r + g + b) else 0.0
    r2 = max(0, r - ir)
    b2 = max(0, b - ir)
    if b2 == 0:
        return 0.0
    ratio = r2 / b2
    if dn40:
        return 0.0
    ct = 449.0 * (ratio ** 3) - 1352.0 * (ratio ** 2) + 2821.0 * ratio - 852.0 * pow(ratio, 1.5) + 3572.0
    return max(1000, min(15000, ct))


def _calc_lux(r: float, g: float, b: float, c: float) -> float:
    if c == 0:
        return 0.0
    return c * 1.0


def _apply_rgb_mode(r: float, g: float, b: float, c: float, mode: int) -> dict[str, float]:
    if mode == 0:
        return {"Red": r, "Green": g, "Blue": b}
    if mode == 1:
        s = r + g + b
        if s == 0:
            return {"Red": 0, "Green": 0, "Blue": 0}
        return {"Red": r / s * 255, "Green": g / s * 255, "Blue": b / s * 255}
    if mode == 2:
        return {"Red": r / max(1, c) * 255, "Green": g / max(1, c) * 255, "Blue": b / max(1, c) * 255}
    if mode == 3:
        mx = max(r, g, b)
        if mx == 0:
            return {"Red": 0, "Green": 0, "Blue": 0}
        return {"Red": r / mx * 255, "Green": g / mx * 255, "Blue": b / mx * 255}
    if mode == 4:
        r_ = max(0, r - c * 0.5)
        g_ = max(0, g - c * 0.5)
        b_ = max(0, b - c * 0.5)
        return {"Red": r_, "Green": g_, "Blue": b_}
    return {"Red": r, "Green": g, "Blue": b}


class P050TCS34725(PluginBase):
    PLUGIN_ID = 50
    PLUGIN_NAME = "Color - TCS34725"
    PLUGIN_VALUES = 4
    DEVICE_PROPERTIES = DeviceProperties(type=DEVICE_TYPE_I2C, vtype=SENSOR_TYPE_QUAD, value_count=4, formula_option=True, send_data_option=True, plugin_stats=True, custom_vtype_var=True)

    def __init__(self):
        super().__init__()
        self._addr: int = TCS34725_ADDR
        self._config: dict[str, Any] = {}

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._addr = self._config.get("address", TCS34725_ADDR)
        if not self._hw:
            return False
        try:
            i2c = self._hw.i2c
            integration = int(self._config.get("integration_time") or 3)
            gain = int(self._config.get("gain") or 0)
            await i2c.write_byte_data(self._addr, TCS34725_CMD | TCS34725_REG_ATIME,
                                      INTEGRATION_CYCLES.get(integration, 0xD5))
            await i2c.write_byte_data(self._addr, TCS34725_CMD | TCS34725_REG_CONTROL,
                                      GAIN_VALUES.get(gain, 0x00))
            await i2c.write_byte_data(self._addr, TCS34725_CMD | TCS34725_REG_ENABLE, 0x03)
        except Exception as e:
            logger.error("TCS34725 init failed: %s", e)
            return False
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        return True

    async def _rw(self, reg: int) -> int:
        if not self._hw:
            return 0
        d = await self._hw.i2c.read_i2c_block_data(self._addr, TCS34725_CMD | reg, 2)
        return (d[1] << 8) | d[0]

    def _apply_matrix(self, rgb: tuple[float, float, float]) -> tuple[float, float, float]:
        r, g, b = rgb
        m = [float(self._config.get(f"m{i}") or (1.0 if i in (0, 4, 8) else 0.0)) for i in range(9)]
        return (
            r * m[0] + g * m[1] + b * m[2],
            r * m[3] + g * m[4] + b * m[5],
            r * m[6] + g * m[7] + b * m[8],
        )

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw:
            return False
        try:
            clear_val = await self._rw(TCS34725_REG_CDATA)
            red_val = await self._rw(TCS34725_REG_CDATA + 2)
            green_val = await self._rw(TCS34725_REG_CDATA + 4)
            blue_val = await self._rw(TCS34725_REG_CDATA + 6)
            r, g, b = self._apply_matrix((red_val, green_val, blue_val))
            mode = int(self._config.get("output_rgb") or 0)
            rgb_vals = _apply_rgb_mode(r, g, b, clear_val, mode)
            out4 = int(self._config.get("output_4") or 0)
            if out4 == 0:
                v4 = round(_calc_color_temp(r, g, b, clear_val), 1)
            elif out4 == 1:
                v4 = round(_calc_color_temp(r, g, b, clear_val, dn40=True), 1)
            elif out4 == 2:
                v4 = round(_calc_lux(r, g, b, clear_val), 2)
            else:
                v4 = round(clear_val, 1)
            vals = {
                rgb_vals.get("Red", 0) if isinstance(rgb_vals.get("Red"), (int, float)) else f"V1": rgb_vals.get("Red", 0),
                rgb_vals.get("Green", 0) if isinstance(rgb_vals.get("Green", 0), (int, float)) else "V2": rgb_vals.get("Green", 0),
                rgb_vals.get("Blue", 0) if isinstance(rgb_vals.get("Blue", 0), (int, float)) else "V3": rgb_vals.get("Blue", 0),
            }
            vals = {"Red": rgb_vals.get("Red", 0), "Green": rgb_vals.get("Green", 0),
                    "Blue": rgb_vals.get("Blue", 0), "V4": v4}
            event.data["values"] = vals
            if self._config.get("all_events", False):
                eb = get_event_bus()
                for k, v in vals.items():
                    ev = Event(type="PLUGIN_VALUE_CHANGE", task_index=event.task_index,
                               data={"value_name": k, "value": v})
                    await eb.publish(ev)
            return True
        except Exception as e:
            logger.error("TCS34725 read failed: %s", e)
            return False

    async def on_plugin_get_device_value_names(self, event: Event) -> bool | None:
        event.data["value_names"] = ["Red", "Green", "Blue", "V4"]
        return True

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        form = [
            {"name": "address", "label": "I2C Address", "type": "select", "value": self._config.get("address", TCS34725_ADDR), "options": [
                {"value": 0x29, "label": "0x29"},
            ]},
            {"name": "integration_time", "label": "Integration Time", "type": "select", "value": self._config.get("integration_time", 3), "options": [
                {"value": 0, "label": "2.4 ms"}, {"value": 1, "label": "24 ms"},
                {"value": 2, "label": "50 ms"}, {"value": 3, "label": "101 ms"},
                {"value": 4, "label": "154 ms"}, {"value": 5, "label": "700 ms"},
            ]},
            {"name": "gain", "label": "Gain", "type": "select", "value": self._config.get("gain", 0), "options": [
                {"value": 0, "label": "1x"}, {"value": 1, "label": "4x"},
                {"value": 2, "label": "16x"}, {"value": 3, "label": "60x"},
            ]},
            {"name": "output_rgb", "label": "Output RGB Select", "type": "select", "value": self._config.get("output_rgb", 0), "options": [
                {"value": 0, "label": "Raw"}, {"value": 1, "label": "Normalized"},
                {"value": 2, "label": "Clear Normalized"}, {"value": 3, "label": "Max Normalized"},
                {"value": 4, "label": "IR Subtract"}, {"value": 5, "label": "Scaled"},
            ]},
            {"name": "output_4", "label": "Output 4 Select", "type": "select", "value": self._config.get("output_4", 0), "options": [
                {"value": 0, "label": "Color Temperature"},
                {"value": 1, "label": "Color Temp DN40"},
                {"value": 2, "label": "Lux"},
                {"value": 3, "label": "Clear"},
            ]},
            {"name": "all_events", "label": "Generate All as Events", "type": "checkbox", "value": self._config.get("all_events", False)},
            {"name": "rgb_events", "label": "Generate RGB Events", "type": "checkbox", "value": self._config.get("rgb_events", False)},
        ]
        for i in range(9):
            form.append({"name": f"m{i}", "label": f"Matrix M{i}", "type": "number", "value": self._config.get(f"m{i}", 1.0 if i in (0, 4, 8) else 0.0), "step": "0.001"})
        event.data["form"] = form
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_get_devicevaluecount(self, event: Event) -> bool | None:
        return None

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        return None

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Red": 0, "Green": 0, "Blue": 0, "V4": 0}

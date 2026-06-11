from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_SINGLE
from rpieasy2.core.device_properties import DeviceProperties, OutputDataType

logger = logging.getLogger("rpieasy2.plugin.p025")

ADS1X15_ADDR = 0x48
ADS1X15_REG_CONV = 0x00
ADS1X15_REG_CONFIG = 0x01

FSR_VALUES = [6.144, 4.096, 2.048, 1.024, 0.512, 0.256]

SPS_1115 = [8, 16, 32, 64, 128, 250, 475, 860]
SPS_1015 = [128, 250, 490, 920, 1600, 2400, 3300, 3300]

MUX_LABELS = [
    "AIN0 - GND (Single-Ended)",
    "AIN1 - GND (Single-Ended)",
    "AIN2 - GND (Single-Ended)",
    "AIN3 - GND (Single-Ended)",
    "AIN0 - AIN1 (Differential)",
    "AIN0 - AIN3 (Differential)",
    "AIN1 - AIN3 (Differential)",
    "AIN2 - AIN3 (Differential)",
]

SPS_LABELS = [
    "8 / 128",
    "16 / 250",
    "32 / 490",
    "64 / 920",
    "128 / 1600",
    "250 / 2400",
    "475 / 3300",
    "860 / 3300",
]


class P025ADS1x15(PluginBase):
    PLUGIN_ID = 25
    PLUGIN_NAME = "Analog input - ADS1x15"
    PLUGIN_VALUES = 4
    DEVICE_PROPERTIES = DeviceProperties(type=DEVICE_TYPE_I2C, vtype=SENSOR_TYPE_SINGLE, value_count=4, formula_option=True, send_data_option=True, timer_option=True, timer_optional=True, output_data_type=OutputDataType.SIMPLE, plugin_stats=True)

    def __init__(self):
        super().__init__()
        self._addr: int = ADS1X15_ADDR
        self._config: dict[str, Any] = {}
        self._oversample_buf: list[list[float]] = [[], [], [], []]

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._addr = self._config.get("address", ADS1X15_ADDR)
        return bool(self._hw)

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("address", ADS1X15_ADDR)
        self._config.setdefault("adc_type", 11)
        self._config.setdefault("gain", 1)
        self._config.setdefault("sps", 4)
        self._config.setdefault("oversampling", 0)
        self._config.setdefault("output_volt", 1)
        self._config.setdefault("cal_enable", 0)
        self._config.setdefault("cal_adc1", 0)
        self._config.setdefault("cal_out1", 0.0)
        self._config.setdefault("cal_adc2", 32767)
        self._config.setdefault("cal_out2", 32767.0)
        for i in range(4):
            self._config.setdefault(f"channel_{i}", 4 if i < 4 else -1)
            self._config.setdefault(f"cal_{i}", 1.0)
        return True

    def _get_enabled_count(self) -> int:
        for n in (4, 3, 2, 1):
            ch = int(self._config.get(f"channel_{n-1}") or n - 1)
            if ch >= 0:
                return n
        return 1

    def _is_ads1015(self) -> bool:
        return int(self._config.get("adc_type") or 11) == 10

    async def _wait_ready(self, dr: int) -> bool:
        i2c = self._hw.i2c
        is_1015 = self._is_ads1015()
        sps = SPS_1015[dr] if is_1015 else SPS_1115[dr]
        timeout_ms = 1500 / sps + 1 if sps > 0 else 100
        deadline = time.monotonic() + timeout_ms / 1000.0
        while time.monotonic() < deadline:
            d = await i2c.read_i2c_block_data(self._addr, ADS1X15_REG_CONFIG, 2)
            if d[0] & 0x80:
                return True
            await asyncio.sleep(max(0.0001, timeout_ms / 10000))
        logger.warning("ADS1x15 waitReady timeout (DR=%d, SPS=%d, timeout=%.1fms)", dr, sps, timeout_ms)
        return False

    async def _read_adc(self, channel: int) -> float:
        if not self._hw:
            return 0.0
        i2c = self._hw.i2c
        gain_idx = int(self._config.get("gain") or 1)
        mux = int(self._config.get(f"channel_{channel}") or (4 if channel < 4 else -1))
        if mux < 0:
            return 0.0
        is_1015 = self._is_ads1015()
        dr = max(0, min(7, int(self._config.get("sps", 4))))
        cfg = (0x8000 | 0x4000 | ((mux & 7) << 12) | ((gain_idx & 7) << 9)
               | 0x0100 | ((dr & 7) << 5) | 0x0003)
        await i2c.write_i2c_block_data(self._addr, ADS1X15_REG_CONFIG, [(cfg >> 8) & 0xFF, cfg & 0xFF])
        ready = await self._wait_ready(dr)
        if not ready:
            d = await i2c.read_i2c_block_data(self._addr, ADS1X15_REG_CONFIG, 2)
            logger.warning("ADS1x15: OS bit not set, reading anyway. Config=%02x%02x", d[0], d[1])
        d = await i2c.read_i2c_block_data(self._addr, ADS1X15_REG_CONV, 2)
        raw = (d[0] << 8) | d[1]
        if is_1015:
            raw >>= 4
        if raw > 0x07FF and is_1015:
            raw -= 0x1000
        elif raw > 0x7FFF and not is_1015:
            raw -= 0x10000
        max_val = 2048.0 if is_1015 else 32768.0
        if int(self._config.get("output_volt", 1)):
            value = raw * FSR_VALUES[gain_idx] / max_val
        else:
            value = float(raw)
        cal = float(self._config.get(f"cal_{channel}", 1.0))
        value *= cal
        if int(self._config.get("cal_enable", 0)):
            adc1 = float(self._config.get("cal_adc1", 0))
            out1 = float(self._config.get("cal_out1", 0.0))
            adc2 = float(self._config.get("cal_adc2", 32767))
            out2 = float(self._config.get("cal_out2", 32767.0))
            if abs(adc2 - adc1) > 1e-9:
                normalized = (value - adc1) / (adc2 - adc1)
                value = normalized * (out2 - out1) + out1
        return value

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw:
            return False
        try:
            n = self._get_enabled_count()
            oversamp = int(self._config.get("oversampling") or 0)
            vals: dict[str, float] = {}
            for i in range(n):
                v = await self._read_adc(i)
                if oversamp > 0:
                    self._oversample_buf[i].append(v)
                    if len(self._oversample_buf[i]) > oversamp:
                        self._oversample_buf[i].pop(0)
                    v = sum(self._oversample_buf[i]) / len(self._oversample_buf[i])
                vals[f"AIN{i}"] = round(v, 4)
            event.data["values"] = vals
            return True
        except Exception as e:
            logger.error("ADS1x15 read failed: %s", e)
            return False

    async def on_plugin_get_devicevaluecount(self, event: Event) -> bool | None:
        event.data["value_count"] = self._get_enabled_count()
        return True

    async def on_plugin_get_discovery_vtypes(self, event: Event) -> bool | None:
        n = self._get_enabled_count()
        event.data["vtypes"] = [SENSOR_V_TYPE_SINGLE] * n
        return True

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        n = self._get_enabled_count()
        if n == 1:
            from rpieasy2.core.rpiconst import SENSOR_TYPE_SINGLE
            event.data["vtype"] = SENSOR_TYPE_SINGLE
        elif n == 2:
            from rpieasy2.core.rpiconst import SENSOR_TYPE_DUAL
            event.data["vtype"] = SENSOR_TYPE_DUAL
        else:
            from rpieasy2.core.rpiconst import SENSOR_TYPE_QUAD
            event.data["vtype"] = SENSOR_TYPE_QUAD
        return True

    async def on_plugin_get_device_value_names(self, event: Event) -> bool | None:
        n = self._get_enabled_count()
        event.data["value_names"] = [f"AIN{i}" for i in range(n)]
        return True

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        mux_options = [{"value": i, "label": MUX_LABELS[i]} for i in range(8)]
        mux_options.append({"value": -1, "label": "Disabled"})
        sps_opts = [{"value": i, "label": f"{SPS_LABELS[i]} SPS"} for i in range(8)]
        gain_opts = [
            {"value": 0, "label": "2/3x gain (FS=6.144V)"},
            {"value": 1, "label": "1x gain (FS=4.096V)"},
            {"value": 2, "label": "2x gain (FS=2.048V)"},
            {"value": 3, "label": "4x gain (FS=1.024V)"},
            {"value": 4, "label": "8x gain (FS=0.512V)"},
            {"value": 5, "label": "16x gain (FS=0.256V)"},
        ]
        form = [
            {"name": "address", "label": "I2C Address", "type": "select", "value": self._config.get("address", ADS1X15_ADDR), "options": [
                {"value": 0x48, "label": "0x48"}, {"value": 0x49, "label": "0x49"},
                {"value": 0x4A, "label": "0x4A"}, {"value": 0x4B, "label": "0x4B"},
            ]},
            {"name": "adc_type", "label": "ADC Type", "type": "select", "value": self._config.get("adc_type", 11), "options": [
                {"value": 10, "label": "ADS1015 (12-bit)"},
                {"value": 11, "label": "ADS1115 (16-bit)"},
            ]},
            {"name": "gain", "label": "Gain (PGA)", "type": "select", "value": self._config.get("gain", 1), "options": gain_opts},
            {"name": "sps", "label": "Sample Rate", "type": "select", "value": self._config.get("sps", 4), "options": sps_opts},
            {"name": "oversampling", "label": "Oversampling (samples)", "type": "select", "value": self._config.get("oversampling", 0), "options": [
                {"value": 0, "label": "Off"},
                {"value": 3, "label": "3 samples"},
                {"value": 5, "label": "5 samples"},
                {"value": 10, "label": "10 samples"},
                {"value": 20, "label": "20 samples"},
            ]},
            {"name": "output_volt", "label": "Convert to Volt", "type": "checkbox", "value": int(self._config.get("output_volt", 1))},
        ]
        for i in range(4):
            ch = self._config.get(f"channel_{i}", 4 if i < 4 else -1)
            form.append({"name": f"channel_{i}", "label": f"Channel {i} Input", "type": "select", "value": ch if ch is not None else 4, "options": mux_options})
            cal = self._config.get(f"cal_{i}", 1.0)
            form.append({"name": f"cal_{i}", "label": f"Ch{i} Multiplier", "type": "number", "value": cal, "step": "0.001"})
        form.append({"name": "cal_header", "label": "Two Point Calibration", "type": "separator"})
        form.append({"name": "cal_enable", "label": "Calibration Enabled", "type": "checkbox", "value": int(self._config.get("cal_enable", 0))})
        form.append({"name": "cal_adc1", "label": "Point 1 ADC", "type": "number", "value": self._config.get("cal_adc1", 0)})
        form.append({"name": "cal_out1", "label": "Point 1 Output", "type": "number", "value": self._config.get("cal_out1", 0.0), "step": "0.001"})
        form.append({"name": "cal_adc2", "label": "Point 2 ADC", "type": "number", "value": self._config.get("cal_adc2", 32767)})
        form.append({"name": "cal_out2", "label": "Point 2 Output", "type": "number", "value": self._config.get("cal_out2", 32767.0), "step": "0.001"})
        event.data["form"] = form
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        old_type = self._config.get("adc_type", 11)
        self._config.update(event.data.get("form_data", {}))
        new_type = self._config.get("adc_type", 11)
        if old_type != new_type:
            self._oversample_buf = [[], [], [], []]
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"AIN0": 0.0, "AIN1": 0.0, "AIN2": 0.0, "AIN3": 0.0}

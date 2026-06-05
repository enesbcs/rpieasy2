from __future__ import annotations

import logging
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_TRIPLE, SENSOR_V_TYPE_VOLTAGE, SENSOR_V_TYPE_CURRENT, SENSOR_V_TYPE_WATT
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p027")

INA219_ADDR = 0x40
INA219_REG_CONFIG = 0x00
INA219_REG_SHUNT = 0x01
INA219_REG_BUS = 0x02
INA219_REG_CAL = 0x05


class P027INA219(PluginBase):
    PLUGIN_ID = 27
    PLUGIN_NAME = "Energy (DC) - INA219"
    PLUGIN_VALUES = 3
    DEVICE_PROPERTIES = DeviceProperties(type=DEVICE_TYPE_I2C, vtype=SENSOR_TYPE_TRIPLE, value_count=3, formula_option=True, send_data_option=True, plugin_stats=True)

    I2C_ADDRESSES = [0x40 + i for i in range(16)]

    def __init__(self):
        super().__init__()
        self._addr: int = INA219_ADDR
        self._config: dict[str, Any] = {}
        self._current_lsb: float = 0.0

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._addr = int(self._config.get("address") or INA219_ADDR)
        if not self._hw: return False
        try:
            i2c = self._hw.i2c
            mr = int(self._config.get("measure_range") or 0)
            if mr == 2:
                conf = 0x047F
                cal_val = 8192
                self._current_lsb = 0.00005
            elif mr == 1:
                conf = 0x3C7F
                cal_val = 10240
                self._current_lsb = 0.00004
            elif mr == 3:
                conf = 0x3C7F
                cal_val = 4096
                self._current_lsb = 0.0005
            else:
                conf = 0x3C7F
                cal_val = 4027
                self._current_lsb = 0.0001
            await i2c.write_i2c_block_data(self._addr, INA219_REG_CONFIG, [(conf >> 8) & 0xFF, conf & 0xFF])
            await i2c.write_i2c_block_data(self._addr, INA219_REG_CAL, [(cal_val >> 8) & 0xFF, cal_val & 0xFF])
        except Exception as e:
            logger.error(f"INA219 init failed: {e}")
            return False
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw: return False
        try:
            i2c = self._hw.i2c
            bus_raw = await i2c.read_i2c_block_data(self._addr, INA219_REG_BUS, 2)
            bus_v = ((bus_raw[0] << 8) | bus_raw[1]) >> 3
            bus_voltage = bus_v * 0.004
            shunt_raw = await i2c.read_i2c_block_data(self._addr, INA219_REG_SHUNT, 2)
            shunt = (shunt_raw[0] << 8) | shunt_raw[1]
            if shunt > 0x7FFF: shunt -= 0x10000
            current = shunt * self._current_lsb
            power = bus_voltage * current
            mt = int(self._config.get("measure_type") or 3)
            if mt == 0:
                event.data["values"] = {"Voltage": round(bus_voltage, 3)}
            elif mt == 1:
                event.data["values"] = {"Current": round(current, 3)}
            elif mt == 2:
                event.data["values"] = {"Power": round(power, 3)}
            else:
                event.data["values"] = {"Voltage": round(bus_voltage, 3),
                    "Current": round(current, 3), "Power": round(power, 3)}
            if self._config.get("powerdown", False):
                await i2c.write_i2c_block_data(self._addr, INA219_REG_CONFIG, [0x00, 0x00])
            return True
        except Exception as e:
            logger.error(f"INA219 read failed: {e}")
            return False

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "address", "label": "I2C Address", "type": "select", "value": self._config.get("address", INA219_ADDR), "options": [
                {"value": a, "label": hex(a)} for a in self.I2C_ADDRESSES
            ]},
            {"name": "measure_range", "label": "Measure Range", "type": "select", "value": self._config.get("measure_range", 0), "options": [
                {"value": 0, "label": "32V 2A"},
                {"value": 1, "label": "32V 1A"},
                {"value": 2, "label": "16V 0.4A"},
                {"value": 3, "label": "26V 8A"},
            ]},
            {"name": "measure_type", "label": "Measure Type", "type": "select", "value": self._config.get("measure_type", 3), "options": [
                {"value": 0, "label": "Voltage"},
                {"value": 1, "label": "Current"},
                {"value": 2, "label": "Power"},
                {"value": 3, "label": "All"},
            ]},
            {"name": "powerdown", "label": "Use Powerdown Mode", "type": "checkbox", "value": self._config.get("powerdown", False)},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_get_devicevaluecount(self, event: Event) -> bool | None:
        mt = int(self._config.get("measure_type") or 3)
        event.data["value_count"] = 1 if mt < 3 else 3
        return True

    
    async def on_plugin_get_discovery_vtypes(self, event: Event) -> bool | None:
        mt = int(self._config.get("measure_type") or 3)
        if mt == 0:
            event.data["vtypes"] = [SENSOR_V_TYPE_VOLTAGE]
        elif mt == 1:
            event.data["vtypes"] = [SENSOR_V_TYPE_CURRENT]
        elif mt == 2:
            event.data["vtypes"] = [SENSOR_V_TYPE_WATT]
        else:
            event.data["vtypes"] = [SENSOR_V_TYPE_VOLTAGE, SENSOR_V_TYPE_CURRENT, SENSOR_V_TYPE_WATT]
        return True

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        mt = int(self._config.get("measure_type") or 3)
        if mt < 3:
            from rpieasy2.core.rpiconst import SENSOR_TYPE_SINGLE
            event.data["vtype"] = SENSOR_TYPE_SINGLE
        else:
            from rpieasy2.core.rpiconst import SENSOR_TYPE_TRIPLE
            event.data["vtype"] = SENSOR_TYPE_TRIPLE
        return True

    async def on_plugin_get_device_value_names(self, event: Event) -> bool | None:
        mt = int(self._config.get("measure_type") or 3)
        if mt == 0:
            event.data["value_names"] = ["Voltage"]
        elif mt == 1:
            event.data["value_names"] = ["Current"]
        elif mt == 2:
            event.data["value_names"] = ["Power"]
        else:
            event.data["value_names"] = ["Voltage", "Current", "Power"]
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Voltage": 0.0, "Current": 0.0, "Power": 0.0}

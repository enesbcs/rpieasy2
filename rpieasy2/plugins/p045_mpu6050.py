from __future__ import annotations

import logging
import time as time_mod
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_I2C, SENSOR_TYPE_SINGLE, SENSOR_TYPE_SWITCH, SENSOR_TYPE_TRIPLE
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p045")

MPU6050_ADDR = 0x68
MPU6050_REG_PWR = 0x6B
MPU6050_REG_ACCEL = 0x3B
MPU6050_REG_GYRO = 0x43

FUNC_MOVEMENT = 0
FUNC_RANGE_X = 1
FUNC_RANGE_Y = 2
FUNC_RANGE_Z = 3
FUNC_ACCEL_X = 4
FUNC_ACCEL_Y = 5
FUNC_ACCEL_Z = 6
FUNC_GFORCE_X = 7
FUNC_GFORCE_Y = 8
FUNC_GFORCE_Z = 9
FUNC_GYRO_X = 10
FUNC_GYRO_Y = 11
FUNC_GYRO_Z = 12

RANGE_SCALE = 16384.0
GYRO_SCALE = 131.0


class P045MPU6050(PluginBase):
    PLUGIN_ID = 45
    PLUGIN_NAME = "Gyro - MPU 6050"
    PLUGIN_VALUES = 6
    DEVICE_PROPERTIES = DeviceProperties(type=DEVICE_TYPE_I2C, vtype=SENSOR_TYPE_SINGLE, value_count=1, send_data_option=True, timer_option=True, formula_option=False, plugin_stats=True, custom_vtype_var=True)

    def __init__(self):
        super().__init__()
        self._addr: int = MPU6050_ADDR
        self._config: dict[str, Any] = {}
        self._min_vals: list[float] = [0.0, 0.0, 0.0]
        self._max_vals: list[float] = [0.0, 0.0, 0.0]
        self._movement_state: int = 0
        self._last_check: float = 0.0
        self._detect_count: int = 0

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._addr = self._config.get("address", MPU6050_ADDR)
        if not self._hw:
            return False
        try:
            await self._hw.i2c.write_byte_data(self._addr, MPU6050_REG_PWR, 0x00)
        except Exception as e:
            logger.error("MPU6050 init failed: %s", e)
            return False
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        return True

    async def _read_word(self, reg: int) -> int:
        if not self._hw:
            return 0
        d = await self._hw.i2c.read_i2c_block_data(self._addr, reg, 2)
        v = (d[0] << 8) | d[1]
        return v - 65536 if v > 32767 else v

    async def _read_accels(self) -> tuple[float, float, float]:
        ax = await self._read_word(MPU6050_REG_ACCEL) / RANGE_SCALE
        ay = await self._read_word(MPU6050_REG_ACCEL + 2) / RANGE_SCALE
        az = await self._read_word(MPU6050_REG_ACCEL + 4) / RANGE_SCALE
        return ax, ay, az

    async def _read_gyros(self) -> tuple[float, float, float]:
        gx = await self._read_word(MPU6050_REG_GYRO) / GYRO_SCALE
        gy = await self._read_word(MPU6050_REG_GYRO + 2) / GYRO_SCALE
        gz = await self._read_word(MPU6050_REG_GYRO + 4) / GYRO_SCALE
        return gx, gy, gz

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._hw:
            return False
        try:
            fn = int(self._config.get("function") or 0)
            ax, ay, az = await self._read_accels()
            gx, gy, gz = await self._read_gyros()
            all3 = bool(self._config.get("all_values", False))
            if fn == FUNC_MOVEMENT:
                th_x = int(self._config.get("threshold_x") or 0)
                th_y = int(self._config.get("threshold_y") or 0)
                th_z = int(self._config.get("threshold_z") or 0)
                window = int(self._config.get("detection_window") or 0)
                min_cnt = int(self._config.get("min_detection_count") or 0)
                now = time_mod.time()
                triggered = False
                if abs(ax) >= th_x or abs(ay) >= th_y or abs(az) >= th_z:
                    self._detect_count += 1
                    self._last_check = now
                    if self._detect_count >= max(1, min_cnt):
                        triggered = True
                elif window > 0 and (now - self._last_check) > window:
                    self._detect_count = 0
                if triggered:
                    self._movement_state = 1
                elif window > 0 and (now - self._last_check) > window:
                    self._movement_state = 0
                if all3:
                    event.data["values"] = {"State": self._movement_state, "RangeX": round(abs(ax), 4), "RangeY": round(abs(ay), 4)}
                    event.data["vtype"] = SENSOR_TYPE_TRIPLE
                else:
                    event.data["values"] = {"State": self._movement_state}
                    event.data["vtype"] = SENSOR_TYPE_SWITCH
                return True
            if fn in (FUNC_RANGE_X, FUNC_RANGE_Y, FUNC_RANGE_Z):
                idx = fn - 1
                vals = [ax, ay, az]
                v = vals[idx]
                self._min_vals[idx] = min(self._min_vals[idx], v)
                self._max_vals[idx] = max(self._max_vals[idx], v)
                rng = self._max_vals[idx] - self._min_vals[idx]
                if all3:
                    event.data["values"] = {
                        "RangeX": round(self._max_vals[0] - self._min_vals[0], 4),
                        "RangeY": round(self._max_vals[1] - self._min_vals[1], 4),
                        "RangeZ": round(self._max_vals[2] - self._min_vals[2], 4),
                    }
                else:
                    event.data["values"] = {f"Range{'XYZ'[idx]}": round(rng, 4)}
                return True
            if fn in (FUNC_ACCEL_X, FUNC_ACCEL_Y, FUNC_ACCEL_Z):
                idx = fn - 4
                vals = [ax, ay, az]
                if all3:
                    event.data["values"] = {"AccelX": round(ax, 4), "AccelY": round(ay, 4), "AccelZ": round(az, 4)}
                else:
                    event.data["values"] = {f"Accel{'XYZ'[idx]}": round(vals[idx], 4)}
                return True
            if fn in (FUNC_GFORCE_X, FUNC_GFORCE_Y, FUNC_GFORCE_Z):
                idx = fn - 7
                vals = [ax / 9.81, ay / 9.81, az / 9.81]
                if all3:
                    event.data["values"] = {"GForceX": round(vals[0], 4), "GForceY": round(vals[1], 4), "GForceZ": round(vals[2], 4)}
                else:
                    event.data["values"] = {f"GForce{'XYZ'[idx]}": round(vals[idx], 4)}
                return True
            if fn in (FUNC_GYRO_X, FUNC_GYRO_Y, FUNC_GYRO_Z):
                idx = fn - 10
                vals = [gx, gy, gz]
                if all3:
                    event.data["values"] = {"GyroX": round(vals[0], 4), "GyroY": round(vals[1], 4), "GyroZ": round(vals[2], 4)}
                else:
                    event.data["values"] = {f"Gyro{'XYZ'[idx]}": round(vals[idx], 4)}
                return True
            event.data["values"] = {"AccelX": round(ax, 4)}
            return True
        except Exception as e:
            logger.error("MPU6050 read failed: %s", e)
            return False

    async def on_plugin_get_device_value_names(self, event: Event) -> bool | None:
        fn = int(self._config.get("function") or 0)
        all3 = bool(self._config.get("all_values", False))
        if fn == FUNC_MOVEMENT:
            if all3:
                event.data["value_names"] = ["State", "RangeX", "RangeY"]
            else:
                event.data["value_names"] = ["State"]
        elif fn in (FUNC_RANGE_X, FUNC_RANGE_Y, FUNC_RANGE_Z):
            if all3:
                event.data["value_names"] = ["RangeX", "RangeY", "RangeZ"]
            else:
                event.data["value_names"] = [f"Range{'XYZ'[fn - 1]}"]
        elif fn in (FUNC_ACCEL_X, FUNC_ACCEL_Y, FUNC_ACCEL_Z):
            if all3:
                event.data["value_names"] = ["AccelX", "AccelY", "AccelZ"]
            else:
                event.data["value_names"] = [f"Accel{'XYZ'[fn - 4]}"]
        elif fn in (FUNC_GFORCE_X, FUNC_GFORCE_Y, FUNC_GFORCE_Z):
            if all3:
                event.data["value_names"] = ["GForceX", "GForceY", "GForceZ"]
            else:
                event.data["value_names"] = [f"GForce{'XYZ'[fn - 7]}"]
        elif fn in (FUNC_GYRO_X, FUNC_GYRO_Y, FUNC_GYRO_Z):
            if all3:
                event.data["value_names"] = ["GyroX", "GyroY", "GyroZ"]
            else:
                event.data["value_names"] = [f"Gyro{'XYZ'[fn - 10]}"]
        else:
            event.data["value_names"] = ["Value"]
        return True

    async def on_plugin_get_devicevaluecount(self, event: Event) -> bool | None:
        fn = int(self._config.get("function") or 0)
        all3 = bool(self._config.get("all_values", False))
        if fn in (FUNC_MOVEMENT, FUNC_RANGE_X, FUNC_RANGE_Y, FUNC_RANGE_Z,
                  FUNC_ACCEL_X, FUNC_ACCEL_Y, FUNC_ACCEL_Z,
                  FUNC_GFORCE_X, FUNC_GFORCE_Y, FUNC_GFORCE_Z,
                  FUNC_GYRO_X, FUNC_GYRO_Y, FUNC_GYRO_Z):
            vc = 3 if all3 else 1
        else:
            vc = 1
        event.data["value_count"] = vc
        return True

    async def on_plugin_get_discovery_vtypes(self, event: Event) -> bool | None:
        fn = int(self._config.get("function") or 0)
        all3 = bool(self._config.get("all_values", False))
        if fn == 0 and all3:
            event.data["vtypes"] = [SENSOR_V_TYPE_SWITCH, SENSOR_V_TYPE_SINGLE, SENSOR_V_TYPE_SINGLE]
        elif fn == 0:
            event.data["vtypes"] = [SENSOR_V_TYPE_SWITCH]
        elif all3:
            event.data["vtypes"] = [SENSOR_V_TYPE_SINGLE, SENSOR_V_TYPE_SINGLE, SENSOR_V_TYPE_SINGLE]
        else:
            event.data["vtypes"] = [SENSOR_V_TYPE_SINGLE]
        return True

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        fn = int(self._config.get("function") or 0)
        all3 = bool(self._config.get("all_values", False))
        if fn == FUNC_MOVEMENT:
            event.data["vtype"] = SENSOR_TYPE_TRIPLE if all3 else SENSOR_TYPE_SWITCH
        elif all3:
            event.data["vtype"] = SENSOR_TYPE_TRIPLE
        else:
            event.data["vtype"] = SENSOR_TYPE_SINGLE
        return True

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        event.data["form"] = [
            {"name": "address", "label": "I2C Address", "type": "select", "value": self._config.get("address", MPU6050_ADDR), "options": [
                {"value": 0x68, "label": "0x68"}, {"value": 0x69, "label": "0x69"},
            ]},
            {"name": "function", "label": "Function", "type": "select", "value": self._config.get("function", 0), "options": [
                {"value": 0, "label": "Movement Detection"},
                {"value": 1, "label": "Range X"},
                {"value": 2, "label": "Range Y"},
                {"value": 3, "label": "Range Z"},
                {"value": 4, "label": "Acceleration X"},
                {"value": 5, "label": "Acceleration Y"},
                {"value": 6, "label": "Acceleration Z"},
                {"value": 7, "label": "G-Force X"},
                {"value": 8, "label": "G-Force Y"},
                {"value": 9, "label": "G-Force Z"},
                {"value": 10, "label": "Gyro X"},
                {"value": 11, "label": "Gyro Y"},
                {"value": 12, "label": "Gyro Z"},
            ]},
            {"name": "threshold_x", "label": "Threshold X", "type": "number", "value": self._config.get("threshold_x", 0)},
            {"name": "threshold_y", "label": "Threshold Y", "type": "number", "value": self._config.get("threshold_y", 0)},
            {"name": "threshold_z", "label": "Threshold Z", "type": "number", "value": self._config.get("threshold_z", 0)},
            {"name": "min_detection_count", "label": "Min Detection Count", "type": "number", "value": self._config.get("min_detection_count", 0)},
            {"name": "detection_window", "label": "Detection Window (s)", "type": "number", "value": self._config.get("detection_window", 0)},
            {"name": "all_axes", "label": "Detection on All 3 Axes", "type": "checkbox", "value": self._config.get("all_axes", False)},
            {"name": "all_values", "label": "Provide All 3 Values", "type": "checkbox", "value": self._config.get("all_values", False)},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        fn = int(self._config.get("function") or 0)
        if fn not in (FUNC_MOVEMENT, FUNC_GYRO_X, FUNC_GYRO_Y, FUNC_GYRO_Z):
            self._min_vals = [0.0, 0.0, 0.0]
            self._max_vals = [0.0, 0.0, 0.0]

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"State": 0, "RangeX": 0.0, "RangeY": 0.0, "RangeZ": 0.0,
                "AccelX": 0.0, "AccelY": 0.0, "AccelZ": 0.0,
                "GForceX": 0.0, "GForceY": 0.0, "GForceZ": 0.0,
                "GyroX": 0.0, "GyroY": 0.0, "GyroZ": 0.0}

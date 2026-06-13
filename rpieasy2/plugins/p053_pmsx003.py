from __future__ import annotations

import asyncio
import logging
import struct
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from rpieasy2.core.device_properties import DeviceProperties
from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import (
    DEVICE_TYPE_SERIAL,
    SENSOR_TYPE_QUAD,
    SENSOR_TYPE_TRIPLE,
    SENSOR_V_TYPE_PM1_0,
    SENSOR_V_TYPE_PM2_5,
    SENSOR_V_TYPE_PM10,
)

logger = logging.getLogger("rpieasy2.plugin.p053")

PMS_START_BYTE1 = 0x42
PMS_START_BYTE2 = 0x4D
PMS_DATA_WORDS = 15


class PMSx003Type(IntEnum):
    PMS1003_5003_7003 = 0
    PMS2003_3003 = 1
    PMS5003_S = 2
    PMS5003_T = 3
    PMS5003_ST = 4


class OutputSelector(IntEnum):
    PARTICLES_UG_M3 = 0
    PM2_5_TEMP_HUM_HCHO = 1
    COUNTS_0_3_2_5 = 2
    COUNTS_1_0_10 = 3


class EventSelector(IntEnum):
    NONE = 0
    PM_TEMP_HUM_HCHO = 1
    ALL_COUNTS = 2
    ALL = 3


OUTPUT_VALUE_NAMES: dict[OutputSelector, list[str]] = {
    OutputSelector.PARTICLES_UG_M3: ["pm1.0", "pm2.5", "pm10"],
    OutputSelector.PM2_5_TEMP_HUM_HCHO: ["pm2.5", "temp", "hum", "hcho"],
    OutputSelector.COUNTS_0_3_2_5: ["cnt0.3", "cnt0.5", "cnt1.0", "cnt2.5"],
    OutputSelector.COUNTS_1_0_10: ["cnt1.0", "cnt2.5", "cnt5.0", "cnt10.0"],
}

OUTPUT_VALUE_COUNT: dict[OutputSelector, int] = {
    OutputSelector.PARTICLES_UG_M3: 3,
    OutputSelector.PM2_5_TEMP_HUM_HCHO: 4,
    OutputSelector.COUNTS_0_3_2_5: 4,
    OutputSelector.COUNTS_1_0_10: 4,
}

OUTPUT_SENSOR_TYPE: dict[OutputSelector, int] = {
    OutputSelector.PARTICLES_UG_M3: SENSOR_TYPE_TRIPLE,
    OutputSelector.PM2_5_TEMP_HUM_HCHO: SENSOR_TYPE_QUAD,
    OutputSelector.COUNTS_0_3_2_5: SENSOR_TYPE_QUAD,
    OutputSelector.COUNTS_1_0_10: SENSOR_TYPE_QUAD,
}

OUTPUT_MODEL_RESTRICTION: dict[OutputSelector, list[PMSx003Type]] = {
    OutputSelector.PARTICLES_UG_M3: list(PMSx003Type),
    OutputSelector.PM2_5_TEMP_HUM_HCHO: [
        PMSx003Type.PMS5003_S, PMSx003Type.PMS5003_T, PMSx003Type.PMS5003_ST,
    ],
    OutputSelector.COUNTS_0_3_2_5: [
        PMSx003Type.PMS5003_S, PMSx003Type.PMS5003_T, PMSx003Type.PMS5003_ST,
    ],
    OutputSelector.COUNTS_1_0_10: [
        PMSx003Type.PMS5003_S, PMSx003Type.PMS5003_T, PMSx003Type.PMS5003_ST,
    ],
}

EVENT_NON_OUTPUT_NAMES: dict[OutputSelector, list[str]] = {
    OutputSelector.PARTICLES_UG_M3: [
        "temp", "hum", "hcho",
        "cnt0.3", "cnt0.5", "cnt1.0", "cnt2.5", "cnt5.0", "cnt10.0",
    ],
    OutputSelector.PM2_5_TEMP_HUM_HCHO: [
        "pm1.0", "pm10",
        "cnt0.3", "cnt0.5", "cnt1.0", "cnt2.5", "cnt5.0", "cnt10.0",
    ],
    OutputSelector.COUNTS_0_3_2_5: [
        "pm1.0", "pm2.5", "pm10", "temp", "hum", "hcho",
        "cnt5.0", "cnt10.0",
    ],
    OutputSelector.COUNTS_1_0_10: [
        "pm1.0", "pm2.5", "pm10", "temp", "hum", "hcho",
        "cnt0.3", "cnt0.5",
    ],
}

EVENT_SELECTOR_NAMES: dict[EventSelector, list[str] | None] = {
    EventSelector.NONE: None,
    EventSelector.PM_TEMP_HUM_HCHO: ["temp", "hum", "hcho", "pm1.0", "pm2.5", "pm10"],
    EventSelector.ALL_COUNTS: [
        "cnt0.3", "cnt0.5", "cnt1.0", "cnt2.5", "cnt5.0", "cnt10.0",
    ],
    EventSelector.ALL: None,
}


@dataclass
class PMSData:
    words: list[int] = field(default_factory=lambda: [0] * PMS_DATA_WORDS)
    count: int = 0

    def add(self, words: list[int]) -> None:
        if self.count == 0:
            self.words = list(words)
        else:
            for i in range(min(len(self.words), len(words))):
                self.words[i] += words[i]
        self.count += 1

    def avg(self) -> list[int]:
        if self.count <= 1:
            return self.words
        c = self.count
        return [w // c if c > 1 else w for w in self.words]


class P053PMSx003(PluginBase):
    PLUGIN_ID = 53
    PLUGIN_NAME = "Dust - PMSx003"
    PLUGIN_VALUES = 4
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_SERIAL,
        vtype=SENSOR_TYPE_TRIPLE,
        value_count=4,
        send_data_option=True,
        timer_option=True,
        formula_option=True,
        plugin_stats=True,
    )

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._serial = None
        self._buf = bytearray()
        self._raw: dict[str, float] = {}
        self._rst_pin: int = -1
        self._pwr_pin: int = -1
        self._values_received = False
        self._accum: PMSData = PMSData()

    async def _model(self) -> PMSx003Type:
        v = self._config.get("model")
        if isinstance(v, str):
            try:
                return PMSx003Type(int(v))
            except (ValueError, TypeError):
                pass
            try:
                return PMSx003Type[v]
            except (KeyError, TypeError):
                pass
        if isinstance(v, int):
            try:
                return PMSx003Type(v)
            except (ValueError, TypeError):
                pass
        return PMSx003Type.PMS1003_5003_7003

    def _output(self) -> OutputSelector:
        try:
            return OutputSelector(int(self._config.get("output_selector", 0)))
        except (ValueError, TypeError):
            return OutputSelector.PARTICLES_UG_M3

    def _event_sel(self) -> EventSelector:
        try:
            return EventSelector(int(self._config.get("event_selector", 0)))
        except (ValueError, TypeError):
            return EventSelector.NONE

    def _oversample(self) -> bool:
        v = self._config.get("oversample")
        return bool(v) if v is not None else True

    def _split_bins(self) -> bool:
        return bool(self._config.get("split_bins", False))

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        if not self._hw:
            return False
        await self._setup_serial()
        return True

    async def on_plugin_exit(self, event: Event) -> bool | None:
        await self._close_serial()
        return True

    async def _setup_serial(self) -> None:
        self._rst_pin = int(self._config.get("rst_pin") or -1)
        self._pwr_pin = int(self._config.get("pwr_pin") or -1)
        if self._pwr_pin > 0:
            try:
                self._hw.gpio.claim_output(self._pwr_pin)
                self._hw.gpio.write(self._pwr_pin, 1)
            except Exception:
                self._pwr_pin = -1
        if self._rst_pin > 0:
            try:
                self._hw.gpio.claim_output(self._rst_pin)
                self._hw.gpio.write(self._rst_pin, 1)
            except Exception:
                self._rst_pin = -1
        await asyncio.sleep(0.01)
        try:
            self._serial = self._hw.serial.open(
                port=self._config.get("serial_port", "/dev/ttyAMA0"),
                baud=int(self._config.get("baudrate") or 9600),
                timeout=0,
            )
        except Exception as e:
            logger.error("PMSx003 serial open failed: %s", e)
            self._serial = None

    async def _close_serial(self) -> None:
        if self._serial:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None

    async def on_plugin_ten_per_second(self, event: Event) -> bool | None:
        if not self._serial:
            return None
        try:
            data = self._serial.read(256)
            if data:
                self._buf.extend(data)
                self._parse_packets()
        except Exception as e:
            logger.error("PMSx003 read error: %s", e)
        return None

    def _extract_words(self, frame: bytes, data_len: int) -> list[int] | None:
        if len(frame) < data_len + 4:
            return None
        num_words = data_len // 2
        fmt = ">" + "H" * num_words
        try:
            vals = list(struct.unpack(fmt, frame[4:4 + data_len]))
        except struct.error:
            return None
        cksum_data = sum(frame[:4 + data_len]) & 0xFFFF
        if 4 + data_len + 2 > len(frame):
            return None
        frame_cksum = (frame[4 + data_len] << 8) | frame[4 + data_len + 1]
        if cksum_data != frame_cksum:
            return None
        return vals

    def _parse_packets(self) -> None:
        while len(self._buf) >= 4:
            idx = self._buf.find(bytes([PMS_START_BYTE1, PMS_START_BYTE2]))
            if idx < 0:
                self._buf = bytearray()
                break
            if idx > 0:
                self._buf = self._buf[idx:]
            if len(self._buf) < 4:
                break
            data_len = (self._buf[2] << 8) | self._buf[3]
            if data_len < 4 or data_len > 60:
                self._buf.pop(0)
                continue
            pkt_len = 4 + data_len + 2
            if len(self._buf) < pkt_len:
                break
            frame = bytes(self._buf[:pkt_len])
            self._buf = self._buf[pkt_len:]
            words = self._extract_words(frame, data_len)
            if words is None:
                continue
            self._process_words(words)

    def _process_words(self, words: list[int]) -> None:
        if len(words) < 12:
            return
        r: dict[str, float] = {}
        r["pm1_0_cf"] = round(words[0] / 10.0, 1)
        r["pm2_5_cf"] = round(words[1] / 10.0, 1)
        r["pm10_cf"] = round(words[2] / 10.0, 1)
        r["pm1_0"] = round(words[3] / 10.0, 1)
        r["pm2_5"] = round(words[4] / 10.0, 1)
        r["pm10"] = round(words[5] / 10.0, 1)
        r["cnt0_3"] = float(words[6])
        r["cnt0_5"] = float(words[7])
        r["cnt1_0"] = float(words[8])
        r["cnt2_5"] = float(words[9])
        r["cnt5_0"] = float(words[10])
        r["cnt10_0"] = float(words[11])
        if len(words) >= 13:
            r["temp"] = round(words[12] / 10.0, 1)
        if len(words) >= 14:
            r["hum"] = round(words[13] / 10.0, 1)
        if len(words) >= 15:
            r["hcho"] = round(words[14] / 100.0, 3)
        self._raw = r
        if self._oversample():
            self._accum.add(words[:PMS_DATA_WORDS])
        self._values_received = True

    def _get_value(self, key: str) -> float:
        return self._raw.get(key, 0.0)

    def _apply_split_bins(self, raw: dict[str, float]) -> dict[str, float]:
        d = dict(raw)
        if self._split_bins():
            d["cnt0_3"] = max(0.0, raw.get("cnt0_3", 0) - raw.get("cnt0_5", 0))
            d["cnt0_5"] = max(0.0, raw.get("cnt0_5", 0) - raw.get("cnt1_0", 0))
            d["cnt1_0"] = max(0.0, raw.get("cnt1_0", 0) - raw.get("cnt2_5", 0))
            d["cnt2_5"] = max(0.0, raw.get("cnt2_5", 0) - raw.get("cnt5_0", 0))
            d["cnt5_0"] = max(0.0, raw.get("cnt5_0", 0) - raw.get("cnt10_0", 0))
        return d

    async def _build_output_values(self) -> dict[str, float]:
        if self._oversample() and self._accum.count > 1:
            avg_words = self._accum.avg()
            r: dict[str, float] = {}
            if len(avg_words) >= 6:
                r["pm1_0"] = round(avg_words[3] / 10.0, 1)
                r["pm2_5"] = round(avg_words[4] / 10.0, 1)
                r["pm10"] = round(avg_words[5] / 10.0, 1)
            if len(avg_words) >= 12:
                r["cnt0_3"] = float(avg_words[6])
                r["cnt0_5"] = float(avg_words[7])
                r["cnt1_0"] = float(avg_words[8])
                r["cnt2_5"] = float(avg_words[9])
                r["cnt5_0"] = float(avg_words[10])
                r["cnt10_0"] = float(avg_words[11])
            if len(avg_words) >= 13:
                r["temp"] = round(avg_words[12] / 10.0, 1)
            if len(avg_words) >= 14:
                r["hum"] = round(avg_words[13] / 10.0, 1)
            if len(avg_words) >= 15:
                r["hcho"] = round(avg_words[14] / 100.0, 3)
        else:
            r = dict(self._raw)
        r = self._apply_split_bins(r)
        output = self._output()
        names = OUTPUT_VALUE_NAMES.get(output, OUTPUT_VALUE_NAMES[OutputSelector.PARTICLES_UG_M3])
        key_map: dict[str, str] = {
            "pm1.0": "pm1_0", "pm2.5": "pm2_5", "pm10": "pm10",
            "temp": "temp", "hum": "hum", "hcho": "hcho",
            "cnt0.3": "cnt0_3", "cnt0.5": "cnt0_5",
            "cnt1.0": "cnt1_0", "cnt2.5": "cnt2_5",
            "cnt5.0": "cnt5_0", "cnt10.0": "cnt10_0",
        }
        result: dict[str, float] = {}
        for name in names:
            raw_key = key_map.get(name)
            if raw_key:
                result[name] = r.get(raw_key, 0.0)
        return result

    async def _get_non_output_values(self) -> dict[str, float] | None:
        esel = self._event_sel()
        if esel == EventSelector.NONE:
            return None
        output = self._output()
        if esel == EventSelector.ALL:
            non_output = EVENT_NON_OUTPUT_NAMES.get(output, [])
        elif esel == EventSelector.PM_TEMP_HUM_HCHO:
            evt = set(EVENT_SELECTOR_NAMES[EventSelector.PM_TEMP_HUM_HCHO] or [])
            non_output = [n for n in evt if n not in set(OUTPUT_VALUE_NAMES.get(output, []))]
        elif esel == EventSelector.ALL_COUNTS:
            evt = set(EVENT_SELECTOR_NAMES[EventSelector.ALL_COUNTS] or [])
            non_output = [n for n in evt if n not in set(OUTPUT_VALUE_NAMES.get(output, []))]
        else:
            return None
        if not non_output:
            return None
        key_map = {
            "pm1.0": "pm1_0", "pm2.5": "pm2_5", "pm10": "pm10",
            "temp": "temp", "hum": "hum", "hcho": "hcho",
            "cnt0.3": "cnt0_3", "cnt0.5": "cnt0_5",
            "cnt1.0": "cnt1_0", "cnt2.5": "cnt2_5",
            "cnt5.0": "cnt5_0", "cnt10.0": "cnt10_0",
        }
        extra: dict[str, float] = {}
        for name in non_output:
            rk = key_map.get(name)
            if rk and rk in self._raw:
                extra[name] = self._raw[rk]
        return extra if extra else None

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._values_received:
            return False
        self._values_received = False
        if self._oversample() and self._accum.count > 0:
            self._accum = PMSData()
        try:
            values = await self._build_output_values()
            event.data["values"] = values
            extra = await self._get_non_output_values()
            if extra:
                event.data["_non_output"] = extra
            return True
        except Exception as e:
            logger.error("PMSx003 read failed: %s", e)
            return False

    async def on_plugin_write(self, event: Event) -> bool | None:
        command = (event.string1 or "").strip().lower()
        parts = command.split(",", 1)
        cmd = parts[0]
        if cmd == "pmsx003" and len(parts) > 1:
            sub = parts[1].strip()
            if sub == "wake":
                await self._wake()
                return True
            if sub == "sleep":
                await self._sleep()
                return True
            if sub == "reset":
                await self._reset()
                return True
        return False

    async def _wake(self) -> None:
        if self._pwr_pin > 0 and self._hw:
            try:
                self._hw.gpio.write(self._pwr_pin, 1)
            except Exception:
                pass
        delay = int(self._config.get("wake_delay") or 0)
        if delay > 0:
            await asyncio.sleep(delay)

    async def _sleep(self) -> None:
        if self._pwr_pin > 0 and self._hw:
            try:
                self._hw.gpio.write(self._pwr_pin, 0)
            except Exception:
                pass

    async def _reset(self) -> None:
        if self._rst_pin > 0 and self._hw:
            try:
                self._hw.gpio.write(self._rst_pin, 0)
                await asyncio.sleep(0.5)
                self._hw.gpio.write(self._rst_pin, 1)
            except Exception:
                pass

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("serial_port", "/dev/ttyAMA0")
        self._config.setdefault("baudrate", 9600)
        self._config.setdefault("model", int(PMSx003Type.PMS1003_5003_7003))
        self._config.setdefault("output_selector", int(OutputSelector.PARTICLES_UG_M3))
        self._config.setdefault("event_selector", int(EventSelector.NONE))
        self._config.setdefault("wake_delay", 0)
        self._config.setdefault("oversample", True)
        self._config.setdefault("split_bins", False)
        return True

    def _valid_outputs_for_model(self, model: PMSx003Type) -> list[OutputSelector]:
        return [o for o in OutputSelector if model in OUTPUT_MODEL_RESTRICTION[o]]

    def _clamp_output(self, model: PMSx003Type, output: OutputSelector) -> OutputSelector:
        valid = self._valid_outputs_for_model(model)
        if output in valid:
            return output
        return valid[0]

    def _clamp_event(self, model: PMSx003Type, output: OutputSelector, event_sel: EventSelector) -> EventSelector:
        if event_sel == EventSelector.NONE:
            return EventSelector.NONE
        if event_sel == EventSelector.PM_TEMP_HUM_HCHO:
            if model in (PMSx003Type.PMS5003_S, PMSx003Type.PMS5003_T, PMSx003Type.PMS5003_ST):
                return event_sel
            return EventSelector.NONE
        return event_sel

    MODEL_LABELS: dict[PMSx003Type, str] = {
        PMSx003Type.PMS1003_5003_7003: "PMS1003 / PMS5003 / PMS7003",
        PMSx003Type.PMS2003_3003: "PMS2003 / PMS3003",
        PMSx003Type.PMS5003_S: "PMS5003S (with temp, hum, HCHO)",
        PMSx003Type.PMS5003_T: "PMS5003T (with temp, hum, HCHO)",
        PMSx003Type.PMS5003_ST: "PMS5003ST (with temp, hum, HCHO)",
    }

    OUTPUT_LABELS: dict[OutputSelector, str] = {
        OutputSelector.PARTICLES_UG_M3: "Particles (PM1.0, PM2.5, PM10) \u00b5g/m\u00b3",
        OutputSelector.PM2_5_TEMP_HUM_HCHO: "PM2.5, Temperature, Humidity, Formaldehyde (HCHO)",
        OutputSelector.COUNTS_0_3_2_5: "Particle counts/0.1L (0.3, 0.5, 1.0, 2.5 \u00b5m)",
        OutputSelector.COUNTS_1_0_10: "Particle counts/0.1L (1.0, 2.5, 5.0, 10.0 \u00b5m)",
    }

    EVENT_LABELS: dict[EventSelector, str] = {
        EventSelector.NONE: "None",
        EventSelector.PM_TEMP_HUM_HCHO: "PMxx, Temperature, Humidity, Formaldehyde",
        EventSelector.ALL_COUNTS: "All particle count bins",
        EventSelector.ALL: "All (PM, count bins, temp, hum, HCHO)",
    }

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        form = []
        port = str(self._config.get("serial_port", "/dev/ttyAMA0"))
        try:
            import serial.tools.list_ports
            ports_found = serial.tools.list_ports.comports()
            port_options = [{"value": p.device, "label": p.device} for p in ports_found]
            if not port_options:
                port_options = [{"value": "", "label": "No serial ports found"}]
            elif port and not any(p["value"] == port for p in port_options):
                port_options.append({"value": port, "label": port})
        except Exception:
            port_options = [{"value": port or "", "label": port or "/dev/ttyAMA0"}]
        form.append({"name": "serial_port", "label": "Serial Device", "type": "select",
                     "value": port, "options": port_options})
        form.append({"name": "baudrate", "label": "Baud Rate", "type": "number",
                     "value": self._config.get("baudrate") or 9600})

        model = self._config.get("model") or PMSx003Type.PMS1003_5003_7003
        if isinstance(model, str):
            try:
                model = int(model)
            except (ValueError, TypeError):
                model = PMSx003Type.PMS1003_5003_7003
        model_opts = [{"value": int(k), "label": v} for k, v in self.MODEL_LABELS.items()]
        form.append({"name": "model", "label": "Sensor Model", "type": "select",
                     "value": int(model), "options": model_opts})
        try:
            cmodel = PMSx003Type(int(model))
        except (ValueError, TypeError):
            cmodel = PMSx003Type.PMS1003_5003_7003
        valid_outputs = self._valid_outputs_for_model(cmodel)
        output_opts = [{"value": int(k), "label": v} for k, v in self.OUTPUT_LABELS.items() if k in valid_outputs]
        form.append({"name": "output_selector", "label": "Output Values", "type": "select",
                     "value": self._config.get("output_selector") or 0, "options": output_opts})
        event_opts = [{"value": int(k), "label": v} for k, v in self.EVENT_LABELS.items()]
        form.append({"name": "event_selector", "label": "Events for non-output values", "type": "select",
                     "value": self._config.get("event_selector") or 0, "options": event_opts})
        form.append({"name": "rst_pin", "label": "RST Pin", "type": "number",
                     "value": self._config.get("rst_pin") or -1})
        form.append({"name": "pwr_pin", "label": "SET/PWR Pin", "type": "number",
                     "value": self._config.get("pwr_pin") or -1})
        form.append({"name": "wake_delay", "label": "Sensor init time after wake (sec)", "type": "number",
                     "value": self._config.get("wake_delay") or 0})
        _oversample = self._config.get("oversample")
        form.append({"name": "oversample", "label": "Oversampling", "type": "checkbox",
                     "value": _oversample if _oversample is not None else True})
        _split_bins = self._config.get("split_bins")
        form.append({"name": "split_bins", "label": "Split count bins", "type": "checkbox",
                     "value": _split_bins if _split_bins is not None else False})
        event.data["form"] = form
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        model = await self._model()
        output = self._output()
        clamped_output = self._clamp_output(model, output)
        if clamped_output != output:
            self._config["output_selector"] = int(clamped_output)
        esel = self._clamp_event(model, clamped_output, self._event_sel())
        if esel != self._event_sel():
            self._config["event_selector"] = int(esel)
        return True

    async def on_plugin_get_devicegpionames(self, event: Event) -> bool | None:
        event.data["gpio_names"] = [
            {"label": "RX Pin", "number": 1},
            {"label": "TX Pin", "number": 2},
        ]
        return True

    async def on_plugin_get_devicevaluecount(self, event: Event) -> bool | None:
        output = self._output()
        event.data["value_count"] = OUTPUT_VALUE_COUNT.get(output, 3)
        return True

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        output = self._output()
        event.data["vtype"] = OUTPUT_SENSOR_TYPE.get(output, SENSOR_TYPE_TRIPLE)
        return True

    async def on_plugin_get_discovery_vtypes(self, event: Event) -> bool | None:
        event.data["vtypes"] = [SENSOR_V_TYPE_PM1_0, SENSOR_V_TYPE_PM2_5, SENSOR_V_TYPE_PM10]
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"pm1.0": 0, "pm2.5": 0, "pm10": 0}

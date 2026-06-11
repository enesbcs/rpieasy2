from __future__ import annotations

import asyncio
import datetime
import logging
import math
import re
import time
from typing import Any

from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_SERIAL, SENSOR_TYPE_QUAD
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p082")

P082_NR_OUTPUT_VALUES = 4

P082_QUERY_LONG = 0
P082_QUERY_LAT = 1
P082_QUERY_ALT = 2
P082_QUERY_SPD = 3
P082_QUERY_SATVIS = 4
P082_QUERY_SATUSE = 5
P082_QUERY_HDOP = 6
P082_QUERY_FIXQ = 7
P082_QUERY_DB_MAX = 8
P082_QUERY_CHKSUM_FAIL = 9
P082_QUERY_DISTANCE = 10
P082_QUERY_DIST_REF = 11
P082_QUERY_COURSE = 12
P082_NR_OUTPUT_OPTIONS = 13

QUERY_LABELS: dict[int, str] = {
    P082_QUERY_LONG: "Longitude",
    P082_QUERY_LAT: "Latitude",
    P082_QUERY_ALT: "Altitude",
    P082_QUERY_SPD: "Speed (m/s)",
    P082_QUERY_SATVIS: "Satellites Visible",
    P082_QUERY_SATUSE: "Satellites Tracked",
    P082_QUERY_HDOP: "HDOP",
    P082_QUERY_FIXQ: "Fix Quality",
    P082_QUERY_DB_MAX: "Max SNR (dBHz)",
    P082_QUERY_CHKSUM_FAIL: "Checksum Fail",
    P082_QUERY_DISTANCE: "Distance (ODO)",
    P082_QUERY_DIST_REF: "Distance from Reference Point",
    P082_QUERY_COURSE: "Course (Bearing)",
}

QUERY_VNAMES: dict[int, str] = {
    P082_QUERY_LONG: "long",
    P082_QUERY_LAT: "lat",
    P082_QUERY_ALT: "alt",
    P082_QUERY_SPD: "spd",
    P082_QUERY_SATVIS: "sat_vis",
    P082_QUERY_SATUSE: "sat_tr",
    P082_QUERY_HDOP: "hdop",
    P082_QUERY_FIXQ: "fix_qual",
    P082_QUERY_DB_MAX: "snr_max",
    P082_QUERY_CHKSUM_FAIL: "chksum_fail",
    P082_QUERY_DISTANCE: "dist",
    P082_QUERY_DIST_REF: "dist_ref",
    P082_QUERY_COURSE: "course",
}

QUERY_DECIMALS: dict[int, int] = {
    P082_QUERY_LONG: 6,
    P082_QUERY_LAT: 6,
    P082_QUERY_ALT: 2,
    P082_QUERY_SPD: 2,
    P082_QUERY_SATVIS: 0,
    P082_QUERY_SATUSE: 0,
    P082_QUERY_HDOP: 3,
    P082_QUERY_FIXQ: 0,
    P082_QUERY_DB_MAX: 0,
    P082_QUERY_CHKSUM_FAIL: 0,
    P082_QUERY_DISTANCE: 1,
    P082_QUERY_DIST_REF: 1,
    P082_QUERY_COURSE: 1,
}

FIX_QUALITY_LABELS: dict[int, str] = {
    0: "Invalid",
    1: "GPS",
    2: "DGPS",
    3: "PPS",
    4: "RTK",
    5: "FloatRTK",
    6: "Estimated",
    7: "Manual",
    8: "Simulated",
}

UBLOX_GPS_STANDBY = bytes([
    0xB5, 0x62, 0x02, 0x41, 0x08, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x02, 0x00,
    0x00, 0x00, 0x4D, 0x3B,
])


def dm_to_sd(dm: str) -> float:
    if not dm or dm == "0":
        return 0.0
    try:
        m = re.match(r"^(\d+)(\d\d\.\d+)$", dm)
        if m:
            d, m_str = m.groups()
            return float(d) + float(m_str) / 60.0
    except Exception:
        pass
    return 0.0


def timestamp(s: str) -> datetime.time | None:
    try:
        ms_s = s[6:]
        ms = int(float(ms_s) * 1000000) if ms_s else 0
        return datetime.time(
            hour=int(s[0:2]),
            minute=int(s[2:4]),
            second=int(s[4:6]),
            microsecond=ms,
        )
    except Exception:
        return None


def datestamp(s: str) -> Any:
    try:
        return datetime.datetime.strptime(s, "%d%m%y").date()
    except Exception:
        pass
    try:
        return datetime.datetime.strptime(s, "%d%m%Y").date()
    except Exception:
        pass
    return s


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


class P082GPS(PluginBase):
    PLUGIN_ID = 82
    PLUGIN_NAME = "Position - GPS"
    PLUGIN_VALUES = P082_NR_OUTPUT_VALUES
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_SERIAL,
        vtype=SENSOR_TYPE_QUAD,
        value_count=P082_NR_OUTPUT_VALUES,
        formula_option=True,
        send_data_option=True,
        timer_option=True,
        plugin_stats=True,
    )

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._serial = None
        self._port: str = "/dev/ttyAMA0"
        self._baud: int = 9600
        self._pwr_pin: int = -1
        self._buf = bytearray()
        self._reader_task: asyncio.Task | None = None
        self._reader_running = False

        self._cache: list[float] = [0.0] * P082_NR_OUTPUT_OPTIONS
        self._query_config: list[int] = [
            P082_QUERY_LONG, P082_QUERY_LAT,
            P082_QUERY_ALT, P082_QUERY_SPD,
        ]

        self._last_fix_time: float = 0.0
        self._fix_timeout_ms: int = 2500
        self._validloc = -1
        self._enable_time = False
        self._chksum_fail_count = 0
        self._chksum_pass_count = 0
        self._set_system_time = False
        self._last_system_time_set: float = 0.0
        self._gps_time_str: str = ""
        self._gps_date_str: str = ""

        self._distance_threshold: float = 0.0
        self._last_lat: float = 0.0
        self._last_lon: float = 0.0
        self._total_distance: float = 0.0
        self._ref_lat: float = 0.0
        self._ref_lon: float = 0.0
        self._last_measurement: float = 0.0

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._port = self._config.get("serial_port", "/dev/ttyAMA0")
        try:
            self._baud = int(self._config.get("baudrate", 9600))
        except (ValueError, TypeError):
            self._baud = 9600
        self._enable_time = self._config.get("enable_time", False)
        try:
            self._fix_timeout_ms = int(self._config.get("fix_timeout", 2500))
        except (ValueError, TypeError):
            self._fix_timeout_ms = 2500
        try:
            self._distance_threshold = float(self._config.get("distance_threshold", 0.0))
        except (ValueError, TypeError):
            self._distance_threshold = 0.0
        try:
            self._ref_lat = float(self._config.get("lat_ref", 0.0))
        except (ValueError, TypeError):
            self._ref_lat = 0.0
        try:
            self._ref_lon = float(self._config.get("lng_ref", 0.0))
        except (ValueError, TypeError):
            self._ref_lon = 0.0
        try:
            self._pwr_pin = int(self._config.get("pwr_pin", -1))
        except (ValueError, TypeError):
            self._pwr_pin = -1
        self._set_system_time = self._config.get("set_system_time", False)
        for i in range(P082_NR_OUTPUT_VALUES):
            try:
                self._query_config[i] = int(self._config.get(f"query{i + 1}", [P082_QUERY_LONG, P082_QUERY_LAT, P082_QUERY_ALT, P082_QUERY_SPD][i]))
            except (ValueError, TypeError):
                self._query_config[i] = [P082_QUERY_LONG, P082_QUERY_LAT, P082_QUERY_ALT, P082_QUERY_SPD][i]
        if self._hw and self._pwr_pin >= 0:
            self._hw.gpio.claim_output(self._pwr_pin)
        ok = await self._setup_serial()
        if ok:
            self._start_reader()
        return ok

    async def on_plugin_exit(self, event: Event) -> bool | None:
        self._stop_reader()
        if self._serial:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None
        return True

    async def _setup_serial(self) -> bool:
        try:
            import serial as pyserial
            self._serial = pyserial.Serial(
                port=self._port,
                baudrate=self._baud,
                bytesize=pyserial.EIGHTBITS,
                parity=pyserial.PARITY_NONE,
                stopbits=pyserial.STOPBITS_ONE,
                timeout=0,
            )
            return True
        except Exception as e:
            logger.error("GPS serial init failed: %s", e)
            self._serial = None
            return False

    def _start_reader(self) -> None:
        if self._reader_task is not None:
            return
        self._reader_running = True
        self._reader_task = asyncio.create_task(self._serial_reader())

    def _stop_reader(self) -> None:
        self._reader_running = False
        if self._reader_task is not None:
            self._reader_task.cancel()
            self._reader_task = None

    async def _serial_reader(self) -> None:
        while self._reader_running:
            if not self._serial or not self._serial.is_open:
                await asyncio.sleep(0.1)
                continue
            try:
                if self._serial.in_waiting:
                    data = await asyncio.to_thread(
                        self._serial.read, self._serial.in_waiting
                    )
                    if data:
                        self._buf.extend(data)
                else:
                    await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("GPS serial reader: %s", e)
                await asyncio.sleep(0.1)

    def has_fix(self) -> bool:
        if self._last_fix_time == 0.0:
            return False
        age_ms = (time.time() - self._last_fix_time) * 1000.0
        return age_ms < self._fix_timeout_ms

    async def on_plugin_fifty_per_second(self, event: Event) -> bool | None:
        self._parse_buffer()
        if self.has_fix() and self._set_system_time:
            await self._try_set_system_time()
        return None

    def _parse_buffer(self) -> None:
        while b"$" in self._buf:
            start = self._buf.find(b"$")
            if start > 0:
                self._buf = self._buf[start:]
            end = self._buf.find(b"\n")
            if end < 0:
                return
            line = self._buf[: end + 1]
            self._buf = self._buf[end + 1 :]
            try:
                sentence = line.decode("utf-8", errors="replace").strip()
                self._parse_sentence(sentence)
            except Exception:
                pass

    def _parse_sentence(self, sentence: str) -> None:
        if "*" in sentence:
            gps_str, chk_sum_str = sentence.split("*", 1)
        elif "_" in sentence:
            gps_str, chk_sum_str = sentence.split("_", 1)
        else:
            chk_sum_str = sentence[-2:]
            gps_str = sentence[:-2]

        parts = gps_str.split(",")
        if not parts:
            return

        sentence_id = parts[0]

        chk_val = 0
        for ch in gps_str[1:]:
            chk_val ^= ord(ch)
        try:
            expected = int(chk_sum_str.strip(), 16)
            if chk_val != expected:
                self._chksum_fail_count += 1
                self._cache[P082_QUERY_CHKSUM_FAIL] = float(self._chksum_fail_count)
                return
        except (ValueError, IndexError):
            self._chksum_fail_count += 1
            self._cache[P082_QUERY_CHKSUM_FAIL] = float(self._chksum_fail_count)
            return

        self._chksum_pass_count += 1

        if "GGA" in sentence_id:
            self._parse_gga(parts)

        if "RMC" in sentence_id:
            self._parse_rmc(parts)

        if "ZDA" in sentence_id:
            self._parse_zda(parts)

        if "VTG" in sentence_id:
            self._parse_vtg(parts)

    def _update_sat_stats(self) -> None:
        pass

    def _parse_gga(self, parts: list[str]) -> None:
        keys = [
            "strType", "fixTime", "lat", "latDir", "lon", "lonDir",
            "fixQual", "numSat", "horDil", "alt", "altUnit",
            "galt", "galtUnit", "DPGS_updt", "DPGS_ID",
        ]
        gpsdat: dict[str, str | None] = {}
        for i, k in enumerate(keys):
            gpsdat[k] = parts[i] if i < len(parts) else None

        now = time.time()
        prev_fix = self.has_fix()

        try:
            fq = int(str(gpsdat.get("fixQual", "0")))
        except (ValueError, TypeError):
            fq = 0

        self._cache[P082_QUERY_FIXQ] = float(fq)
        self._validloc = 1 if fq > 0 else 0

        num_sat_str = gpsdat.get("numSat", "0")
        try:
            num_sat = int(str(num_sat_str))
        except (ValueError, TypeError):
            num_sat = 0

        self._cache[P082_QUERY_SATVIS] = float(num_sat)
        self._cache[P082_QUERY_SATUSE] = float(num_sat)

        hdop_str = gpsdat.get("horDil", "0")
        try:
            hdop = float(str(hdop_str))
        except (ValueError, TypeError):
            hdop = 0.0
        self._cache[P082_QUERY_HDOP] = hdop

        if self._validloc == 1:
            alt_str = gpsdat.get("alt", "0")
            try:
                alt_val = float(str(alt_str))
            except (ValueError, TypeError):
                alt_val = 0.0
            self._cache[P082_QUERY_ALT] = alt_val

            lat_raw = gpsdat.get("lat")
            if lat_raw:
                lat_val = dm_to_sd(str(lat_raw))
                if str(gpsdat.get("latDir")) == "S":
                    lat_val *= -1
                self._cache[P082_QUERY_LAT] = lat_val

            lon_raw = gpsdat.get("lon")
            if lon_raw:
                lon_val = dm_to_sd(str(lon_raw))
                if str(gpsdat.get("lonDir")) == "W":
                    lon_val *= -1
                self._cache[P082_QUERY_LONG] = lon_val

            self._last_fix_time = now
            self._update_distance_tracking()

        cur_fix = self.has_fix()
        if cur_fix != prev_fix:
            if cur_fix:
                logger.info("GPS: Got Fix")
            else:
                logger.info("GPS: Lost Fix")

    def _parse_rmc(self, parts: list[str]) -> None:
        keys = [
            "strType", "fixTime", "status", "lat", "latdir", "lon",
            "londir", "speed_over_ground_knots", "track_made_good",
            "fixDate", "mag_variation", "mag_variation_dir", "faa_mode", "checksum",
        ]
        rmc: dict[str, str | None] = {}
        for i, k in enumerate(keys):
            rmc[k] = parts[i] if i < len(parts) else None

        speed_knots_str = rmc.get("speed_over_ground_knots")
        if speed_knots_str:
            try:
                knots = float(str(speed_knots_str))
                self._cache[P082_QUERY_SPD] = knots * 0.514444
            except (ValueError, TypeError):
                pass

        course_str = rmc.get("track_made_good")
        if course_str:
            try:
                self._cache[P082_QUERY_COURSE] = float(str(course_str))
            except (ValueError, TypeError):
                pass

        fix_date = rmc.get("fixDate")
        fix_time = rmc.get("fixTime")
        if fix_date and fix_time and fix_time != "000000":
            try:
                d = str(fix_date)
                t = str(fix_time)
                self._gps_date_str = f"20{d[4:6]}-{d[2:4]}-{d[0:2]}"
                self._gps_time_str = f"{t[0:2]}:{t[2:4]}:{t[4:6]}"
            except Exception:
                pass

    def _parse_zda(self, parts: list[str]) -> None:
        keys = ["strType", "fixTime", "day", "mon", "year", "lzoneh", "lzonem"]
        zda: dict[str, str | None] = {}
        for i, k in enumerate(keys):
            zda[k] = parts[i] if i < len(parts) else None

        fix_time = zda.get("fixTime")
        day = zda.get("day")
        mon = zda.get("mon")
        year = zda.get("year")

        if fix_time and day and mon and year:
            self._gps_time_str = f"{fix_time[:2]}:{fix_time[2:4]}:{fix_time[4:6]}"
            y = year if len(str(year)) == 4 else f"20{year}"
            self._gps_date_str = f"{y}-{mon}-{day}"

        if self._enable_time:
            if fix_time:
                ts = timestamp(str(fix_time))
            if day and mon and year:
                ds = datestamp(f"{day}{mon}{year}")

    def _parse_vtg(self, parts: list[str]) -> None:
        keys = [
            "strType", "trueTrack", "trueTrackRel", "magnetTrack",
            "magnetTrackRel", "speedKnot", "speedKnotUnit",
            "speedKm", "speedKmUnit",
        ]
        vtg: dict[str, str | None] = {}
        for i, k in enumerate(keys):
            vtg[k] = parts[i] if i < len(parts) else None

        speed_knots_str = vtg.get("speedKnot")
        if speed_knots_str:
            try:
                knots = float(str(speed_knots_str))
                self._cache[P082_QUERY_SPD] = knots * 0.514444
            except (ValueError, TypeError):
                pass

        course_str = vtg.get("trueTrack")
        if course_str:
            try:
                self._cache[P082_QUERY_COURSE] = float(str(course_str))
            except (ValueError, TypeError):
                pass

    def _update_distance_tracking(self) -> None:
        lat = self._cache[P082_QUERY_LAT]
        lon = self._cache[P082_QUERY_LONG]

        if self._last_lat != 0.0 or self._last_lon != 0.0:
            dist = _haversine(self._last_lat, self._last_lon, lat, lon)
            if dist > 0.1:
                self._total_distance += dist
                self._cache[P082_QUERY_DISTANCE] = self._total_distance

        self._last_lat = lat
        self._last_lon = lon

        if self._ref_lat != 0.0 or self._ref_lon != 0.0:
            dist_ref = _haversine(self._ref_lat, self._ref_lon, lat, lon)
            self._cache[P082_QUERY_DIST_REF] = dist_ref

    async def _write_to_gps(self, data: bytes) -> bool:
        if not self._serial or not self._serial.is_open:
            return False
        try:
            await asyncio.to_thread(self._serial.write, data)
            return True
        except Exception as e:
            logger.error("GPS write error: %s", e)
            return False

    async def _try_set_system_time(self) -> None:
        if not self._set_system_time:
            return
        if not self._gps_date_str or not self._gps_time_str:
            return
        if self._gps_date_str == "0" or self._gps_time_str == "0":
            return
        now = time.time()
        if now - self._last_system_time_set < 300:
            return
        dt_str = f"{self._gps_date_str} {self._gps_time_str}"
        utc_str = f"{dt_str} UTC"
        try:
            proc = await asyncio.create_subprocess_exec(
                "date", "-u", "-s", utc_str,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=5)
            if proc.returncode == 0:
                self._last_system_time_set = now
                logger.info("GPS: System time set to %s", utc_str)
        except Exception as e:
            logger.error("GPS: Failed to set system time: %s", e)

    async def _power_down(self) -> bool:
        return await self._write_to_gps(UBLOX_GPS_STANDBY)

    async def _wake_up(self) -> bool:
        if self._serial and self._serial.is_open:
            try:
                await asyncio.to_thread(self._serial.write, b"\n")
                return True
            except Exception as e:
                logger.error("GPS wake error: %s", e)
        if self._pwr_pin >= 0 and self._hw:
            try:
                self._hw.gpio.write(self._pwr_pin, 1)
            except Exception:
                pass
        return False

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self.has_fix():
            return False

        now = time.time()
        try:
            interval = float(self._config.get("task_interval", 60))
        except (ValueError, TypeError):
            interval = 60
        if self._last_measurement > 0 and (now - self._last_measurement) < interval:
            if self._distance_threshold <= 0:
                return False

        self._cache[P082_QUERY_DIST_REF] = 0.0
        lat = self._cache[P082_QUERY_LAT]
        lon = self._cache[P082_QUERY_LONG]
        if self._ref_lat != 0.0 or self._ref_lon != 0.0:
            dist_ref = _haversine(self._ref_lat, self._ref_lon, lat, lon)
            self._cache[P082_QUERY_DIST_REF] = dist_ref

        triggered = False
        if self._distance_threshold > 0 and self._last_measurement > 0:
            dist_since_last = _haversine(
                self._last_lat, self._last_lon, lat, lon
            )
            if dist_since_last >= self._distance_threshold:
                triggered = True
        else:
            triggered = True

        if not triggered:
            return False

        self._last_measurement = now
        event.data["values"] = {}
        for i, q in enumerate(self._query_config):
            if q < P082_NR_OUTPUT_OPTIONS:
                label = QUERY_LABELS.get(q, f"val{i}")
                event.data["values"][label] = self._cache[q]
        return True

    async def on_plugin_write(self, event: Event) -> bool | None:
        command = (event.string1 or "").strip().lower()
        parts = command.split(",")
        cmd = parts[0] if parts else ""
        sub = parts[1].strip() if len(parts) > 1 else ""

        if cmd == "gps" and sub == "wake":
            await self._wake_up()
            logger.info("GPS: Wake command")
            return True

        if cmd == "gps" and sub == "sleep":
            await self._power_down()
            logger.info("GPS: Sleep command")
            return True

        if cmd == "gps" and sub == "reset":
            self._total_distance = 0.0
            self._cache[P082_QUERY_DISTANCE] = 0.0
            self._chksum_fail_count = 0
            self._cache[P082_QUERY_CHKSUM_FAIL] = 0.0
            logger.info("GPS: Reset ODO and stats")
            return True

        return False

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config.setdefault("serial_port", "/dev/ttyAMA0")
        self._config.setdefault("baudrate", 9600)
        self._config.setdefault("enable_time", False)
        self._config.setdefault("fix_timeout", 2500)
        self._config.setdefault("distance_threshold", 0)
        self._config.setdefault("lat_ref", 0.0)
        self._config.setdefault("lng_ref", 0.0)
        self._config.setdefault("pwr_pin", -1)
        self._config.setdefault("set_system_time", False)
        self._config.setdefault("query1", P082_QUERY_LONG)
        self._config.setdefault("query2", P082_QUERY_LAT)
        self._config.setdefault("query3", P082_QUERY_ALT)
        self._config.setdefault("query4", P082_QUERY_SPD)
        return True

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
                     "value": self._config.get("baudrate", 9600)})
        fix_ok = 1 if self.has_fix() else 0
        form.append({"name": "_fix_status", "label": "Fix", "type": "text",
                      "value": str(fix_ok), "readonly": True})
        if fix_ok:
            form.append({"name": "_sat_vis", "label": "Satellites in view", "type": "text",
                          "value": str(int(self._cache[P082_QUERY_SATVIS])), "readonly": True})
            form.append({"name": "_hdop", "label": "HDOP", "type": "text",
                          "value": f"{self._cache[P082_QUERY_HDOP]:.3f}", "readonly": True})
            fq = int(self._cache[P082_QUERY_FIXQ])
            fq_label = FIX_QUALITY_LABELS.get(fq, f"Unknown ({fq})")
            form.append({"name": "_fix_qual", "label": "Fix Quality", "type": "text",
                          "value": f"{fq} - {fq_label}", "readonly": True})

        form.append({"name": "enable_time", "label": "Enable time decoding (ZDA)", "type": "checkbox",
                      "value": self._config.get("enable_time", False)})

        form.append({"name": "fix_timeout", "label": "Fix Timeout (ms)", "type": "number",
                      "value": self._config.get("fix_timeout", 2500), "min": 100, "max": 10000})
        form.append({"name": "set_system_time", "label": "Set system time from GPS (requires root)", "type": "checkbox",
                      "value": self._config.get("set_system_time", False)})
        form.append({"name": "distance_threshold", "label": "Distance Update Interval (m)", "type": "number",
                      "value": self._config.get("distance_threshold", 0), "min": 0, "max": 10000})
        form.append({"name": "pwr_pin", "label": "Power Pin (for gps,wake/sleep)", "type": "number",
                      "value": self._config.get("pwr_pin", -1)})

        form.append({"name": "lat_ref", "label": "Reference Latitude", "type": "number",
                      "value": self._config.get("lat_ref", 0.0), "step": "any"})
        form.append({"name": "lng_ref", "label": "Reference Longitude", "type": "number",
                      "value": self._config.get("lng_ref", 0.0), "step": "any"})

        event.data["form"] = form
        return True

    async def on_plugin_webform_load_output_selector(self, event: Event) -> bool | None:
        opts = [{"value": k, "label": v} for k, v in sorted(QUERY_LABELS.items())]
        decs = [QUERY_DECIMALS.get(k, 2) for k in sorted(QUERY_LABELS.keys())]
        event.data["output_selector"] = {
            "name": None,
            "label": "Output Values",
            "options": opts,
            "fields": [
                {"name": "query1", "label": "Value 1", "value": self._config.get("query1", P082_QUERY_LONG)},
                {"name": "query2", "label": "Value 2", "value": self._config.get("query2", P082_QUERY_LAT)},
                {"name": "query3", "label": "Value 3", "value": self._config.get("query3", P082_QUERY_ALT)},
                {"name": "query4", "label": "Value 4", "value": self._config.get("query4", P082_QUERY_SPD)},
            ],
        }
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        for i in range(P082_NR_OUTPUT_VALUES):
            try:
                self._query_config[i] = int(self._config.get(f"query{i + 1}", [P082_QUERY_LONG, P082_QUERY_LAT, P082_QUERY_ALT, P082_QUERY_SPD][i]))
            except (ValueError, TypeError):
                self._query_config[i] = [P082_QUERY_LONG, P082_QUERY_LAT, P082_QUERY_ALT, P082_QUERY_SPD][i]
        try:
            self._fix_timeout_ms = int(self._config.get("fix_timeout", 2500))
        except (ValueError, TypeError):
            self._fix_timeout_ms = 2500
        try:
            self._distance_threshold = float(self._config.get("distance_threshold", 0.0))
        except (ValueError, TypeError):
            self._distance_threshold = 0.0
        try:
            self._ref_lat = float(self._config.get("lat_ref", 0.0))
        except (ValueError, TypeError):
            self._ref_lat = 0.0
        try:
            self._ref_lon = float(self._config.get("lng_ref", 0.0))
        except (ValueError, TypeError):
            self._ref_lon = 0.0
        try:
            self._pwr_pin = int(self._config.get("pwr_pin", -1))
        except (ValueError, TypeError):
            self._pwr_pin = -1
        self._set_system_time = self._config.get("set_system_time", False)
        return True

    async def on_plugin_get_devicegpionames(self, event: Event) -> bool | None:
        event.data["gpio_names"] = [
            {"label": "RX Pin", "number": 1},
            {"label": "TX Pin (optional)", "number": 2},
            {"label": "PPS Pin (optional)", "number": 3},
        ]
        return True

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        return None

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {QUERY_LABELS.get(P082_QUERY_LONG, "Longitude"): 0.0,
                QUERY_LABELS.get(P082_QUERY_LAT, "Latitude"): 0.0,
                QUERY_LABELS.get(P082_QUERY_ALT, "Altitude"): 0.0,
                QUERY_LABELS.get(P082_QUERY_SPD, "Speed"): 0.0}

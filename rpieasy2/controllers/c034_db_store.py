from __future__ import annotations

import datetime
import logging
import time
from typing import Any

from rpieasy2.core.config import get_config
from rpieasy2.core.controller_base import ControllerBase
from rpieasy2.core.events import Event
from rpieasy2.core.rpiconst import SENSOR_TYPE_STRING
from rpieasy2.core.util import get_wifi_rssi

logger = logging.getLogger("rpieasy2.controller.c034")

DB_TYPE_SQLITE = 0
DB_TYPE_MYSQL = 1

_last_rssi_val = 0
_last_rssi_time = 0.0


def _get_cached_rssi(urssi: int = -1) -> str:
    global _last_rssi_val, _last_rssi_time
    now = time.time()
    if (now - _last_rssi_time) >= 3 and urssi == -1:
        _last_rssi_val = get_wifi_rssi() or 0
        _last_rssi_time = now
    rssi = urssi if urssi != -1 else _last_rssi_val
    return str(int(rssi))


class C034DBStore(ControllerBase):
    CONTROLLER_ID = 34
    CONTROLLER_NAME = "DB Data Storage"
    usesID = False
    usesAccount = True
    usesPassword = True
    usesHost = True
    usesPort = True
    needsNetwork = False
    usesQueue = True
    usesTemplate = False
    usesCheckReply = False
    usesTimeout = False
    allowsExpire = False

    def __init__(self):
        super().__init__()
        self.db = None
        self.provider = -1
        self.connected = False
        self.dbname = ""
        self.initialized = False

    async def on_controller_init(self, event: Event) -> bool | None:
        config: dict[str, Any] = event.data.get("controller_config", {})
        self._init_queue_from_config(config)
        self.provider = int(config.get("c034_dbtype", DB_TYPE_SQLITE))
        self.dbname = config.get("c034_dbname", "")
        self.db_controllerip = config.get("controllerip", config.get("host", "127.0.0.1"))
        self.db_controllerport = int(config.get("controllerport", config.get("port", 3306)) or 3306)
        self.db_controlleruser = config.get("controlleruser", config.get("username", ""))
        self.db_controllerpassword = config.get("controllerpassword", config.get("password", ""))
        self._connect()
        return True

    def _connect(self) -> None:
        self.connected = False
        try:
            if self.provider == DB_TYPE_SQLITE:
                from rpieasy2.lib.db_sqlite import DB_SQLite3
                if not self.dbname:
                    return
                self.db = DB_SQLite3()
                self.db.connect(self.dbname)
                if not self.db.isexist_sensortable():
                    self.db.create_sensortable()
            elif self.provider == DB_TYPE_MYSQL:
                from rpieasy2.lib.db_mysql import DB_MySQL
                if not self.dbname:
                    return
                self.db = DB_MySQL()
                self.db.connect(
                    hostname=self.db_controllerip,
                    dbname=self.dbname,
                    username=self.db_controlleruser,
                    passw=self.db_controllerpassword,
                    dbport=self.db_controllerport,
                )
                if not self.db.isexist_sensortable():
                    self.db.create_sensortable()
            self.connected = True
        except Exception as e:
            logger.error("DB connect failed: %s", e)
            self.connected = False
        self.initialized = self.connected

    def _disconnect(self) -> None:
        if self.db is not None:
            try:
                self.db.disconnect()
            except Exception:
                pass
        self.db = None
        self.connected = False
        self.initialized = False

    async def on_controller_send(self, event: Event) -> bool | None:
        if not self.connected or not self.db or event.task_index < 0:
            return False

        task_config: dict[str, Any] = event.data.get("task_config", {})
        values: dict[str, Any] = event.data.get("values", {})
        named_values = values.get("named_values", values)

        config = get_config()
        sys_cfg = config.data.get("system", {})
        unit = sys_cfg.get("unit", 0)
        nodename = sys_cfg.get("name", "RPIEasy2")
        taskname = task_config.get("TDN", task_config.get("name", f"Task{event.task_index + 1}"))
        sensortype = int(task_config.get("TDSF", 1))

        values_list = list(named_values.values())
        vcount = len(values_list)

        sql = "INSERT INTO easysensor (time,unit,nodename,taskname,sensortype"
        vals = f" VALUES ('{datetime.datetime.now()}',{unit},'{nodename}','{taskname}',{sensortype}"

        sql += ",tasknum"
        vals += f",{event.task_index + 1}"

        if sensortype == SENSOR_TYPE_STRING:
            sql += ",valuetext"
            text_val = str(values_list[0]) if values_list else ""
            vals += f",'{text_val}'"
        elif vcount > 0:
            for i in range(min(vcount, 4)):
                vnum = i + 1
                sql += f",value{vnum}"
                try:
                    fval = float(values_list[i])
                except (ValueError, TypeError):
                    fval = 0.0
                vals += f",{fval}"

        rssi_str = _get_cached_rssi()
        try:
            rssi = int(rssi_str)
        except ValueError:
            rssi = 0
        sql += ",rssi"
        vals += f",{rssi}"

        sql += ",battery"
        vals += ",255"

        sql += ")"
        vals += ")"
        full_sql = sql + vals

        try:
            self.db.sqlexec(full_sql)
        except Exception as e:
            logger.error("DB Store insert failed: %s", e)
            return False
        return True

    async def on_controller_process_queue(self, event: Event) -> bool | None:
        if self.connected and self.db is not None:
            try:
                self.db.save(True)
            except Exception:
                pass
        return True

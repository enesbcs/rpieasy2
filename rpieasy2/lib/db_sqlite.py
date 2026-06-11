from __future__ import annotations

import logging
import sqlite3
import time

logger = logging.getLogger("rpieasy2.lib.db_sqlite")


class DB_SQLite3:

    def __init__(self):
        self.connected = False
        self.db: sqlite3.Connection | None = None
        self.cur: sqlite3.Cursor | None = None
        self.datasetchanged = False
        self.writeinprogress = False

    def connect(self, dbname: str | None = None) -> bool:
        self.connected = False
        if not dbname:
            return False
        try:
            self.db = sqlite3.connect(database=dbname, check_same_thread=False)
            self.cur = self.db.cursor()
            self.connected = True
            self.datasetchanged = False
            self.writeinprogress = False
        except Exception as e:
            logger.error("SQLite connect failed: %s", e)
            self.connected = False
        return self.connected

    def disconnect(self) -> None:
        if self.connected:
            self.save(True)
            try:
                self.db and self.db.close()
            except Exception:
                pass
        self.connected = False

    def sqlexec(self, sqlstr: str = "") -> bool:
        if not self.connected or not sqlstr:
            return False
        st = sqlstr.strip()
        if st[:4].lower() in ("inse", "upda", "drop", "crea"):
            self.datasetchanged = True
            if self.writeinprogress:
                for _ in range(5):
                    time.sleep(0.1)
                    if not self.writeinprogress:
                        break
            self.writeinprogress = True
        try:
            self.cur and self.cur.execute(sqlstr)
            self.writeinprogress = False
            return True
        except Exception as e:
            logger.error("SQLite exec error: %s", e)
            self.writeinprogress = False
            return False

    def sqlget(self):
        if self.connected and self.cur:
            try:
                return self.cur.fetchone()
            except Exception:
                pass
        return None

    def is_writeable(self) -> bool:
        return self.connected and not self.writeinprogress

    def is_save_needed(self) -> bool:
        return self.datasetchanged

    def save(self, when_needed: bool = False) -> bool:
        if self.connected and self.db:
            if when_needed and not self.datasetchanged:
                return False
            try:
                self.db.commit()
            except Exception:
                pass
        self.datasetchanged = False
        return True

    def create_sensortable(self) -> None:
        self.sqlexec("DROP TABLE IF EXISTS easysensor")
        self.sqlexec(
            "CREATE TABLE IF NOT EXISTS easysensor ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "time TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "unit INTEGER DEFAULT 0, "
            "nodename TEXT DEFAULT NULL, "
            "tasknum INTEGER, "
            "taskname TEXT DEFAULT NULL, "
            "sensortype INTEGER DEFAULT 1, "
            "value1 REAL, "
            "value2 REAL DEFAULT 0, "
            "value3 REAL DEFAULT 0, "
            "value4 REAL DEFAULT 0, "
            "rssi INTEGER DEFAULT 0, "
            "battery INTEGER DEFAULT 100, "
            "valuetext TEXT DEFAULT NULL"
            ")"
        )
        self.save()

    def isexist_sensortable(self) -> bool:
        self.sqlexec("SELECT count(*) FROM easysensor")
        res = self.sqlget()
        return res is not None

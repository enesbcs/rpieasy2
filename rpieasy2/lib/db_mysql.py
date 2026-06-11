from __future__ import annotations

import logging

logger = logging.getLogger("rpieasy2.lib.db_mysql")

try:
    import pymysql
except ImportError:
    pymysql = None


class DB_MySQL:

    def __init__(self):
        self.connected = False
        self.db = None
        self.cur = None
        self.datasetchanged = False
        self.writeinprogress = False
        self.dbname = ""

    def connect(
        self,
        hostname: str | None = None,
        dbname: str | None = None,
        username: str | None = None,
        passw: str | None = None,
        dbport: int = 0,
    ) -> bool:
        self.connected = False
        if pymysql is None:
            logger.error("pymysql not installed")
            return False
        if not dbname:
            return False
        hostname = hostname or "localhost"
        dbname = dbname or "easydata"
        self.dbname = dbname
        try:
            self.db = pymysql.connect(
                host=hostname, user=username, password=passw,
                db=dbname, charset="utf8mb4",
            )
            self.cur = self.db.cursor()
            self.connected = True
            self.datasetchanged = False
        except Exception:
            try:
                if int(dbport) > 0:
                    self.db = pymysql.connect(
                        host=hostname, user=username, password=passw,
                        charset="utf8mb4", port=int(dbport),
                    )
                else:
                    self.db = pymysql.connect(
                        host=hostname, user=username, password=passw,
                        charset="utf8mb4",
                    )
                self.cur = self.db.cursor()
                self.cur.execute("CREATE DATABASE IF NOT EXISTS " + self.dbname + " CHARACTER SET utf8mb4;")
                self.cur.execute("USE " + self.dbname + ";")
                self.connected = True
                self.datasetchanged = False
            except Exception as e:
                logger.error("MySQL connect failed: %s", e)
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
        if not self.datasetchanged:
            st = sqlstr.strip()
            if st[:4].lower() in ("inse", "upda", "drop", "crea"):
                self.datasetchanged = True
        try:
            self.cur and self.cur.execute(sqlstr)
            return True
        except Exception as e:
            logger.error("MySQL exec error: %s", e)
            return False

    def sqlget(self):
        if self.connected and self.cur:
            try:
                return self.cur.fetchone()
            except Exception:
                pass
        return None

    def is_writeable(self) -> bool:
        return self.connected

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
            "id INT NOT NULL AUTO_INCREMENT, "
            "time TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "unit TINYINT UNSIGNED DEFAULT 0, "
            "nodename VARCHAR(30) DEFAULT NULL, "
            "tasknum SMALLINT, "
            "taskname VARCHAR(30) DEFAULT NULL, "
            "sensortype SMALLINT DEFAULT 1, "
            "value1 FLOAT, "
            "value2 FLOAT DEFAULT 0, "
            "value3 FLOAT DEFAULT 0, "
            "value4 FLOAT DEFAULT 0, "
            "rssi SMALLINT DEFAULT 0, "
            "battery SMALLINT DEFAULT 100, "
            "valuetext VARCHAR(50) DEFAULT NULL, "
            "PRIMARY KEY (id)"
            ")"
        )
        self.save()

    def isexist_sensortable(self) -> bool:
        self.sqlexec("SELECT count(*) FROM easysensor")
        res = self.sqlget()
        return res is not None

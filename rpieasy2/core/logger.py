import collections
import logging
import socket
import sys
import time
import traceback

from rpieasy2.core.rpiconst import LOG_BUFFER_MAXLEN, LOG_TTL

_log_buffer = None

_LOG_LEVEL_MAP = {
    0: "None",
    1: "Error",
    2: "Info",
    3: "Debug",
    4: "Debug More",
}

_CUSTOM_TO_PYTHON_LEVEL: dict[int, int] = {
    0: 100,
    1: logging.ERROR,
    2: logging.INFO,
    3: logging.DEBUG,
    4: logging.DEBUG,
}

_SYSLOG_SEVERITY: dict[int, int] = {
    logging.ERROR: 3,
    logging.WARNING: 4,
    logging.INFO: 5,
    logging.DEBUG: 7,
}


def _get_web_log_level() -> int:
    try:
        from rpieasy2.core.config import get_config
        val = get_config().data.get("system", {}).get("web_log_level", 2)
        return int(val)
    except Exception:
        return 2


class SyslogHandler(logging.Handler):
    def __init__(self):
        super().__init__()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            from rpieasy2.core.config import get_config
            cfg = get_config().data.get("system", {})
            ip = cfg.get("syslog_ip", "")
            if not ip:
                return
            port = int(cfg.get("syslog_port", 514))
            level = int(cfg.get("syslog_level", 0))
            if level == 0:
                return
            min_python_level = _CUSTOM_TO_PYTHON_LEVEL.get(level, logging.INFO)
            if record.levelno < min_python_level:
                return
            devicename = cfg.get("name", "RPIEasy")
            facility = 0
            severity = _SYSLOG_SEVERITY.get(record.levelno, 7)
            prio = facility * 8 + severity
            msg = record.getMessage()
            lstr = f"<{prio}>RPIEasy {devicename}: {msg}"
            data = lstr.encode("utf-8")
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.sendto(data, (ip, port))
            finally:
                s.close()
        except Exception:
            self.handleError(record)


class LogBuffer(logging.Handler):
    def __init__(self, maxlen: int = LOG_BUFFER_MAXLEN):
        super().__init__()
        self.buffer = collections.deque(maxlen=maxlen)

    def emit(self, record: logging.LogRecord) -> None:
        cfg_level = _get_web_log_level()
        min_python_level = _CUSTOM_TO_PYTHON_LEVEL.get(cfg_level, logging.INFO)
        if record.levelno < min_python_level:
            return
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.created))
        text = f"{ts} [{logging.getLevelName(record.levelno)}] {record.name}: {record.getMessage()}"
        if record.exc_text:
            text += "\n" + record.exc_text
        if record.exc_info and not record.exc_text:
            text += "\n" + "".join(traceback.format_exception(*record.exc_info))
        self.buffer.append({
            "timestamp": ts,
            "level": record.levelno,
            "text": text,
        })

    def get_log_json(self) -> dict:
        entries = list(self.buffer)
        level = _get_web_log_level()
        return {
            "Log": {
                "nrEntries": len(entries),
                "TTL": LOG_TTL,
                "SettingsWebLogLevel": level,
                "SettingsWebLogLevelName": _LOG_LEVEL_MAP.get(level, "Unknown"),
                "Entries": [
                    {"timestamp": e["timestamp"], "level": e["level"], "text": e["text"]}
                    for e in entries
                ]
            }
        }


def get_log_buffer() -> LogBuffer:
    global _log_buffer
    return _log_buffer


def setup_logging(level: int = logging.INFO) -> None:
    global _log_buffer
    root = logging.getLogger("rpieasy2")
    for h in list(root.handlers):
        root.removeHandler(h)
    root.setLevel(logging.DEBUG)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    root.addHandler(handler)

    _log_buffer = LogBuffer()
    root.addHandler(_log_buffer)

    root.addHandler(SyslogHandler())


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"rpieasy2.{name}")

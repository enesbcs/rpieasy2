import json
import logging
from pathlib import Path
from typing import Any

from rpieasy2.core.rpiconst import BUILD, DEFAULT_NAME, DEFAULT_P2P_PORT, UNIT, build_to_date_str

logger = logging.getLogger("rpieasy2.config")

_CONFIG = None
_SCRIPT_DIR = Path(__file__).resolve().parent.parent.parent


class RpEasyConfig:
    def __init__(self, path: Path | None = None):
        self.path = path or _SCRIPT_DIR / "config" / "rpieasy2.json"
        self.data: dict[str, Any] = self._default()

    def _fallback_paths(self) -> list[Path]:
        return [
            self.path,
            _SCRIPT_DIR / "config" / "rpieasy2.json",
            _SCRIPT_DIR / "rpieasy2.json",
            Path.home() / "rpieasy2" / "config" / "rpieasy2.json",
            Path.home() / "rpieasy2" / "rpieasy2.json",
        ]

    def _default(self) -> dict[str, Any]:
        return {
            "system": {
                "name": DEFAULT_NAME, "unit": UNIT, "build": build_to_date_str(BUILD),
                "p2p_port": DEFAULT_P2P_PORT, "enable_rules": True,
                "json_bool_output_without_quotes": False,
                "collect_timing_statistics": False,
                "allow_taskvalueset_on_all_plugins": False,
                "syslog_ip": "",
                "syslog_port": 514,
                "syslog_level": 0,
                "ftdi_devices": [],
                "show_unit_of_measure": True,
                "disable_rules_auto_completion": False,
            },
            "controllers": [],
            "notifications": [],
            "tasks": [],
            "rules": [],
        }

    def load(self) -> None:
        for p in self._fallback_paths():
            if p.exists():
                with open(p) as f:
                    loaded = json.load(f)
                    default = self._default()
                    for key, val in loaded.items():
                        if key in default and isinstance(default[key], dict) and isinstance(val, dict):
                            default[key].update(val)
                        else:
                            default[key] = val
                    self.data = default
                    self.path = p
                logger.debug("Config loaded from %s", p)
                return
        logger.debug("No config file found, using defaults")

    def save(self) -> None:
        targets = [self.path] + [p for p in self._fallback_paths() if p != self.path]
        for p in targets:
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                with open(p, "w") as f:
                    json.dump(self.data, f, indent=2)
                self.path = p
                logger.debug("Config saved to %s", p)
                return
            except (OSError, PermissionError):
                continue
        raise PermissionError("Cannot save config to any location, tried: script dir, ~/rpieasy2/")

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def get_task(self, task_index: int) -> dict[str, Any] | None:
        tasks = self.data.get("tasks", [])
        if 0 <= task_index < len(tasks):
            return tasks[task_index]
        return None

    def set_task(self, task_index: int, task: dict[str, Any]) -> None:
        tasks = self.data.get("tasks", [])
        while len(tasks) <= task_index:
            tasks.append({})
        tasks[task_index] = task

    def get_controller(self, idx: int) -> dict[str, Any] | None:
        ctrls = self.data.get("controllers", [])
        if 0 <= idx < len(ctrls):
            return ctrls[idx]
        return None

    def set_controller(self, idx: int, ctrl: dict[str, Any]) -> None:
        ctrls = self.data.get("controllers", [])
        while len(ctrls) <= idx:
            ctrls.append({})
        ctrls[idx] = ctrl

    def get_notification(self, idx: int) -> dict[str, Any] | None:
        notifs = self.data.get("notifications", [])
        if 0 <= idx < len(notifs):
            return notifs[idx]
        return None

    def set_notification(self, idx: int, notif: dict[str, Any]) -> None:
        notifs = self.data.get("notifications", [])
        while len(notifs) <= idx:
            notifs.append({})
        notifs[idx] = notif

    def _rules_dir(self) -> Path:
        return self.path.parent

    def get_rules(self) -> list[dict[str, Any]]:
        rules_dir = self._rules_dir()
        result = []
        for i in range(1, 5):
            f = rules_dir / f"rules{i}.txt"
            text = f.read_text(encoding="utf-8") if f.exists() else ""
            result.append({"name": f"Rules Set {i}", "rules": text})
        return result

    def set_rules(self, rules: list[dict[str, Any]]) -> None:
        rules_dir = self._rules_dir()
        rules_dir.mkdir(parents=True, exist_ok=True)
        for i in range(1, 5):
            f = rules_dir / f"rules{i}.txt"
            text = rules[i - 1].get("rules", "") if i - 1 < len(rules) else ""
            f.write_text(text, encoding="utf-8")
        self.data.pop("rules", None)


def get_config() -> RpEasyConfig:
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = RpEasyConfig()
        _CONFIG.load()
    return _CONFIG


def reset_config() -> None:
    global _CONFIG
    _CONFIG = None


def factory_reset() -> None:
    cfg = get_config()
    cfg.data = cfg._default()
    cfg.save()
    rules_dir = cfg._rules_dir()
    for i in range(1, 5):
        f = rules_dir / f"rules{i}.txt"
        if f.exists():
            f.write_text("", encoding="utf-8")


def set_system_config(key: str, value: Any) -> None:
    cfg = get_config()
    cfg.data.setdefault("system", {})[key] = value
    cfg.save()


def get_system_config(key: str, default: Any = None) -> Any:
    return get_config().data.get("system", {}).get(key, default)

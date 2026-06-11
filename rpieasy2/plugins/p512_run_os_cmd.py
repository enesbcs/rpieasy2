from __future__ import annotations

import asyncio
import logging
from typing import Any

from rpieasy2.core.device_properties import DeviceProperties
from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_DUMMY, SENSOR_TYPE_SWITCH
from rpieasy2.core.system_vars import LIVE_TASK_VALUES, resolve_controller_template

logger = logging.getLogger("rpieasy2.plugin.p512")


class P512RunOSCmd(PluginBase):
    PLUGIN_ID = 512
    PLUGIN_NAME = "Generic - Run OS Command"
    PLUGIN_VALUES = 1
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_DUMMY,
        vtype=SENSOR_TYPE_SWITCH,
        value_count=1,
        inverse_logic_option=True,
        send_data_option=True,
        timer_optional=True,
    )

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._state: int = 0
        self._trigger_enabled: bool = False
        self._prev_trigger_val: int = -1

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._state = 0
        self._prev_trigger_val = -1
        ref = self._config.get("trigger_ref", "_")
        self._trigger_enabled = ref not in ("_", "0", "")
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        event.data["values"] = {"State": str(self._state)}
        event.data["named_values"] = {"State": str(self._state)}
        event.data["value_names"] = ["State"]
        return True

    async def on_plugin_write(self, event: Event) -> bool | None:
        sv = event.data.get("values", {})
        if sv:
            raw = next(iter(sv.values()), "0")
        else:
            raw = event.string1 or "0"
        if str(raw).lower() in ("on", "1"):
            val = 1
        else:
            val = 0
        await self._run_command(val)
        return True

    async def _run_command(self, number: int) -> None:
        if not self._config.get("enabled", True):
            return
        inverted = self._config.get("inversed", False)
        cmd_idx = 1 - number if inverted else number
        cmd_key = f"cmd{cmd_idx}"
        cmdline = self._config.get(cmd_key, "")
        if not cmdline or cmdline == "0":
            return
        if self._config.get("enable_parsing", False):
            cmdline = resolve_controller_template(
                cmdline,
                task_index=self._task_index,
                task_config=self._config,
            )
        use_threading = self._config.get("use_threading", False)
        logger.info("OS command: %s", cmdline)
        if use_threading:
            asyncio.create_task(self._run_process(cmdline))
            logger.info("OS command started in background")
        else:
            output = await self._run_process(cmdline)
            output = output.strip()
            if output:
                logger.info("OS command output: %s", output)
            else:
                logger.info("OS command executed successfully")

    @staticmethod
    async def _run_process(cmdline: str) -> str:
        try:
            proc = await asyncio.create_subprocess_shell(
                cmdline,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                logger.warning("OS command returned code %d", proc.returncode)
            return stdout.decode(errors="replace") if stdout else ""
        except Exception as e:
            logger.error("OS command failed: %s", e)
            return ""

    async def on_plugin_ten_per_second(self, event: Event) -> bool | None:
        if not self._trigger_enabled:
            return None
        ref = self._config.get("trigger_ref", "_")
        if ref in ("_", "0", ""):
            self._trigger_enabled = False
            return None
        try:
            parts = ref.split("_")
            tasknum = int(parts[0])
            valnum = int(parts[1])
            tv = LIVE_TASK_VALUES.get(tasknum, {})
            values_list = list(tv.values())
            if valnum < len(values_list):
                aval = int(float(values_list[valnum]))
            else:
                return None
        except (ValueError, IndexError, TypeError):
            return None
        if aval != self._prev_trigger_val:
            try:
                low = int(self._config.get("trigger_low", 0))
            except (ValueError, TypeError):
                low = 0
            try:
                high = int(self._config.get("trigger_high", 1))
            except (ValueError, TypeError):
                high = 1
            if aval <= low:
                await self._run_command(0)
            elif aval >= high:
                await self._run_command(1)
            self._prev_trigger_val = aval
        return None

    async def on_plugin_get_device_value_names(self, event: Event) -> bool | None:
        event.data["value_names"] = ["State"]
        return True

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        options = [{"value": "_", "label": "None"}]
        for ti in LIVE_TASK_VALUES:
            tv = LIVE_TASK_VALUES[ti]
            for vi, vname in enumerate(tv.keys()):
                label = f"T{ti + 1}-{vi + 1} / Task{ti + 1}-{vname}"
                options.append({"value": f"{ti}_{vi}", "label": label})
        current_ref = self._config.get("trigger_ref", "_")
        found = any(o["value"] == current_ref for o in options)
        if not found and current_ref not in ("_", "0", ""):
            options.append({"value": current_ref, "label": f"Current: {current_ref}"})
        event.data["form"] = [
            {"name": "cmd0", "label": "Command 0", "type": "text",
             "value": self._config.get("cmd0", ""),
             "placeholder": "Command to execute for OFF state"},
            {"name": "cmd1", "label": "Command 1", "type": "text",
             "value": self._config.get("cmd1", ""),
             "placeholder": "Command to execute for ON state"},
            {"name": "use_threading", "label": "Run in background (no wait for result)",
             "type": "checkbox", "value": self._config.get("use_threading", False)},
            {"name": "enable_parsing", "label": "Enable parsing command line before execute",
             "type": "checkbox", "value": self._config.get("enable_parsing", False)},
            {"name": "trigger_ref", "label": "Trigger variable", "type": "select",
             "value": current_ref, "options": options},
            {"name": "trigger_low", "label": "Trigger Low value", "type": "number",
             "value": self._config.get("trigger_low", 0), "min": -65535, "max": 65535},
            {"name": "trigger_high", "label": "Trigger High value", "type": "number",
             "value": self._config.get("trigger_high", 1), "min": -65535, "max": 65535},
        ]
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        fd = event.data.get("form_data", {})
        cmd0 = str(fd.get("cmd0", "")).strip()
        cmd1 = str(fd.get("cmd1", "")).strip()
        self._config["cmd0"] = "" if cmd0 == "0" else cmd0
        self._config["cmd1"] = "" if cmd1 == "0" else cmd1
        self._config["use_threading"] = fd.get("use_threading") in (True, "on", "1", 1)
        self._config["enable_parsing"] = fd.get("enable_parsing") in (True, "on", "1", 1)
        self._config["trigger_ref"] = str(fd.get("trigger_ref", "_"))
        try:
            self._config["trigger_low"] = int(fd.get("trigger_low", 0))
        except (ValueError, TypeError):
            self._config["trigger_low"] = 0
        try:
            self._config["trigger_high"] = int(fd.get("trigger_high", 1))
        except (ValueError, TypeError):
            self._config["trigger_high"] = 1
        ref = self._config.get("trigger_ref", "_")
        self._trigger_enabled = ref not in ("_", "0", "")
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config["cmd0"] = ""
        self._config["cmd1"] = ""
        self._config["use_threading"] = False
        self._config["enable_parsing"] = False
        self._config["trigger_ref"] = "_"
        self._config["trigger_low"] = 0
        self._config["trigger_high"] = 1
        return True

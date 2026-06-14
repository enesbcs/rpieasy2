from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from typing import Any

from rpieasy2.core.events import Event, get_event_bus
from rpieasy2.core.rpiconst import SENSOR_TYPE_TO_VALUE_COUNT
from rpieasy2.core.system_vars import LIVE_TASK_VALUES, resolve_system_var, resolve_taskval, VAR_PATTERN, TASKVAL_PATTERN

logger = logging.getLogger("rpieasy2.rules")

ESPEASY_RULES_FLOAT_TYPE = float


class RuleParseError(Exception):
    pass


class RuleCommand:
    def __init__(self, raw: str):
        self.raw = raw.strip()
        self.name = ""
        self.args: list[str] = []
        self._parse()

    def _parse(self) -> None:
        if not self.raw:
            return
        text = self.raw
        if " " in text and "," in text:
            fsp = text.find(" ")
            fco = text.find(",")
            if fsp < fco:
                self.name = text[:fsp].strip().lower()
                rest = text[fsp + 1 :].strip()
                self.args = [p.strip() for p in self._split_args(rest)]
                return
        parts = self._split_args(text)
        if parts:
            self.name = parts[0].strip().lower()
            self.args = [p.strip() for p in parts[1:]]

    @staticmethod
    def _split_args(text: str) -> list[str]:
        args: list[str] = []
        current: list[str] = []
        in_quote = False
        quote_char = ""
        for ch in text:
            if in_quote:
                if ch == quote_char:
                    in_quote = False
                else:
                    current.append(ch)
            elif ch in ('"', "'"):
                in_quote = True
                quote_char = ch
            elif ch == ",":
                args.append("".join(current).strip())
                current = []
            else:
                current.append(ch)
        if current:
            args.append("".join(current).strip())
        return args


class Condition:
    def __init__(self, raw: str):
        self.raw = raw.strip()
        self.left: str = ""
        self.operator: str = ""
        self.right: str = ""
        self._parse()

    def _parse(self) -> None:
        op_pattern = re.compile(r"(>=|<=|!=|==|=|<|>)")
        m = op_pattern.search(self.raw)
        if m:
            self.operator = m.group(1)
            self.left = self.raw[:m.start()].strip()
            self.right = self.raw[m.end():].strip()
            if self.operator == "=":
                self.operator = "=="

    def evaluate(self, context: dict[str, Any]) -> bool:
        if not self.operator:
            return False
        left_val = self._resolve(self.left, context)
        right_val = self._resolve(self.right, context)
        logger.debug("Condition.evaluate: %s %s %s -> left=%s right=%s",
                     self.left, self.operator, self.right, left_val, right_val)
        try:
            lf = float(left_val)
            rf = float(right_val)
            if self.operator == ">=":
                return lf >= rf
            if self.operator == "<=":
                return lf <= rf
            if self.operator == "!=":
                return lf != rf
            if self.operator == "==":
                return lf == rf
            if self.operator == ">":
                return lf > rf
            if self.operator == "<":
                return lf < rf
        except (ValueError, TypeError):
            pass
        if self.operator == "==":
            return str(left_val) == str(right_val)
        if self.operator == "!=":
            return str(left_val) != str(right_val)
        return False

    @staticmethod
    def _resolve(val: str, context: dict[str, Any]) -> str:
        val = val.strip()
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1]
            if "#" in inner:
                task, vname = inner.split("#", 1)
                task = task.strip().lower()
                vname = vname.strip().lower()
                tv = context.get("task_values", {})
                for tname, values in tv.items():
                    if tname.lower() == task:
                        for vn, vv in values.items():
                            if vn.lower() == vname:
                                result = str(vv)
                                logger.debug("_resolve: [%s#%s] -> %s (from tv=%s)", task, vname, result, tv)
                                return result
                logger.debug("_resolve: [%s#%s] NOT FOUND in task_values=%s", task, vname, tv)
            return "0"
        if val.startswith("%") and val.endswith("%"):
            var = val[1:-1].lower()
            return str(context.get("vars", {}).get(var, "0"))
        if val.startswith("$"):
            svar = val[1:]
            return str(context.get("str_vars", {}).get(svar, ""))
        return val


class RuleBlock:
    def __init__(self, trigger: str):
        self.trigger = trigger.strip()
        self.if_blocks: list[IfBlock] = []

    def matches(self, event_type: str, event_data: dict[str, Any]) -> bool:
        trig = self.trigger.lower()
        if trig == event_type.lower():
            return True
        if "#" in trig:
            parts = trig.split("#", 1)
            trig_task = parts[0].lower()
            trig_val = parts[1].lower()
            expected_val: str | None = None
            if "=" in trig_val:
                vparts = trig_val.split("=", 1)
                trig_val = vparts[0].strip()
                expected_val = vparts[1].strip()
            for tname, values in event_data.get("task_values", {}).items():
                if tname.lower() == trig_task:
                    for vn, vv in values.items():
                        if vn.lower() == trig_val:
                            if expected_val is None:
                                return True
                            return str(vv) == expected_val
        if trig.startswith("clock#time="):
            try:
                expected = trig.split("=", 1)[1].strip()
                now = time.strftime("%H:%M")
                if now == expected:
                    return True
            except Exception:
                pass
        return False


class IfBlock:
    def __init__(self):
        self.condition: Condition | None = None
        self.commands: list[RuleCommand] = []
        self.elseif_blocks: list[IfBlock] = []
        self.else_block: IfBlock | None = None


NOTE_FREQUENCIES: dict[str, float] = {
    "c": 261.63, "c#": 277.18, "db": 277.18, "d": 293.66,
    "d#": 311.13, "eb": 311.13, "e": 329.63, "f": 349.23,
    "f#": 369.99, "gb": 369.99, "g": 392.00, "g#": 415.30,
    "ab": 415.30, "a": 440.00, "a#": 466.16, "bb": 466.16,
    "b": 493.88,
}


def _parse_rtttl(rtttl: str) -> list[tuple[float, float]]:
    result: list[tuple[float, float]] = []
    try:
        name_part, rest = rtttl.split(":", 1)
        defaults_part, notes_part = rest.split(":", 1)
    except ValueError:
        return result
    defaults: dict[str, int] = {"d": 4, "o": 5, "b": 120}
    for part in defaults_part.split(","):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            try:
                defaults[k.strip()] = int(v.strip())
            except (ValueError, KeyError):
                pass
    bpm = defaults.get("b", 120)
    default_dur = defaults.get("d", 4)
    default_oct = defaults.get("o", 5)
    whole_note_ms = (60000.0 / bpm) * 4
    for token in notes_part.split(","):
        token = token.strip()
        if not token:
            continue
        note_text = token
        dotted = False
        if note_text.endswith("."):
            dotted = True
            note_text = note_text[:-1]
        dur_str = ""
        i = 0
        while i < len(note_text) and note_text[i].isdigit():
            dur_str += note_text[i]
            i += 1
        note_dur = int(dur_str) if dur_str else default_dur
        note_body = note_text[i:]
        oct_str = ""
        j = 0
        while j < len(note_body) and note_body[j].isdigit():
            oct_str += note_body[j]
            j += 1
        note_name = note_body.lower()
        if j > 0:
            note_name = note_body[:j].lower()
        else:
            octave_match = False
            for k, ch in enumerate(note_body):
                if ch.isdigit():
                    note_name = note_body[:k].lower()
                    oct_str = note_body[k:]
                    octave_match = True
                    break
            if not octave_match:
                note_name = note_body.lower()
        pitch_class = note_name
        octave = int(oct_str) if oct_str else default_oct
        duration_ms = whole_note_ms / note_dur
        if dotted:
            duration_ms *= 1.5
        if pitch_class in NOTE_FREQUENCIES:
            freq = NOTE_FREQUENCIES[pitch_class] * (2 ** (octave - 4))
            result.append((freq, duration_ms / 1000.0))
        elif pitch_class == "p":
            result.append((0, duration_ms / 1000.0))
    return result


class RuleSet:
    def __init__(self, name: str = ""):
        self.name = name
        self.rules: list[RuleBlock] = []


class RulesEngine:
    def __init__(self):
        self._rule_sets: list[RuleSet] = []
        self._vars: dict[str, float] = {}
        self._str_vars: dict[str, str] = {}
        self._event_bus = get_event_bus()
        self._task_values: dict[int, dict[str, str]] = {}
        self._task_names: dict[int, str] = {}
        self._task_configs: dict[int, dict[str, Any]] = {}
        self._timers: dict[int, float] = {}
        self._loop_timers: dict[int, float] = {}
        self._paused_timers: dict[int, float] = {}
        self._monitored_pins: dict[int, int] = {}
        self._enabled = False
        self._hw: Any = None
        self._plugin_info: dict[int, Any] = {}
        self._running = False
        self._task_enabled: dict[int, bool] = {}
        self._runat_tasks: dict[str, asyncio.Task] = {}

    def set_plugin_info(self, info: dict[int, Any]) -> None:
        self._plugin_info = info

    def set_task_configs(self, configs: dict[int, dict[str, Any]]) -> None:
        self._task_configs = configs
        names: dict[int, str] = {}
        for ti, tc in configs.items():
            n = tc.get("TDN", tc.get("name", f"Task{ti}"))
            names[ti] = n
            enabled = tc.get("TDE", tc.get("enabled", True))
            if isinstance(enabled, str):
                enabled = enabled.lower() in ("true", "1", "yes")
            self._task_enabled[ti] = bool(enabled)
            logger.debug("set_task_configs: ti=%s name=%s pin=%s config_keys=%s",
                         ti, n, tc.get("pin", "N/A"), list(tc.keys()))
        self._task_names = names

    def update_task_value(self, task_index: int, values: dict[str, str]) -> None:
        self._task_values[task_index] = values

    def _resolve_system_var(self, name: str) -> str:
        result = resolve_system_var(name)
        if result:
            return result
        n = name.lower()
        if n in self._vars:
            return str(self._vars[n])
        return ""

    def _resolve_template(self, text: str) -> str:
        def _replace_var(m: re.Match) -> str:
            return self._resolve_system_var(m.group(1))
        result = VAR_PATTERN.sub(_replace_var, text)
        result = resolve_taskval(result, self._task_values, self._task_names)
        return result

    def load_rules(self, rule_sets: list[dict[str, Any]]) -> None:
        self._rule_sets = []
        for rs_data in rule_sets:
            rs = RuleSet(rs_data.get("name", ""))
            text = rs_data.get("rules", "")
            self._parse_rules(text, rs)
            self._rule_sets.append(rs)

    def _parse_rules(self, text: str, rule_set: RuleSet) -> None:
        lines = text.split("\n")
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            i += 1
            if not line or line.startswith("//"):
                continue
            if line.lower().startswith("on ") and line.lower().endswith(" do"):
                trigger = line[3:-3].strip()
                block = RuleBlock(trigger)
                i = self._parse_block(lines, i, block)
                rule_set.rules.append(block)

    def _parse_block(self, lines: list[str], start: int, block: RuleBlock) -> int:
        i = start
        current_if: IfBlock | None = None
        in_else = False
        top_cmds: list[RuleCommand] = []
        while i < len(lines):
            line = lines[i].strip()
            i += 1
            if not line or line.startswith("//"):
                continue
            if line.lower() == "endon":
                if top_cmds:
                    ifb = IfBlock()
                    ifb.commands = top_cmds
                    block.if_blocks.insert(0, ifb)
                return i
            if line.lower().startswith("if "):
                in_else = False
                cond_text = line[3:].strip()
                ifb = IfBlock()
                ifb.condition = Condition(cond_text)
                block.if_blocks.append(ifb)
                current_if = ifb
                i = self._parse_commands(lines, i, current_if.commands)
            elif line.lower().startswith("elseif "):
                in_else = False
                if current_if is not None and block.if_blocks:
                    cond_text = line[7:].strip()
                    elifb = IfBlock()
                    elifb.condition = Condition(cond_text)
                    block.if_blocks[-1].elseif_blocks.append(elifb)
                    current_if = elifb
                    i = self._parse_commands(lines, i, current_if.commands)
            elif line.lower() == "else":
                in_else = True
                if block.if_blocks:
                    elb = IfBlock()
                    block.if_blocks[-1].else_block = elb
                    current_if = elb
                    i = self._parse_commands(lines, i, current_if.commands)
            elif line.lower() == "endif":
                in_else = False
                current_if = None
            elif current_if is not None:
                current_if.commands.append(RuleCommand(line))
            else:
                top_cmds.append(RuleCommand(line))
        if top_cmds:
            ifb = IfBlock()
            ifb.commands = top_cmds
            block.if_blocks.insert(0, ifb)
        return i

    def _parse_commands(self, lines: list[str], start: int, cmd_list: list[RuleCommand]) -> int:
        i = start
        while i < len(lines):
            line = lines[i].strip()
            i += 1
            if not line or line.startswith("//"):
                continue
            low = line.lower()
            if low.startswith("if ") or low.startswith("elseif "):
                i -= 1
                return i
            if low in ("endif", "else", "endon"):
                i -= 1
                return i
            cmd_list.append(RuleCommand(line))
        return i

    async def fire_event(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        if not self._enabled:
            return
        ctx: dict[str, Any] = {
            "task_values": {},
            "vars": self._vars,
            "str_vars": self._str_vars,
        }
        for ti, vd in self._task_values.items():
            tname = self._task_names.get(ti, f"Task{ti}")
            ctx["task_values"][tname] = vd
        if data:
            ctx.update(data)

        logger.debug("fire_event: type=%s task_values=%s", event_type, ctx.get("task_values", {}))

        for rs in self._rule_sets:
            for block in rs.rules:
                if block.matches(event_type, ctx):
                    await self._execute_block(block, ctx)

    async def _execute_block(self, block: RuleBlock, context: dict[str, Any]) -> None:
        for ifb in block.if_blocks:
            executed = await self._execute_if(ifb, context)
            if executed:
                break

    async def _execute_if(self, ifb: IfBlock, context: dict[str, Any]) -> bool:
        if ifb.condition is not None and not ifb.condition.evaluate(context):
            for elifb in ifb.elseif_blocks:
                if elifb.condition is not None and elifb.condition.evaluate(context):
                    await self._execute_commands(elifb.commands, context)
                    return True
            if ifb.else_block is not None:
                await self._execute_commands(ifb.else_block.commands, context)
                return True
            return False
        if ifb.condition is None:
            await self._execute_commands(ifb.commands, context)
            return True
        await self._execute_commands(ifb.commands, context)
        return True

    async def _execute_commands(self, commands: list[RuleCommand], context: dict[str, Any]) -> None:
        for cmd in commands:
            try:
                await self._execute_command(cmd, context)
            except Exception as e:
                logger.error(f"Rule command failed: {cmd.raw}: {e}")

    def _update_task_value_for_pin(self, pin: int, state: int) -> None:
        for ti, vd in self._task_values.items():
            tc = self._task_configs.get(ti, {})
            tc_pin = tc.get("pin", tc.get("pins", ""))
            if str(tc_pin).strip() == str(pin).strip():
                for vn in vd:
                    self._task_values[ti][vn] = str(state)
                LIVE_TASK_VALUES[ti] = {k: str(v) for k, v in self._task_values[ti].items()}
                from rpieasy2.core.webserver import _last_task_values as _ltv
                _ltv[ti] = {k: str(v) for k, v in self._task_values[ti].items()}

    async def _execute_command(self, cmd: RuleCommand, context: dict[str, Any]) -> None:
        name = cmd.name
        args = [self._resolve_template(a) for a in cmd.args]

        if name == "event":
            if args:
                ev_name = args[0]
                ev_data: dict[str, Any] = {}
                if len(args) > 1:
                    ev_data["values"] = args[1:]
                await self._event_bus.publish(Event(
                    type="PLUGIN_WRITE", string1=ev_name, data=ev_data))
                await self.fire_event(ev_name)

        elif name == "asyncevent":
            if args:
                ev_name = args[0]
                ev_data: dict[str, Any] = {}
                if len(args) > 1:
                    ev_data["values"] = args[1:]
                asyncio.ensure_future(self.fire_event(ev_name))

        elif name in ("taskvalueset", "taskvaluesetandrun"):
            if len(args) >= 3:
                task_ident = args[0]
                val_nr = int(args[1]) - 1
                value = args[2]
                target_ti: int | None = None
                try:
                    target_ti = int(task_ident) - 1
                    if target_ti < 0:
                        target_ti = None
                except ValueError:
                    for ti, tname in self._task_names.items():
                        if tname.lower() == task_ident.lower():
                            target_ti = ti
                            break
                if target_ti is not None:
                    vnames: list[str] | None = None
                    if target_ti in self._task_values:
                        vnames = list(self._task_values[target_ti].keys())
                    else:
                        vnames = self._get_value_names(target_ti)
                        if vnames:
                            self._task_values[target_ti] = {vn: "0" for vn in vnames}
                    if vnames and 0 <= val_nr < len(vnames):
                        vk = vnames[val_nr]
                        self._task_values[target_ti][vk] = value
                        ev = Event(type="PLUGIN_WRITE", task_index=target_ti, data={
                            "task_config": self._task_configs.get(target_ti, {}),
                            "values": {vk: value},
                        })
                        await self._event_bus.publish(ev)
                        if name == "taskvaluesetandrun":
                            read_ev = Event(type="PLUGIN_READ", task_index=target_ti, data={
                                "task_config": self._task_configs.get(target_ti, {}),
                                "values": {vk: value},
                            })
                            await self._event_bus.publish(read_ev)

        elif name == "taskvaluetoggle":
            if len(args) >= 2:
                task_ident = args[0]
                val_nr = int(args[1]) - 1
                target_ti: int | None = None
                try:
                    target_ti = int(task_ident) - 1
                except ValueError:
                    for ti, tname in self._task_names.items():
                        if tname.lower() == task_ident.lower():
                            target_ti = ti
                            break
                if target_ti is not None and target_ti in self._task_values:
                    vnames = list(self._task_values[target_ti].keys())
                    if vnames and 0 <= val_nr < len(vnames):
                        vk = vnames[val_nr]
                        cur = self._task_values[target_ti].get(vk, "0")
                        new = "1" if cur in ("0", "", "false", "off") else "0"
                        self._task_values[target_ti][vk] = new

        elif name == "taskvaluesetderived":
            if len(args) >= 3:
                task_ident = args[0]
                val_nr = int(args[1]) - 1
                expr = args[2]
                target_ti: int | None = None
                try:
                    target_ti = int(task_ident) - 1
                except ValueError:
                    for ti, tname in self._task_names.items():
                        if tname.lower() == task_ident.lower():
                            target_ti = ti
                            break
                if target_ti is not None:
                    try:
                        result = self._calculate(expr)
                    except Exception:
                        result = 0
                    vnames: list[str] | None = None
                    if target_ti in self._task_values:
                        vnames = list(self._task_values[target_ti].keys())
                    else:
                        vnames = self._get_value_names(target_ti)
                        if vnames:
                            self._task_values[target_ti] = {vn: "0" for vn in vnames}
                    if vnames and 0 <= val_nr < len(vnames):
                        vk = vnames[val_nr]
                        self._task_values[target_ti][vk] = str(result)

        elif name == "taskvaluesetpresentation":
            if len(args) >= 3:
                task_ident = args[0]
                val_nr = int(args[1]) - 1
                fmt = args[2]
                target_ti: int | None = None
                try:
                    target_ti = int(task_ident) - 1
                except ValueError:
                    for ti, tname in self._task_names.items():
                        if tname.lower() == task_ident.lower():
                            target_ti = ti
                            break
                if target_ti is not None and target_ti in self._task_configs:
                    tc = self._task_configs[target_ti]
                    try:
                        tc[f"TDTV{val_nr + 1}"] = int(fmt)
                    except ValueError:
                        tc[f"TDTV{val_nr + 1}"] = fmt

        elif name == "taskrun":
            if args:
                task_ident = args[0]
                target_ti: int | None = None
                try:
                    target_ti = int(task_ident) - 1
                except ValueError:
                    for ti, tname in self._task_names.items():
                        if tname.lower() == task_ident.lower():
                            target_ti = ti
                            break
                if target_ti is not None:
                    read_ev = Event(type="PLUGIN_READ", task_index=target_ti, data={
                        "task_config": self._task_configs.get(target_ti, {}),
                    })
                    await self._event_bus.publish(read_ev)

        elif name == "taskrunat":
            if len(args) >= 2:
                task_ident = args[0]
                target_ti: int | None = None
                try:
                    target_ti = int(task_ident) - 1
                except ValueError:
                    for ti, tname in self._task_names.items():
                        if tname.lower() == task_ident.lower():
                            target_ti = ti
                            break
                if target_ti is not None:
                    time_str = args[1]
                    task_key = f"runat_{target_ti}"

                    async def _run_at(ti: int, tstr: str) -> None:
                        try:
                            while True:
                                now = time.strftime("%H:%M")
                                if now == tstr:
                                    read_ev = Event(type="PLUGIN_READ", task_index=ti, data={
                                        "task_config": self._task_configs.get(ti, {}),
                                    })
                                    await self._event_bus.publish(read_ev)
                                    return
                                await asyncio.sleep(10)
                        finally:
                            self._runat_tasks.pop(task_key, None)

                    old = self._runat_tasks.get(task_key)
                    if old:
                        old.cancel()
                    self._runat_tasks[task_key] = asyncio.create_task(_run_at(target_ti, time_str))

        elif name == "scheduletaskrun":
            if len(args) >= 2:
                task_ident = args[0]
                seconds = float(args[1])
                target_ti: int | None = None
                try:
                    target_ti = int(task_ident) - 1
                except ValueError:
                    for ti, tname in self._task_names.items():
                        if tname.lower() == task_ident.lower():
                            target_ti = ti
                            break
                if target_ti is not None and seconds > 0:

                    async def _delayed_run(ti: int) -> None:
                        await asyncio.sleep(seconds)
                        read_ev = Event(type="PLUGIN_READ", task_index=ti, data={
                            "task_config": self._task_configs.get(ti, {}),
                        })
                        await self._event_bus.publish(read_ev)

                    asyncio.create_task(_delayed_run(target_ti))

        elif name == "taskclear":
            if args:
                task_ident = args[0]
                target_ti: int | None = None
                try:
                    target_ti = int(task_ident) - 1
                except ValueError:
                    for ti, tname in self._task_names.items():
                        if tname.lower() == task_ident.lower():
                            target_ti = ti
                            break
                if target_ti is not None and target_ti in self._task_values:
                    for vn in self._task_values[target_ti]:
                        self._task_values[target_ti][vn] = "0"

        elif name == "taskclearall":
            for ti in self._task_values:
                for vn in self._task_values[ti]:
                    self._task_values[ti][vn] = "0"

        elif name == "taskdisable":
            if args:
                task_ident = args[0]
                target_ti: int | None = None
                try:
                    target_ti = int(task_ident) - 1
                except ValueError:
                    for ti, tname in self._task_names.items():
                        if tname.lower() == task_ident.lower():
                            target_ti = ti
                            break
                if target_ti is not None:
                    self._task_enabled[target_ti] = False
                    tc = self._task_configs.get(target_ti, {})
                    tc["TDE"] = False

        elif name == "taskenable":
            if args:
                task_ident = args[0]
                target_ti: int | None = None
                try:
                    target_ti = int(task_ident) - 1
                except ValueError:
                    for ti, tname in self._task_names.items():
                        if tname.lower() == task_ident.lower():
                            target_ti = ti
                            break
                if target_ti is not None:
                    self._task_enabled[target_ti] = True
                    tc = self._task_configs.get(target_ti, {})
                    tc["TDE"] = True

        elif name == "config":
            if args and args[0].lower() == "task" and len(args) >= 3:
                task_ident = args[1]
                cmd = ",".join(args[2:])
                ev = Event(type="PLUGIN_WRITE", data={
                    "remote_config": True,
                    "task_name": task_ident,
                    "command": cmd,
                })
                await self._event_bus.publish(ev)
            elif len(args) >= 3:
                task_ident = args[0]
                cfg_key = args[1]
                cfg_val = args[2]
                target_ti: int | None = None
                try:
                    target_ti = int(task_ident) - 1
                except ValueError:
                    for ti, tname in self._task_names.items():
                        if tname.lower() == task_ident.lower():
                            target_ti = ti
                            break
                if target_ti is not None and target_ti in self._task_configs:
                    self._task_configs[target_ti][cfg_key] = cfg_val

        elif name == "let":
            if len(args) >= 2:
                var_name = args[0]
                try:
                    val = ESPEASY_RULES_FLOAT_TYPE(args[1])
                    self._vars[var_name] = val
                except (ValueError, TypeError):
                    try:
                        result = self._calculate(args[1])
                        self._vars[var_name] = result
                    except Exception:
                        self._vars[var_name] = 0

        elif name == "letstr":
            if len(args) >= 2:
                self._str_vars[args[0]] = args[1]

        elif name == "inc":
            var_name = args[0] if args else ""
            amount = 1.0
            if len(args) >= 2:
                try:
                    amount = ESPEASY_RULES_FLOAT_TYPE(args[1])
                except (ValueError, TypeError):
                    try:
                        amount = self._calculate(args[1])
                    except Exception:
                        amount = 1.0
            self._vars[var_name] = self._vars.get(var_name, 0) + amount

        elif name == "dec":
            var_name = args[0] if args else ""
            amount = 1.0
            if len(args) >= 2:
                try:
                    amount = ESPEASY_RULES_FLOAT_TYPE(args[1])
                except (ValueError, TypeError):
                    try:
                        amount = self._calculate(args[1])
                    except Exception:
                        amount = 1.0
            self._vars[var_name] = self._vars.get(var_name, 0) - amount

        elif name == "logentry":
            msg = ", ".join(args) if args else ""
            logger.info(f"[RULE] {msg}")

        elif name in ("gpio", "gpio_write"):
            if len(args) >= 2 and self._hw:
                if args[0].lower() == "toggle":
                    pin = int(args[1])
                    try:
                        cur = self._hw.gpio.read(pin)
                        new_state = 1 - cur
                        self._hw.gpio.write(pin, new_state)
                        self._update_task_value_for_pin(pin, new_state)
                    except Exception as e:
                        logger.error(f"GPIO Toggle failed: {e}")
                else:
                    pin = int(args[0])
                    state = int(args[1])
                    logger.debug("gpio command: pin=%s state=%s", pin, state)
                    try:
                        self._hw.gpio.write(pin, state)
                        self._update_task_value_for_pin(pin, state)
                    except Exception as e:
                        logger.error(f"GPIO write failed: {e}")

        elif name in ("gpio_toggle", "gpiotoggle"):
            if args and self._hw:
                pin = int(args[0])
                try:
                    cur = self._hw.gpio.read(pin)
                    new_state = 1 - cur
                    self._hw.gpio.write(pin, new_state)
                    self._update_task_value_for_pin(pin, new_state)
                except Exception as e:
                    logger.error(f"GPIO Toggle failed: {e}")

        elif name in ("gpio_longpulse", "longpulse"):
            if len(args) >= 3 and self._hw:
                pin = int(args[0])
                state = int(args[1])
                duration_high = float(args[2])
                duration_low = float(args[3]) if len(args) > 3 else 0
                repeat = int(args[4]) if len(args) > 4 else 0

                async def _longpulse(p: int, s: int, dh: float, dl: float, r: int) -> None:
                    try:
                        if dl == 0 or r == 0:
                            self._hw.gpio.write(p, s)
                            self._update_task_value_for_pin(p, s)
                            await asyncio.sleep(dh)
                            self._hw.gpio.write(p, 1 - s)
                            self._update_task_value_for_pin(p, 1 - s)
                        else:
                            count = 0
                            while True:
                                self._hw.gpio.write(p, s)
                                self._update_task_value_for_pin(p, s)
                                await asyncio.sleep(dh)
                                self._hw.gpio.write(p, 1 - s)
                                self._update_task_value_for_pin(p, 1 - s)
                                await asyncio.sleep(dl)
                                count += 1
                                if r > 0 and count >= r:
                                    break
                    except Exception as e:
                        logger.error(f"LongPulse failed: {e}")

                asyncio.create_task(_longpulse(pin, state, duration_high, duration_low, repeat))

        elif name in ("gpio_longpulse_ms", "longpulse_ms"):
            if len(args) >= 3 and self._hw:
                pin = int(args[0])
                state = int(args[1])
                duration_ms = float(args[2])

                async def _longpulse_ms(p: int, s: int, d: float) -> None:
                    try:
                        self._hw.gpio.write(p, s)
                        self._update_task_value_for_pin(p, s)
                        await asyncio.sleep(d / 1000.0)
                        self._hw.gpio.write(p, 1 - s)
                        self._update_task_value_for_pin(p, 1 - s)
                    except Exception as e:
                        logger.error(f"LongPulse_ms failed: {e}")

                asyncio.create_task(_longpulse_ms(pin, state, duration_ms))

        elif name in ("gpio_pwm", "pwm"):
            if len(args) >= 2 and self._hw:
                pin = int(args[0])
                duty = float(args[1])
                fade = float(args[2]) if len(args) > 2 else 0
                freq = float(args[3]) if len(args) > 3 else 1000
                try:
                    self._hw.gpio.pwm(pin, freq, duty)
                except Exception as e:
                    logger.error(f"PWM failed: {e}")

        elif name in ("gpio_tone", "tone"):
            if len(args) >= 2 and self._hw:
                pin = int(args[0])
                freq = float(args[1])
                duration = float(args[2]) if len(args) > 2 else None
                try:
                    if duration:
                        self._hw.gpio.tone(pin, freq, duration)
                    else:
                        self._hw.gpio.tone(pin, freq)
                except Exception as e:
                    logger.error(f"Tone failed: {e}")

        elif name in ("gpio_rtttl", "rtttl"):
            if len(args) >= 2 and self._hw:
                pin = int(args[0])
                rtttl_str = ",".join(args[1:])
                notes = _parse_rtttl(rtttl_str)

                async def _play_rtttl() -> None:
                    for freq, dur in notes:
                        if freq > 0:
                            try:
                                self._hw.gpio.tone(pin, freq)
                            except Exception:
                                pass
                        await asyncio.sleep(dur)
                        try:
                            self._hw.gpio.tone_stop(pin)
                        except Exception:
                            pass
                        await asyncio.sleep(0.01)

                asyncio.create_task(_play_rtttl())

        elif name in ("gpio_monitor", "monitor"):
            if args and self._hw:
                pin = int(args[0])
                mode = args[1].lower() if len(args) > 1 else "both"
                edge_map = {"rising": 1, "falling": 2, "both": 3, "r": 1, "f": 2, "b": 3}
                edge = edge_map.get(mode, 3)
                self._monitored_pins[pin] = edge

                def _monitor_cb(gpio: int, level: int, ts: int) -> None:
                    asyncio.ensure_future(self.fire_event(f"GPIO#{gpio}", {"state": level}))

                try:
                    self._hw.gpio.claim_input(pin)
                    self._hw.gpio.watch(pin, edge, _monitor_cb)
                except Exception as e:
                    logger.error(f"Monitor pin {pin} failed: {e}")

        elif name in ("gpio_unmonitor", "unmonitor"):
            if args and self._hw:
                pin = int(args[0])
                self._monitored_pins.pop(pin, None)
                try:
                    self._hw.gpio.unwatch(pin)
                except Exception as e:
                    logger.error(f"UnMonitor pin {pin} failed: {e}")

        elif name in ("gpio_status", "gpio_state"):
            if args and self._hw:
                pin = int(args[0])
                try:
                    val = self._hw.gpio.read(pin)
                    logger.info(f"GPIO Status: pin={pin} value={val}")
                except Exception as e:
                    logger.error(f"GPIO Status failed: {e}")

        elif name in ("gpio_mode", "mode"):
            if len(args) >= 2 and self._hw:
                pin = int(args[0])
                mode_name = args[1].lower()
                try:
                    self._hw.gpio.set_mode(pin, mode_name)
                except Exception as e:
                    logger.error(f"GPIO Mode failed: {e}")

        elif name in ("gpio_moderange", "moderange"):
            if len(args) >= 3 and self._hw:
                first = int(args[0])
                last = int(args[1])
                mode_name = args[2].lower()
                for pin in range(first, last + 1):
                    try:
                        self._hw.gpio.set_mode(pin, mode_name)
                    except Exception as e:
                        logger.error(f"GPIO ModeRange pin {pin} failed: {e}")

        elif name == "timerset":
            if len(args) >= 2:
                timer_nr = int(args[0])
                seconds = float(args[1])
                if seconds <= 0:
                    self._timers.pop(timer_nr, None)
                    self._loop_timers.pop(timer_nr, None)
                else:
                    self._timers[timer_nr] = time.time() + seconds

        elif name == "timerset_ms":
            if len(args) >= 2:
                timer_nr = int(args[0])
                ms = float(args[1])
                if ms <= 0:
                    self._timers.pop(timer_nr, None)
                    self._loop_timers.pop(timer_nr, None)
                else:
                    self._timers[timer_nr] = time.time() + (ms / 1000.0)

        elif name == "looptimerset":
            if len(args) >= 2:
                timer_nr = int(args[0])
                interval = float(args[1])
                if interval <= 0:
                    self._loop_timers.pop(timer_nr, None)
                    self._timers.pop(timer_nr, None)
                else:
                    self._loop_timers[timer_nr] = interval
                    self._timers[timer_nr] = time.time() + interval

        elif name == "looptimerset_ms":
            if len(args) >= 2:
                timer_nr = int(args[0])
                interval_ms = float(args[1])
                if interval_ms <= 0:
                    self._loop_timers.pop(timer_nr, None)
                    self._timers.pop(timer_nr, None)
                else:
                    interval_sec = interval_ms / 1000.0
                    self._loop_timers[timer_nr] = interval_sec
                    self._timers[timer_nr] = time.time() + interval_sec

        elif name == "looptimersetandrun":
            if len(args) >= 2:
                timer_nr = int(args[0])
                interval = float(args[1])
                if interval <= 0:
                    self._loop_timers.pop(timer_nr, None)
                    self._timers.pop(timer_nr, None)
                else:
                    self._loop_timers[timer_nr] = interval
                    self._timers[timer_nr] = time.time()
                    asyncio.ensure_future(self.fire_event(f"Timer#{timer_nr}"))

        elif name == "looptimersetandrun_ms":
            if len(args) >= 2:
                timer_nr = int(args[0])
                interval_ms = float(args[1])
                if interval_ms <= 0:
                    self._loop_timers.pop(timer_nr, None)
                    self._timers.pop(timer_nr, None)
                else:
                    interval_sec = interval_ms / 1000.0
                    self._loop_timers[timer_nr] = interval_sec
                    self._timers[timer_nr] = time.time()
                    asyncio.ensure_future(self.fire_event(f"Timer#{timer_nr}"))

        elif name == "timerpause":
            if args:
                timer_nr = int(args[0])
                if timer_nr in self._timers:
                    self._paused_timers[timer_nr] = self._timers[timer_nr]

        elif name == "timerresume":
            if args:
                timer_nr = int(args[0])
                self._paused_timers.pop(timer_nr, None)

        elif name == "delay":
            if args:
                ms = float(args[0])
                await asyncio.sleep(ms / 1000.0)

        elif name == "sendtohttp":
            if len(args) >= 3:
                host = args[0]
                port = args[1]
                path = args[2] if len(args) > 2 else "/"
                import aiohttp
                try:
                    async with aiohttp.ClientSession() as session:
                        url = f"http://{host}:{port}{path}"
                        await session.get(url, timeout=aiohttp.ClientTimeout(total=10))
                except Exception as e:
                    logger.error(f"SendToHTTP failed: {e}")

        elif name == "sendtoudp":
            if len(args) >= 3:
                host = args[0]
                port = int(args[1])
                message = args[2]
                ev = Event(type="CONTROLLER_SEND_UDP", data={
                    "host": host, "port": port, "message": message,
                })
                await self._event_bus.publish(ev)

        elif name == "sendto":
            if len(args) >= 2:
                dest_unit = int(args[0])
                event_name = ",".join(args[1:])
                try:
                    from rpieasy2.core.p2p_service import get_discovered_nodes, p2p_sendto
                    nodes = get_discovered_nodes()
                    node = nodes.get(dest_unit)
                    if node and node.get("ip"):
                        ev = Event(type="CONTROLLER_SEND_UDP", data={
                            "host": node["ip"], "port": 0,
                            "message": event_name,
                        })
                        await self._event_bus.publish(ev)
                        payload = event_name.encode()
                        p2p_sendto(payload, node["ip"])
                    else:
                        logger.warning(f"sendto: unit {dest_unit} not found in P2P nodes")
                except Exception as e:
                    logger.error(f"sendto failed: {e}")

        elif name == "publish":
            if len(args) >= 2:
                topic = args[0]
                message = args[1]
                ev = Event(type="CONTROLLER_SEND", data={
                    "send_to_mqtt": True, "topic": topic, "message": message,
                })
                await self._event_bus.publish(ev)

        elif name == "publishr":
            if len(args) >= 2:
                topic = args[0]
                message = args[1]
                ev = Event(type="CONTROLLER_SEND", data={
                    "send_to_mqtt": True, "topic": topic, "message": message, "retain": True,
                })
                await self._event_bus.publish(ev)

        elif name == "subscribe":
            if args:
                topic = args[0]
                ev = Event(type="CONTROLLER_SUBSCRIBE", data={"topic": topic})
                await self._event_bus.publish(ev)

        elif name == "notify":
            if len(args) >= 2:
                idx = int(args[0])
                message = ", ".join(args[1:])
                ev = Event(type="NOTIFIER_SEND", notifier_index=idx, data={
                    "message": message,
                })
                await self._event_bus.publish(ev)

        elif name == "servo":
            if len(args) >= 3 and self._hw:
                servo_id = int(args[0])
                pin = int(args[1])
                angle = int(args[2])
                try:
                    if angle >= 9000:
                        self._hw.gpio.servo_detach(pin)
                    else:
                        self._hw.gpio.claim_output(pin)
                        self._hw.gpio.servo(pin, angle)
                except Exception as e:
                    logger.error(f"Servo failed: {e}")

        elif name == "reboot":
            logger.info("Rules triggered reboot")
            ev = Event(type="PLUGIN_EXIT", data={})
            await self._event_bus.publish(ev)
            import sys as _sys
            _sys.stdout.flush()
            import os as _os
            _os.execv(_sys.executable, [_sys.executable, "-m", "rpieasy2"])

        elif name == "restart":
            import sys as _sys
            pkg_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            base_dir = os.path.dirname(pkg_dir)
            main_py = os.path.join(base_dir, "main.py")
            _sys.stdout.flush()
            os.execv(_sys.executable, [_sys.executable, main_py])

        elif name == "pulse":
            if len(args) >= 3 and self._hw:
                pin = int(args[0])
                state = int(args[1])
                duration = int(args[2])

                async def _pulse():
                    try:
                        self._hw.gpio.write(pin, state)
                        self._update_task_value_for_pin(pin, state)
                        await asyncio.sleep(duration / 1000.0)
                        self._hw.gpio.write(pin, 1 - state)
                        self._update_task_value_for_pin(pin, 1 - state)
                    except Exception as e:
                        logger.error(f"Pulse failed: {e}")

                asyncio.create_task(_pulse())

        elif name == "play":
            if args and self._hw:
                freq = int(args[0])
                duration = int(args[1]) if len(args) > 1 else 100

                async def _play():
                    try:
                        self._hw.gpio.write(4, 1)
                        self._update_task_value_for_pin(4, 1)
                        await asyncio.sleep(duration / 1000.0)
                        self._hw.gpio.write(4, 0)
                        self._update_task_value_for_pin(4, 0)
                    except Exception as e:
                        logger.error(f"Play failed: {e}")

                asyncio.create_task(_play())

        elif name == "factoryreset":
            from rpieasy2.core.config import factory_reset
            factory_reset()
            logger.info("Factory reset executed")

        elif name == "save":
            from rpieasy2.core.config import get_config
            get_config().save()
            logger.info("Config saved")

        elif name == "load":
            from rpieasy2.core.config import get_config
            get_config().load()
            logger.info("Config reloaded")

        elif name == "name":
            if args:
                from rpieasy2.core.config import set_system_config
                set_system_config("name", args[0])
                logger.info(f"Device name set to: {args[0]}")

        elif name == "unit":
            if args:
                from rpieasy2.core.config import set_system_config
                try:
                    set_system_config("unit", int(args[0]))
                    logger.info(f"Unit set to: {args[0]}")
                except ValueError:
                    logger.error(f"Invalid unit: {args[0]}")

        elif name == "password":
            if args:
                from rpieasy2.core.config import set_system_config
                set_system_config("password", args[0])
                logger.info("Password set")

        elif name == "clearpassword":
            from rpieasy2.core.config import set_system_config
            set_system_config("password", "")
            logger.info("Password cleared")

        elif name == "build":
            from rpieasy2.core.rpiconst import BUILD, build_to_date_str
            logger.info(f"Build: {BUILD} ({build_to_date_str(BUILD)})")

        elif name == "settings":
            from rpieasy2.core.config import get_config
            cfg = get_config()
            sys_cfg = cfg.data.get("system", {})
            logger.info(f"Settings: name={sys_cfg.get('name')} unit={sys_cfg.get('unit')} "
                        f"build={sys_cfg.get('build')} rules_enabled={sys_cfg.get('enable_rules')}")

        elif name == "status":
            from rpieasy2.core.rpiconst import BUILD, build_to_date_str
            import platform
            logger.info(f"Status: Build={BUILD} ({build_to_date_str(BUILD)}) "
                        f"Platform={platform.platform()}")

        elif name == "executerules":
            from rpieasy2.core.config import get_config
            cfg = get_config()
            self.load_rules(cfg.get_rules())
            logger.info("Rules reloaded")

        elif name in ("datetime", "date"):
            if args:
                dt_str = args[0]
                try:
                    proc = await asyncio.create_subprocess_exec("date", "-s", dt_str)
                    await asyncio.wait_for(proc.wait(), timeout=5)
                    logger.info(f"Date/time set to: {dt_str}")
                except Exception as e:
                    logger.error(f"Failed to set datetime: {e}")

        elif name == "dst":
            if args:
                from rpieasy2.core.config import set_system_config
                set_system_config("dst", args[0])
                logger.info(f"DST set to: {args[0]}")

        elif name == "timezone":
            if args:
                try:
                    proc = await asyncio.create_subprocess_exec("timedatectl", "set-timezone", args[0])
                    await asyncio.wait_for(proc.wait(), timeout=5)
                    from rpieasy2.core.config import set_system_config
                    set_system_config("timezone", args[0])
                    logger.info(f"Timezone set to: {args[0]}")
                except Exception as e:
                    logger.error(f"Failed to set timezone: {e}")

        elif name == "i2cscanner":
            try:
                from rpieasy2.core.hw import has_native_hw, is_stub_i2c
                if has_native_hw():
                    from rpieasy2.core.hw import scan_i2c_bus
                    found = await scan_i2c_bus()
                elif self._hw and hasattr(self._hw, "i2c") and not is_stub_i2c(self._hw.i2c):
                    found = await self._hw.i2c.scan()
                else:
                    logger.error("I2C Scanner: no I2C backend configured (set MPSSE channel to I2C on Hardware page)")
                    return
                if found:
                    hex_found = [hex(a) for a in found]
                    logger.info(f"I2C Scanner: devices found at {', '.join(hex_found)}")
                else:
                    logger.info("I2C Scanner: no devices found")
            except ImportError:
                logger.error("I2C Scanner: smbus2 not installed")
            except PermissionError:
                logger.error("I2C Scanner: permission denied (add user to i2c group)")
            except Exception as e:
                logger.error(f"I2C Scanner failed: {e}")

        elif name in ("posttohttp", "posttohttps"):
            if len(args) >= 3:
                host = args[0]
                port = int(args[1])
                path = args[2]
                header = args[3] if len(args) > 3 else ""
                body = args[4] if len(args) > 4 else ""
                import aiohttp
                try:
                    is_tls = name == "posttohttps"
                    proto = "https" if is_tls else "http"
                    async with aiohttp.ClientSession() as session:
                        url = f"{proto}://{host}:{port}{path}"
                        headers = {}
                        if header:
                            if ":" in header:
                                k, v = header.split(":", 1)
                                headers[k.strip()] = v.strip()
                        if body:
                            await session.post(url, data=body, headers=headers,
                                               timeout=aiohttp.ClientTimeout(total=10))
                        else:
                            await session.post(url, headers=headers,
                                               timeout=aiohttp.ClientTimeout(total=10))
                except Exception as e:
                    logger.error(f"{name} failed: {e}")

        elif name == "controllerdisable":
            if args:
                ctrl_nr = int(args[0])
                idx = ctrl_nr - 1
                from rpieasy2.core.config import get_config
                cfg = get_config()
                ctrl = cfg.get_controller(idx) or {}
                ctrl["controllerenabled"] = False
                cfg.set_controller(idx, ctrl)
                logger.info(f"Controller {ctrl_nr} disabled")

        elif name == "controllerenable":
            if args:
                ctrl_nr = int(args[0])
                idx = ctrl_nr - 1
                from rpieasy2.core.config import get_config
                cfg = get_config()
                ctrl = cfg.get_controller(idx) or {}
                ctrl["controllerenabled"] = True
                cfg.set_controller(idx, ctrl)
                logger.info(f"Controller {ctrl_nr} enabled")

        elif name == "latitude":
            if args:
                try:
                    lat = float(args[0])
                    self._vars["latitude"] = lat
                    from rpieasy2.core.config import set_system_config
                    set_system_config("latitude", str(lat))
                    logger.info(f"Latitude set to: {lat}")
                except ValueError:
                    logger.error(f"Invalid latitude: {args[0]}")

        elif name == "longitude":
            if args:
                try:
                    lon = float(args[0])
                    self._vars["longitude"] = lon
                    from rpieasy2.core.config import set_system_config
                    set_system_config("longitude", str(lon))
                    logger.info(f"Longitude set to: {lon}")
                except ValueError:
                    logger.error(f"Invalid longitude: {args[0]}")

        elif name == "oledframedcmd":
            # oledframedcmd,<subcommand>,<args>...
            # Dispatches to P036 FrameOLED plugin via PLUGIN_WRITE
            ev = Event(type="PLUGIN_WRITE", data={
                "command": "oledframedcmd",
                "args": args,
            })
            await self._event_bus.publish(ev)

        else:
            logger.warning(f"Unknown command: {name}")
            raise ValueError(f"Unknown command: {name}")

    @staticmethod
    def _calculate(expr: str) -> float:
        expr = expr.strip()
        try:
            return float(expr)
        except ValueError:
            pass
        tokens = re.findall(r"[\d.]+|[+\-*/()]", expr)
        if not tokens:
            return 0
        try:
            return RulesEngine._safe_eval("".join(tokens))
        except Exception:
            return 0

    _SAFE_EVAL_PRIORITY = {"+": 1, "-": 1, "*": 2, "/": 2}
    _SAFE_EVAL_OPERATORS = set("+-*/")

    @staticmethod
    def _safe_eval(expr: str) -> float:
        tokens: list[str] = []
        i = 0
        while i < len(expr):
            ch = expr[i]
            if ch in RulesEngine._SAFE_EVAL_OPERATORS or ch in "()":
                tokens.append(ch)
                i += 1
            else:
                j = i
                while j < len(expr) and (expr[j].isdigit() or expr[j] == "."):
                    j += 1
                tokens.append(expr[i:j])
                i = j
        output: list[float] = []
        ops: list[str] = []
        for t in tokens:
            if t not in RulesEngine._SAFE_EVAL_OPERATORS and t not in "()":
                output.append(float(t))
            elif t == "(":
                ops.append(t)
            elif t == ")":
                while ops and ops[-1] != "(":
                    RulesEngine._apply_op(output, ops.pop())
                ops.pop()
            else:
                while ops and ops[-1] != "(" and RulesEngine._SAFE_EVAL_PRIORITY.get(ops[-1], 0) >= RulesEngine._SAFE_EVAL_PRIORITY.get(t, 0):
                    RulesEngine._apply_op(output, ops.pop())
                ops.append(t)
        while ops:
            RulesEngine._apply_op(output, ops.pop())
        return output[0] if output else 0.0

    @staticmethod
    def _apply_op(output: list[float], op: str) -> None:
        b = output.pop()
        a = output.pop()
        if op == "+":
            output.append(a + b)
        elif op == "-":
            output.append(a - b)
        elif op == "*":
            output.append(a * b)
        elif op == "/":
            if b == 0:
                output.append(0.0)
            else:
                output.append(a / b)

    def set_hw_manager(self, hw: Any) -> None:
        self._hw = hw

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled

    def is_enabled(self) -> bool:
        return self._enabled

    def get_timer(self, nr: int) -> float:
        remaining = self._timers.get(nr, 0) - time.time()
        return max(0, remaining)

    def check_timers(self) -> None:
        now = time.time()
        for nr, expiry in list(self._timers.items()):
            if now >= expiry:
                self._timers.pop(nr, None)
                asyncio.ensure_future(self.fire_event(f"Timer#{nr}"))
        for nr, interval in list(self._loop_timers.items()):
            if nr in self._paused_timers:
                continue
            expiry = self._timers.get(nr)
            if expiry is None or now >= expiry:
                self._timers[nr] = now + interval
                asyncio.ensure_future(self.fire_event(f"Timer#{nr}"))

    def get_vars(self) -> dict[str, float]:
        return dict(self._vars)

    def get_str_vars(self) -> dict[str, str]:
        return dict(self._str_vars)

    def get_task_values_snapshot(self) -> dict[int, dict[str, str]]:
        return dict(self._task_values)

    def _get_value_names(self, task_index: int) -> list[str] | None:
        tc = self._task_configs.get(task_index)
        if not tc:
            return None
        nv = SENSOR_TYPE_TO_VALUE_COUNT.get(int(tc.get("TDNUM_out", 1)), 1)
        return [tc.get(f"TDVN{i + 1}", f"Value {i + 1}") for i in range(nv)]

    def to_storage_format(self, rule_sets: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return rule_sets


_RULES_ENGINE_INSTANCE: RulesEngine | None = None


def get_rules_engine() -> RulesEngine | None:
    global _RULES_ENGINE_INSTANCE
    return _RULES_ENGINE_INSTANCE


def set_rules_engine(engine: RulesEngine | None) -> None:
    global _RULES_ENGINE_INSTANCE
    _RULES_ENGINE_INSTANCE = engine

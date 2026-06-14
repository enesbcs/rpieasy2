#!/usr/bin/env python3
import argparse
import asyncio
import logging
import os
import signal
import time
from typing import Any

from aiohttp import web

from rpieasy2.core.config import get_config
from rpieasy2.core.events import Event, get_event_bus
from rpieasy2.core.hw import create_hw_manager, detect_ftdi, has_native_hw
from rpieasy2.core.logger import setup_logging
from rpieasy2.core.rpiconst import BACKUP_WEB_PORT, DEFAULT_TASK_INTERVAL, DEFAULT_WEB_PORT, PERIODIC_TICK_INTERVAL
from rpieasy2.core.rules_engine import RulesEngine, set_rules_engine
from rpieasy2.core.system_vars import LIVE_TASK_NAMES, LIVE_TASK_VALUES, set_start_time
from rpieasy2.core.scheduler import Scheduler
from rpieasy2.core.webserver import (
    _controller_info,
    _lazy_load_controller,
    _lazy_load_plugin,
    _notifier_info,
    _plugin_info,
    GPIO_NAMES,
    create_app,
    load_gpio_names,
    subscribe_plugin_read,
)

logger = logging.getLogger("rpieasy2")
scheduler = Scheduler()
_active_plugins: dict[int, Any] = {}
_last_task_read: dict[int, float] = {}
_rules_engine: RulesEngine | None = None
_hw_manager: Any = None


async def _init_controllers_async(hw, config) -> None:
    bus = get_event_bus()
    for idx, ctrl_cfg in enumerate(config.data.get("controllers", [])):
        cid = ctrl_cfg.get("id")
        if cid is None:
            continue
        if not ctrl_cfg.get("controllerenabled", ctrl_cfg.get("enabled", True)):
            continue
        entry = _controller_info.get(int(cid))
        if entry is None:
            logger.warning(f"Controller ID {cid} not found")
            continue
        _lazy_load_controller(int(cid))
        cls = entry.get("class")
        if cls is None:
            continue
        try:
            inst = cls()
            inst.set_hw_manager(hw)
            inst.subscribe()
            ctrl_cfg_with_unit = dict(ctrl_cfg)
            ctrl_cfg_with_unit["unit"] = config.data.get("system", {}).get("unit", 0)
            await bus.publish(Event(type="CONTROLLER_INIT", controller_index=idx,
                                    data={"controller_config": ctrl_cfg_with_unit,
                                          "system_config": config}))
            logger.debug(f"Loaded controller '{inst.CONTROLLER_NAME}' (ID={cid}) idx={idx}")
        except Exception as e:
            logger.error(f"Failed to init controller {cid}: {e}")


async def _init_notifiers_async(hw, config) -> None:
    bus = get_event_bus()
    for idx, notif_cfg in enumerate(config.data.get("notifications", [])):
        nid = notif_cfg.get("id")
        if nid is None:
            continue
        if not notif_cfg.get("enabled", True):
            continue
        entry = _notifier_info.get(int(nid))
        if entry is None:
            logger.warning(f"Notifier ID {nid} not found")
            continue
        cls = entry.get("class")
        if cls is None:
            continue
        try:
            inst = cls()
            inst.set_hw_manager(hw)
            inst.subscribe()
            await bus.publish(Event(type="NOTIFIER_INIT", notifier_index=idx,
                                    data={"notifier_config": notif_cfg}))
            logger.debug(f"Loaded notifier '{inst.NOTIFIER_NAME}' (ID={nid}) idx={idx}")
        except Exception as e:
            logger.error(f"Failed to init notifier {nid}: {e}")


async def _init_task_plugins_async(hw, config) -> dict[int, Any]:
    active: dict[int, Any] = {}
    bus = get_event_bus()
    for i, task in enumerate(config.data.get("tasks", [])):
        enabled = task.get("TDE", task.get("enabled", True))
        if isinstance(enabled, str):
            enabled = enabled.lower() in ("true", "1", "yes", "on")
        if not bool(enabled):
            continue
        pid = task.get("plugin_id") or task.get("plugin")
        if pid is None:
            continue
        pid_int = int(pid) if isinstance(pid, (int, str)) else None
        _lazy_load_plugin(pid_int)
        entry = _plugin_info.get(pid_int)
        if entry is None:
            logger.warning(f"Plugin ID {pid_int} not found for task {i}")
            continue
        cls = entry.get("class")
        if cls is None:
            logger.warning(f"Plugin class missing for ID {pid_int}")
            continue
        try:
            inst = cls()
            inst.set_hw_manager(hw)
            inst._config = task
            inst.subscribe(task_index=i)
            ev = Event(type="PLUGIN_INIT", task_index=i, data={"task_config": task})
            await bus.publish(ev)
            active[i] = inst
            logger.debug(f"Loaded plugin {inst.PLUGIN_NAME} for task {i}")
        except Exception as e:
            logger.error(f"Failed to init plugin for task {i}: {e}")
    return active


async def _handle_task_config_changed(event: Event) -> bool | None:
    ti = event.task_index
    if ti < 0:
        return False
    task_config = event.data.get("task_config", {})
    global _active_plugins
    global _rules_engine
    if _rules_engine:
        enabled = task_config.get("TDE", task_config.get("enabled", True))
        if isinstance(enabled, str):
            enabled = enabled.lower() in ("true", "1", "yes")
        _rules_engine._task_enabled[ti] = bool(enabled)
        _rules_engine._task_configs[ti] = task_config
        tname = task_config.get("TDN", task_config.get("name", f"Task{ti}"))
        _rules_engine._task_names[ti] = tname
    old_inst = _active_plugins.pop(ti, None)
    if old_inst:
        try:
            await old_inst.on_plugin_exit(Event(type="PLUGIN_EXIT", task_index=ti, data={}))
        except Exception:
            pass
        logger.debug(f"Unloaded plugin for task {ti}")
    pid = task_config.get("plugin_id")
    if pid is None:
        return True
    pid_int = int(pid) if isinstance(pid, (int, str)) else 0
    _lazy_load_plugin(pid_int)
    entry = _plugin_info.get(pid_int)
    if entry is None:
        return True
    cls = entry.get("class")
    if cls is None:
        return True
    try:
        inst = cls()
        global _hw_manager
        if _hw_manager:
            inst.set_hw_manager(_hw_manager)
        inst._config = task_config
        inst._task_index = ti
        inst.subscribe(task_index=ti)
        init_ev = Event(type="PLUGIN_INIT", task_index=ti, data={"task_config": task_config})
        await inst.on_plugin_init(init_ev)
        _active_plugins[ti] = inst
        logger.debug(f"Runtime loaded plugin {inst.PLUGIN_NAME} for task {ti}")
    except Exception as e:
        logger.error(f"Failed to runtime init plugin for task {ti}: {e}")
    return True


async def periodic_tick(rules_engine: RulesEngine | None = None):
    bus = get_event_bus()
    cfg = get_config()
    now = time.time()
    if rules_engine and rules_engine.is_enabled():
        rules_engine.check_timers()
    for i, task in enumerate(cfg.data.get("tasks", [])):
        enabled = task.get("TDE", task.get("enabled", True))
        if rules_engine and i in rules_engine._task_enabled:
            enabled = rules_engine._task_enabled[i]
        data_feed = task.get("data_feed_source", 0)
        if data_feed != 0:
            continue
        if enabled and i in _active_plugins:
            await bus.publish(Event(type="PLUGIN_ONCE_A_SECOND", task_index=i))
            interval = int(task.get("TDT") or task.get("interval", DEFAULT_TASK_INTERVAL))
            if interval <= 0 or (now - _last_task_read.get(i, 0.0)) >= interval:
                _last_task_read[i] = now
                await bus.publish(Event(type="PLUGIN_READ", task_index=i))


async def ten_per_second_tick():
    bus = get_event_bus()
    cfg = get_config()
    for i, task in enumerate(cfg.data.get("tasks", [])):
        enabled = task.get("TDE", task.get("enabled", True))
        if enabled and i in _active_plugins:
            await bus.publish(Event(type="PLUGIN_TEN_PER_SECOND", task_index=i))


async def fifty_per_second_tick():
    bus = get_event_bus()
    cfg = get_config()
    for i, task in enumerate(cfg.data.get("tasks", [])):
        enabled = task.get("TDE", task.get("enabled", True))
        if enabled and i in _active_plugins:
            await bus.publish(Event(type="PLUGIN_FIFTY_PER_SECOND", task_index=i))


_BOOT_START: float = 0.0


def _log_boot(phase: str) -> None:
    elapsed = time.time() - _BOOT_START
    logger.debug(f"[BOOT] {phase} ({elapsed:.3f}s)")


async def main():
    global _BOOT_START
    _BOOT_START = time.time()
    parser = argparse.ArgumentParser(description="RPIEasy")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=DEFAULT_WEB_PORT)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--config", help="Config file path")
    parser.add_argument("--ftdi", action="store_true")
    args = parser.parse_args()
    _log_boot("argparse done")

    setup_logging(level=logging.DEBUG if args.debug else logging.INFO)
    set_start_time(time.time())
    logger.info("RPIEasy starting...")

    cfg = get_config()
    if args.config:
        cfg.path = args.config
        cfg.load()
    _log_boot("config loaded")

    use_ftdi = args.ftdi or detect_ftdi()
    ftdi_devices = cfg.data.get("system", {}).get("ftdi_devices", [])
    if use_ftdi and ftdi_devices:
        for d in ftdi_devices:
            if not d.get("device_id"):
                logger.warning("FTDI config entry (url=%s) has no device_id. Open Hardware page and re-save to enable auto URL resolution.", d.get("url", ""))
        try:
            from rpieasy2.core.hw.ftdi import resolve_ftdi_configs
            resolved = resolve_ftdi_configs(ftdi_devices)
            ftdi_devices = sorted(resolved, key=lambda d: (d.get("device_id") or d.get("url", "") or ""))
        except Exception:
            pass
    hw = create_hw_manager(use_ftdi=use_ftdi, ftdi_config=ftdi_devices[0] if ftdi_devices else None,
                            ftdi_devices=ftdi_devices if ftdi_devices else None)
    global _hw_manager
    _hw_manager = hw
    hw_label = "FTDI" if use_ftdi else ("native RPi" if has_native_hw() else "Generic PC")
    logger.info(f"Hardware: {hw_label}")
    if has_native_hw() and not use_ftdi:
        from rpieasy2.core.rpiconst import is_raspberry_pi
        if not is_raspberry_pi():
            load_gpio_names()
            logger.info(f"GPIO_NAMES loaded: {len(GPIO_NAMES)} entries")
            gpio_modes = cfg.data.get("system", {}).get("gpio_modes", {})
            if gpio_modes and hasattr(hw, "gpio") and hasattr(hw.gpio, "set_pin_mode"):
                for pin_str, mode in gpio_modes.items():
                    try:
                        hw.gpio.set_pin_mode(int(pin_str), mode)
                    except Exception as e:
                        logger.warning("Failed to set pin mode for %s: %s", pin_str, e)
                if hasattr(hw.gpio, "apply_pin_modes"):
                    try:
                        hw.gpio.apply_pin_modes()
                    except Exception as e:
                        logger.warning("apply_pin_modes failed: %s", e)
                logger.info("GPIO modes applied: %d", len(gpio_modes))
    _log_boot("hw manager created")

    base_dir = os.path.dirname(__file__)
    plugins_dir = os.path.join(base_dir, "rpieasy2", "plugins")
    controllers_dir = os.path.join(base_dir, "rpieasy2", "controllers")
    notifiers_dir = os.path.join(base_dir, "rpieasy2", "notifiers")
    app = create_app(plugins_dir=plugins_dir, controllers_dir=controllers_dir,
                     notifiers_dir=notifiers_dir)
    app["hw_manager"] = hw
    _log_boot("app created")

    # --- Rules Engine (initialize before webserver start) ---
    global _rules_engine
    rules_enabled = cfg.data.get("system", {}).get("enable_rules", True)
    _rules_engine = RulesEngine()
    _rules_engine.set_hw_manager(hw)
    _rules_engine.set_plugin_info(_plugin_info)
    task_configs = {}
    for ti, task in enumerate(cfg.data.get("tasks", [])):
        task_configs[ti] = task
    _rules_engine.set_task_configs(task_configs)
    _rules_engine.load_rules(cfg.get_rules())
    _rules_engine.set_enabled(rules_enabled)
    app["rules_engine"] = _rules_engine
    set_rules_engine(_rules_engine)
    logger.info(f"Rules engine: {'enabled' if rules_enabled else 'disabled'} ({len(cfg.get_rules())} rule sets)")
    _log_boot("rules engine ready")

    # Start webserver early so the web UI is available ASAP after update
    cfg_port = cfg.data.get("system", {}).get("web_port")
    if cfg_port and args.port == DEFAULT_WEB_PORT:
        ports = [int(cfg_port)]
    else:
        ports = [args.port, BACKUP_WEB_PORT] if args.port == DEFAULT_WEB_PORT else [args.port]
    runner = web.AppRunner(app)
    await runner.setup()
    site = None
    for port in ports:
        try:
            site = web.TCPSite(runner, args.host, port)
            await site.start()
            args.port = port
            logger.info(f"Webserver on http://{args.host}:{port}")
            break
        except OSError as e:
            logger.warning(f"Port {port} not available: {e}")
    if site is None:
        logger.error("No available port found, exiting")
        return
    cfg.data.setdefault("system", {})["web_port"] = args.port
    cfg.save()
    _log_boot("webserver started")

    bus = get_event_bus()
    bus.subscribe("TASK_CONFIG_CHANGED", _handle_task_config_changed)

    global _active_plugins
    _active_plugins = await _init_task_plugins_async(hw, cfg)
    logger.debug(f"Active task plugins: {len(_active_plugins)}")
    _log_boot("task plugins initialized")

    subscribe_plugin_read()
    _log_boot("plugin read subscribed")

    await _init_controllers_async(hw, cfg)
    _log_boot("controllers initialized")

    await _init_notifiers_async(hw, cfg)
    _log_boot("notifiers initialized")

    for idx, ctrl_cfg in enumerate(cfg.data.get("controllers", [])):
        if not ctrl_cfg.get("controllerenabled", ctrl_cfg.get("enabled", True)) or not ctrl_cfg.get("id"):
            continue
        min_interval = float(ctrl_cfg.get("queue_min_interval", 1))

        async def _process_queue(idx=idx):
            await bus.publish(Event(type="CONTROLLER_PROCESS_QUEUE", controller_index=idx))

        await scheduler.schedule_interval(min_interval, _process_queue)
        logger.debug(f"Queue timer started for controller {idx} (interval={min_interval}s)")

    from rpieasy2.core.p2p_service import start_p2p_service
    await start_p2p_service(cfg, scheduler)
    _log_boot("p2p service started")

    now = time.time()
    for ti, inst in _active_plugins.items():
        task = cfg.data.get("tasks", [])[ti] if ti < len(cfg.data.get("tasks", [])) else {}
        enabled = task.get("TDE", task.get("enabled", True))
        if not enabled:
            continue
        _last_task_read[ti] = now
        await bus.publish(Event(type="PLUGIN_READ", task_index=ti))
    _log_boot("initial PLUGIN_READ done")

    _rules_prev_vals: dict[int, dict[str, str]] = {}

    async def _rules_plugin_read_listener(event: Event) -> bool | None:
        global _rules_engine
        if _rules_engine and _rules_engine.is_enabled() and event.task_index >= 0:
            vals = event.data.get("values", {})
            if vals:
                prev = _rules_prev_vals.get(event.task_index, {})
                _rules_engine.update_task_value(event.task_index, vals)
                _rules_prev_vals[event.task_index] = {k: str(v) for k, v in vals.items()}
                tname = _rules_engine._task_names.get(event.task_index, f"Task{event.task_index}")
                for vname, vval in vals.items():
                    if str(prev.get(vname, "")) != str(vval):
                        await _rules_engine.fire_event(f"{tname}#{vname}")
        return None

    bus.subscribe("PLUGIN_READ", _rules_plugin_read_listener)

    async def _controller_send_listener(event: Event) -> bool | None:
        if event.task_index < 0:
            return None
        task_config = cfg.data.get("tasks", [])[event.task_index] if event.task_index < len(cfg.data.get("tasks", [])) else {}
        if not task_config:
            return None
        vals = event.data.get("values", {})
        if not vals:
            return None
        LIVE_TASK_VALUES[event.task_index] = {k: str(v) for k, v in vals.items()}
        LIVE_TASK_NAMES[event.task_index] = task_config.get("name", task_config.get("TDN", f"Task{event.task_index + 1}"))
        bus2 = get_event_bus()
        for ctrl_idx in range(len(cfg.data.get("controllers", []))):
            ctrl_cfg = cfg.data["controllers"][ctrl_idx]
            if not ctrl_cfg.get("controllerenabled", ctrl_cfg.get("enabled", True)):
                continue
            if not task_config.get(f"TDSD{ctrl_idx}", True):
                continue
            parent_ctrl = ctrl_cfg.get("id", 0)
            send_ev = Event(
                type="CONTROLLER_SEND",
                task_index=event.task_index,
                controller_index=ctrl_idx,
                data={
                    "task_config": task_config,
                    "values": {
                        "named_values": dict(vals),
                        "value_names": list(vals.keys()),
                        "controller": dict(ctrl_cfg),
                    },
                },
            )
            await bus2.publish(send_ev)
        return None

    bus.subscribe("PLUGIN_READ", _controller_send_listener)

    async def _rules_gpio_change_listener(event: Event) -> bool | None:
        global _rules_engine
        if _rules_engine and _rules_engine.is_enabled() and event.task_index >= 0:
            state = event.data.get("state")
            if state is not None:
                _rules_engine.update_task_value(event.task_index, {"Switch": state})
                tname = _rules_engine._task_names.get(event.task_index, f"Task{event.task_index}")
                await _rules_engine.fire_event(f"{tname}#Switch")
                _rules_prev_vals[event.task_index] = {"Switch": str(state)}
                await get_event_bus().publish(
                    Event(type="PLUGIN_READ", task_index=event.task_index)
                )
        return None

    bus.subscribe("PLUGIN_GPIO_CHANGE", _rules_gpio_change_listener)

    async def _rules_system_boot() -> None:
        global _rules_engine
        if _rules_engine and _rules_engine.is_enabled():
            await _rules_engine.fire_event("System#Boot")

    asyncio.create_task(_rules_system_boot())

    await scheduler.schedule_interval(PERIODIC_TICK_INTERVAL, periodic_tick, _rules_engine)
    await scheduler.schedule_interval(0.1, ten_per_second_tick)
    await scheduler.schedule_interval(0.02, fifty_per_second_tick)
    _log_boot("scheduler started")

    stop_event = asyncio.Event()

    _log_boot("startup complete")

    stop_event = asyncio.Event()

    def _signal_handler():
        logger.info("Shutting down...")
        scheduler.stop()
        asyncio.create_task(_do_shutdown())

    async def _do_shutdown():
        await hw.close()
        from rpieasy2.core.p2p_service import stop_p2p_service
        stop_p2p_service()
        stop_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            pass

    await stop_event.wait()
    await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())

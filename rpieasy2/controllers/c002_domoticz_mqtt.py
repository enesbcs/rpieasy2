from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiomqtt

from rpieasy2.core import webserver as webserver_mod
from rpieasy2.core.config import get_config
from rpieasy2.core.controller_base import ControllerBase
from rpieasy2.core.events import Event, get_event_bus
from rpieasy2.core.rpiconst import DEFAULT_MQTT_PORT
from rpieasy2.core.rules_engine import RuleCommand, get_rules_engine
from rpieasy2.core.system_vars import resolve_controller_template

logger = logging.getLogger("rpieasy2.controller.c002")


class C002DomoticzMQTT(ControllerBase):
    CONTROLLER_ID = 2
    CONTROLLER_NAME = "Domoticz MQTT"
    CONTROLLER_HAS_MQTT = True
    usesMQTT = True
    usesTemplate = True
    usesAccount = True
    usesPassword = True
    usesExtCreds = True
    usesID = True
    defaultPort = 1883

    def __init__(self):
        super().__init__()
        self._client: aiomqtt.Client | None = None
        self._ctrl_config: dict[str, Any] = {}
        self._pending_subscriptions: list[str] = ["domoticz/out"]
        self._controller_index: int | None = None
        self._cmd_sub_topic: str | None = None
        self._client_loop_task: asyncio.Task | None = None

    def subscribe(self) -> None:
        super().subscribe()

    async def on_controller_config_changed(self, event: Event) -> bool | None:
        """Restart the client loop so new settings are applied immediately.

        aiomqtt 2.x clients must be closed from the task that opened the
        context manager, so the loop task is cancelled (which exits the
        ``async with`` cleanly) and started again to reconnect.
        """
        if getattr(self, "_shutting_down", False):
            return None
        if event.controller_index >= 0 and event.controller_index != getattr(self, "_controller_index", -1):
            return None
        # Check if controller is still enabled in live config before logging anything
        try:
            from rpieasy2.core.config import get_config
            cfg = get_config()
            ctrls = cfg.data.get("controllers", [])
            idx = getattr(self, "_controller_index", None)
            if idx is None or idx >= len(ctrls) or not ctrls[idx].get("id"):
                return None  # Controller was removed
            if not ctrls[idx].get("controllerenabled", ctrls[idx].get("enabled", True)):
                return None  # Controller was disabled
        except Exception:
            pass
        logger.info("Controller config changed, restarting MQTT client loop")
        task = getattr(self, "_client_loop_task", None)
        if task is not None and not task.done():
            task.cancel()
        self._client_loop_task = asyncio.create_task(self._run_client_loop())
        return True

    async def on_controller_init(self, event: Event) -> bool | None:
        config: dict[str, Any] = event.data.get("controller_config", {})
        self._ctrl_config = config
        try:
            self._controller_index = int(event.controller_index)
        except Exception:
            self._controller_index = None
        self._cmd_sub_topic = None
        if config.get("enable_cmd_subscription"):
            topic = resolve_controller_template("%sysname%/cmd", controller_config=config)
            self._cmd_sub_topic = topic
            if topic not in self._pending_subscriptions:
                self._pending_subscriptions.append(topic)
        if config.get("controllerenabled", config.get("enabled", True)):
            self._client_loop_task = asyncio.create_task(self._run_client_loop())
        else:
            logger.info("MQTT controller disabled, not starting client loop")
        return True

    async def _run_client_loop(self) -> None:
        backoff = 1
        while True:
            config = self._ctrl_config
            # Re-read the live controller settings so web UI changes (host, port,
            # credentials, enable flag) are picked up without a restart.
            idx = getattr(self, "_controller_index", None)
            if idx is not None:
                try:
                    ctrls = get_config().data.get("controllers", [])
                    live = ctrls[idx] if 0 <= idx < len(ctrls) else None
                    if live and live.get("id") == self.CONTROLLER_ID:
                        config = live
                        self._ctrl_config = config
                    elif live is not None and live.get("id") != self.CONTROLLER_ID:
                        # protocol changed away from this slot; idle until restart
                        config = {"controllerenabled": False}
                        self._ctrl_config = config
                except Exception:
                    pass
            if not config.get("controllerenabled", config.get("enabled", True)):
                await asyncio.sleep(5)
                continue
            if config.get("enable_cmd_subscription"):
                try:
                    topic = resolve_controller_template("%sysname%/cmd", controller_config=config)
                    if topic:
                        self._cmd_sub_topic = topic
                        if topic not in self._pending_subscriptions:
                            self._pending_subscriptions.append(topic)
                except Exception:
                    pass
            else:
                self._cmd_sub_topic = None
            try:
                host = config.get("controllerip", config.get("host", "127.0.0.1")) or "127.0.0.1"
                host = host.strip()
                if not host:
                    host = "127.0.0.1"
                port_raw = config.get("controllerport", config.get("port", DEFAULT_MQTT_PORT))
                try:
                    port = int(port_raw) if port_raw else DEFAULT_MQTT_PORT
                except (ValueError, TypeError):
                    port = DEFAULT_MQTT_PORT
                user = config.get("controlleruser", config.get("username", "")) or None
                password = config.get("controllerpassword", config.get("password", "")) or None
                use_tls = int(config.get("usetls", 0))

                will = None
                will_topic = config.get("controllerlwttopic", "")
                if config.get("sendlwttobroker", False) and will_topic:
                    will = aiomqtt.Will(
                        topic=will_topic,
                        payload=config.get("lwtdisconnectmessage", "offline") or "offline",
                        retain=config.get("willretain", False),
                    )

                logger.info("Connecting to Domoticz MQTT %s:%s", host, port)
                tls_params = aiomqtt.TLSParameters() if use_tls and port == 8883 else None
                async with aiomqtt.Client(hostname=host, port=port,
                                          username=user,
                                          password=password,
                                          will=will,
                                          tls_params=tls_params) as client:
                    self._client = client
                    backoff = 1

                    if will_topic and config.get("lwtconnectmessage"):
                        await client.publish(will_topic,
                                             config["lwtconnectmessage"],
                                             retain=config.get("willretain", False))

                    for topic in self._pending_subscriptions:
                        try:
                            await client.subscribe(topic)
                            logger.info("Domoticz MQTT subscribed to: %s", topic)
                        except Exception as e:
                            logger.error("Domoticz MQTT subscribe failed for %s: %s", topic, e)

                    try:
                        if idx is not None:
                            webserver_mod.set_controller_runtime_state(idx, {"connected": True, "last_error": ""})
                    except Exception:
                        pass

                    await self._listen()
            except Exception as e:
                logger.error("Domoticz MQTT connection error: %s", e)
                try:
                    if idx is not None:
                        webserver_mod.set_controller_runtime_state(idx, {"connected": False, "last_error": str(e)})
                except Exception:
                    pass
            finally:
                try:
                    if idx is not None:
                        webserver_mod.set_controller_runtime_state(idx, {"connected": False})
                except Exception:
                    pass
                self._client = None

            await asyncio.sleep(min(backoff, 30))
            backoff = min(backoff * 2, 30)

    async def _listen(self) -> None:
        async for msg in self._client.messages:
            topic = msg.topic.value
            payload = msg.payload.decode().strip()
            if self._cmd_sub_topic and topic == self._cmd_sub_topic:
                if payload:
                    engine = get_rules_engine()
                    if engine and engine.is_enabled():
                        rc = RuleCommand(payload)
                        if rc.name:
                            await engine._execute_command(rc, {
                                "task_values": {}, "vars": engine._vars, "str_vars": engine._str_vars,
                            })
                continue
            if topic == "domoticz/out":
                await self._handle_domoticz_command(payload)

    async def _handle_domoticz_command(self, payload: str) -> None:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return
        idx = data.get("idx")
        if not idx:
            return
        nvalue = data.get("nvalue", 0)
        svalue1 = data.get("svalue1", "")
        switchtype = data.get("switchtype", "")
        try:
            idx = int(idx)
        except (ValueError, TypeError):
            return
        cfg = get_config()
        tasks = cfg.data.get("tasks", [])
        ctrl_idx = self._controller_index
        for ti, task in enumerate(tasks):
            send_flag = False
            if ctrl_idx is not None:
                send_flag = task.get(f"TDSD{ctrl_idx}", False)
                if not send_flag and task.get("TDSD") is not None:
                    send_flag = bool(task.get("TDSD"))
            if not send_flag:
                continue
            task_idx_val = task.get("task_values", {}).get("idx", task.get("idx", 0))
            try:
                task_idx_val = int(task_idx_val)
            except (ValueError, TypeError):
                continue
            if task_idx_val != idx:
                continue
            plugin_id = int(task.get("plugin_id", task.get("plugin", 0)) or 0)
            pin = int(task.get("pin", 0)) if task.get("pin") else -1

            if plugin_id == 1:
                action_value = int(nvalue)
                bus = get_event_bus()
                ev = Event(type="PLUGIN_WRITE", task_index=ti,
                           data={"values": {str(pin): action_value}})
                await bus.publish(ev)
                logger.info("Domoticz MQTT: GPIO command for task %d: pin=%d value=%d", ti, pin, action_value)
            elif plugin_id in (29, 88):
                if switchtype == "dimmer":
                    pwm_value = 0
                    if nvalue == 1 or nvalue == 2:
                        try:
                            pwm_value = int(float(svalue1)) * 10
                        except (ValueError, TypeError):
                            pwm_value = 0
                    bus = get_event_bus()
                    ev = Event(type="PLUGIN_WRITE", task_index=ti,
                               data={"values": {str(pin): pwm_value}})
                    await bus.publish(ev)
                    logger.info("Domoticz MQTT: dimmer command for task %d: pin=%d value=%d", ti, pin, pwm_value)
                else:
                    action_value = int(nvalue)
                    bus = get_event_bus()
                    ev = Event(type="PLUGIN_WRITE", task_index=ti,
                               data={"values": {str(pin): action_value}})
                    await bus.publish(ev)
                    logger.info("Domoticz MQTT: switch command for task %d: pin=%d value=%d", ti, pin, action_value)

    async def on_controller_send(self, event: Event) -> bool | None:
        if not self._client:
            return False

        if event.data.get("send_to_mqtt"):
            topic = event.data.get("topic", "")
            message = event.data.get("message", "")
            retain = event.data.get("retain", False)
            if topic and message:
                try:
                    await self._client.publish(topic, message, retain=retain)
                    return True
                except Exception as e:
                    logger.error("Domoticz MQTT rule publish failed: %s", e)
                    return False
            return False

        config: dict[str, Any] = event.data.get("task_config", {})
        values: dict[str, Any] = event.data.get("values", {})
        ctrl_config = config.get("controller", {})
        topic_template = ctrl_config.get("controllerpublish", ctrl_config.get("topic", "domoticz/in"))
        idx = config.get("task_values", {}).get("idx", config.get("idx", "0"))
        retain_flag = int(ctrl_config.get("publishretainflag", 0))

        from rpieasy2.core.util import get_wifi_rssi
        rssi = get_wifi_rssi() or 0

        payload = {"idx": int(idx), "nvalue": 0}
        named_values = values.get("named_values", values)
        svalue = ";".join(str(v) for v in named_values.values())
        payload["svalue"] = svalue
        payload["Battery"] = 255
        payload["RSSI"] = rssi

        topic = resolve_controller_template(topic_template, task_index=event.task_index,
                                             task_config=config, task_values=values,
                                             controller_config=ctrl_config)
        try:
            await self._client.publish(topic, json.dumps(payload), retain=bool(retain_flag))
            return True
        except Exception as e:
            logger.error("Domoticz MQTT publish failed: %s", e)
            return False

    async def on_controller_subscribe(self, event: Event) -> bool | None:
        topic = event.data.get("topic", "")
        if not topic:
            return False
        if self._client:
            try:
                await self._client.subscribe(topic)
                logger.info("Domoticz MQTT subscribed to: %s", topic)
                return True
            except Exception as e:
                logger.error("Domoticz MQTT subscribe failed: %s", e)
                return False
        else:
            self._pending_subscriptions.append(topic)
            return True

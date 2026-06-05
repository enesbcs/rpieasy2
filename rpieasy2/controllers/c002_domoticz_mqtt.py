from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiomqtt

from rpieasy2.core.config import get_config
from rpieasy2.core.controller_base import ControllerBase
from rpieasy2.core.events import Event, get_event_bus
from rpieasy2.core.rpiconst import DEFAULT_MQTT_PORT
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

    async def on_controller_init(self, event: Event) -> bool | None:
        config: dict[str, Any] = event.data.get("controller_config", {})
        self._ctrl_config = config
        try:
            self._controller_index = int(event.controller_index)
        except Exception:
            self._controller_index = None
        if config.get("controllerenabled", config.get("enabled", True)):
            asyncio.create_task(self._run_client_loop())
        else:
            logger.info("MQTT controller disabled, not starting client loop")
        return True

    async def _run_client_loop(self) -> None:
        backoff = 1
        while True:
            config = self._ctrl_config
            if not config.get("controllerenabled", config.get("enabled", True)):
                await asyncio.sleep(5)
                continue
            try:
                host = config.get("controllerip", config.get("host", "127.0.0.1"))
                port = int(config.get("controllerport", config.get("port", DEFAULT_MQTT_PORT)))
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

                    await self._listen()
            except Exception as e:
                logger.error("Domoticz MQTT connection error: %s", e)
            finally:
                self._client = None

            await asyncio.sleep(min(backoff, 30))
            backoff = min(backoff * 2, 30)

    async def _listen(self) -> None:
        async for msg in self._client.messages:
            topic = msg.topic.value
            payload = msg.payload.decode().strip()
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

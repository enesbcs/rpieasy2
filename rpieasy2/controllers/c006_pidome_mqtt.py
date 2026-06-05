from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiomqtt

from rpieasy2.core.config import get_config
from rpieasy2.core.controller_base import ControllerBase
from rpieasy2.core.events import Event, get_event_bus
from rpieasy2.core.rpiconst import DEFAULT_MQTT_PORT
from rpieasy2.core.system_vars import resolve_controller_template

logger = logging.getLogger("rpieasy2.controller.c006")

PIDOME_DEFAULT_TOPIC = "/hooks/devices/%id%/SensorData/%valname%"


class C006PiDomeMQTT(ControllerBase):
    CONTROLLER_ID = 6
    CONTROLLER_NAME = "PiDome MQTT"
    CONTROLLER_HAS_MQTT = True
    usesMQTT = True
    usesAccount = False
    usesPassword = False
    usesExtCreds = True
    usesTemplate = True
    usesID = False
    defaultPort = DEFAULT_MQTT_PORT

    def __init__(self):
        super().__init__()
        self._client: aiomqtt.Client | None = None
        self._ctrl_config: dict[str, Any] = {}
        self._pending_subscriptions: list[str] = ["/Home/#"]

    async def on_controller_init(self, event: Event) -> bool | None:
        config = event.data.get("controller_config", {})
        self._ctrl_config = config
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
                client_id = config.get("controllerclientid", "") or None
                use_tls = int(config.get("usetls", 0))

                kwargs: dict[str, Any] = dict(hostname=host, port=port)
                if user:
                    kwargs["username"] = user
                if password is not None:
                    kwargs["password"] = password
                if client_id:
                    kwargs["client_id"] = client_id
                if use_tls:
                    kwargs["tls_params"] = aiomqtt.TLSParameters()

                logger.info("Connecting to PiDome MQTT %s:%s", host, port)
                async with aiomqtt.Client(**kwargs) as client:
                    self._client = client
                    backoff = 1

                    for topic in self._pending_subscriptions:
                        try:
                            await client.subscribe(topic)
                            logger.info("PiDome MQTT subscribed to: %s", topic)
                        except Exception as e:
                            logger.error("PiDome MQTT subscribe failed for %s: %s", topic, e)

                    await self._listen()
            except Exception as e:
                logger.error("PiDome MQTT connection error: %s", e)
            finally:
                self._client = None

            await asyncio.sleep(min(backoff, 30))
            backoff = min(backoff * 2, 30)

    async def _listen(self) -> None:
        async for msg in self._client.messages:
            topic = msg.topic.value
            payload = msg.payload.decode().strip()
            if topic.endswith("/set"):
                await self._handle_set_command(topic, payload)
            else:
                await self._handle_pidome_command(topic, payload)

    async def _handle_set_command(self, topic: str, payload: str) -> None:
        cfg = get_config()
        tasks = cfg.data.get("tasks", [])
        base_topic = topic[:-4]
        parts = base_topic.split("/")
        if len(parts) >= 2:
            task_name = parts[-2]
            value_name = parts[-1]
            for ti, task in enumerate(tasks):
                tname = task.get("TDN", task.get("name", ""))
                if tname == task_name:
                    bus = get_event_bus()
                    ev = Event(type="PLUGIN_WRITE", task_index=ti,
                               data={"values": {value_name: payload}})
                    await bus.publish(ev)
                    logger.info("PiDome MQTT set: task=%s value=%s=%s", task_name, value_name, payload)
                    return

    async def _handle_pidome_command(self, topic: str, payload: str) -> None:
        tmp = topic.lstrip("/")
        parts = tmp.split("/")
        if len(parts) < 6:
            return
        sysname = get_config().data.get("system", {}).get("name", "")
        name = parts[4]
        if name != sysname:
            return
        cmd = parts[5]
        try:
            par1 = int(parts[6])
        except (ValueError, IndexError):
            return
        par2 = "1" if payload.lower() == "true" else ("0" if payload.lower() == "false" else payload)
        bus = get_event_bus()
        if cmd == "gpio":
            ev = Event(type="PLUGIN_WRITE", data={"values": {str(par1): int(par2)}})
            await bus.publish(ev)
            logger.info("PiDome MQTT gpio command: pin=%d value=%s", par1, par2)
        elif cmd == "pwm":
            ev = Event(type="PLUGIN_WRITE", data={"values": {str(par1): int(par2)}})
            await bus.publish(ev)
            logger.info("PiDome MQTT pwm command: pin=%d value=%s", par1, par2)
        else:
            logger.debug("PiDome MQTT unknown command: %s", cmd)

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
                    logger.error("PiDome MQTT rule publish failed: %s", e)
                    return False
            return False

        config = event.data.get("task_config", {})
        values = event.data.get("values", {})
        ctrl_config = config.get("controller", {})
        task_name = config.get("name", "rpieasy2_task")
        unit = config.get("unit", 1)
        retain_flag = int(ctrl_config.get("publishretainflag", 0))

        base_topic = ctrl_config.get("controllerpublish", ctrl_config.get("topic", PIDOME_DEFAULT_TOPIC))
        base_topic = resolve_controller_template(base_topic, task_index=event.task_index,
                                                  task_config=config, task_values=values,
                                                  controller_config=ctrl_config)
        named_values = values.get("named_values", values)

        try:
            for vname, vval in named_values.items():
                topic = f"{base_topic}/{vname}"
                await self._client.publish(topic, str(vval), retain=bool(retain_flag))
            return True
        except Exception as e:
            logger.error("PiDome MQTT publish failed: %s", e)
            return False

    async def on_controller_subscribe(self, event: Event) -> bool | None:
        topic = event.data.get("topic", "")
        if not topic:
            return False
        if self._client:
            try:
                await self._client.subscribe(topic)
                return True
            except Exception as e:
                logger.error("PiDome MQTT subscribe failed: %s", e)
                return False
        else:
            self._pending_subscriptions.append(topic)
            return True

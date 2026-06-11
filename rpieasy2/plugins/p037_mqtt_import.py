from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiomqtt

from rpieasy2.core.events import Event, get_event_bus
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEFAULT_MQTT_PORT, DEVICE_TYPE_DUMMY, SENSOR_TYPE_SINGLE
from rpieasy2.core.device_properties import DeviceProperties

logger = logging.getLogger("rpieasy2.plugin.p037")


def _try_parse_json(payload: str) -> dict[str, Any] | None:
    try:
        return json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return None


class P037MQTTImport(PluginBase):
    PLUGIN_ID = 37
    PLUGIN_NAME = "Generic - MQTT Import"
    PLUGIN_VALUES = 4
    DEVICE_PROPERTIES = DeviceProperties(type=DEVICE_TYPE_DUMMY, vtype=SENSOR_TYPE_SINGLE, value_count=4, decimals_only=True, custom_vtype_var=True, send_data_option=True)

    def __init__(self):
        super().__init__()
        self._client: aiomqtt.Client | None = None
        self._config: dict[str, Any] = {}
        self._values: dict[str, Any] = {"Value1": "", "Value2": "", "Value3": "", "Value4": ""}
        self._last_raw: str = ""

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        host = self._config.get("host", "127.0.0.1") or "127.0.0.1"
        try:
            port = int(self._config.get("port", DEFAULT_MQTT_PORT))
        except (ValueError, TypeError):
            port = DEFAULT_MQTT_PORT
        topic = self._config.get("topic", "#") or "#"
        user = self._config.get("username", "") or ""
        password = self._config.get("password", "") or ""
        try:
            self._client = aiomqtt.Client(hostname=host, port=port,
                                          username=user if user else None,
                                          password=password if password else None)
            await self._client.__aenter__()
            await self._client.subscribe(topic)
            asyncio.create_task(self._listen())
        except Exception as e:
            logger.error("MQTT Import init failed: %s", e)
            return False
        return True

    async def on_plugin_exit(self, event: Event) -> bool | None:
        if self._client:
            try:
                await self._client.__aexit__(None, None, None)
            except Exception:
                pass
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        return True

    async def _listen(self) -> None:
        try:
            async with self._client.messages() as messages:
                async for msg in messages:
                    payload = msg.payload.decode()
                    topic_str = msg.topic.value
                    self._last_raw = payload
                    parse_json = int(self._config.get("parse_json") or 0)
                    if parse_json == 1:
                        parsed = _try_parse_json(payload)
                        if parsed:
                            self._values["Value1"] = str(parsed.get("Value1", parsed.get(list(parsed.keys())[0] if parsed else "", "")))
                            for i in range(1, 5):
                                key = f"Value{i}"
                                if key in parsed:
                                    self._values[key] = str(parsed[key])
                            ev = Event(type="PLUGIN_READ", task_index=self._task_index)
                            await get_event_bus().publish(ev)
                            continue
                    apply_mappings = int(self._config.get("apply_mappings") or 0)
                    if apply_mappings == 1:
                        for i in range(1, 6):
                            t = self._config.get(f"topic_{i}", "")
                            if t and topic_str.endswith(t):
                                self._values[f"Value{i}"] = payload
                                break
                    else:
                        self._values["Value1"] = payload
                    dedup = bool(self._config.get("deduplicate", False))
                    send_ev = bool(self._config.get("send_events", False))
                    if send_ev:
                        ev = Event(type="PLUGIN_WRITE", string1=topic_str, string2=payload)
                        await get_event_bus().publish(ev)
        except Exception as e:
            logger.error("MQTT Import listener error: %s", e)

    async def on_plugin_read(self, event: Event) -> bool | None:
        event.data["values"] = dict(self._values)
        return True

    async def on_plugin_get_device_value_names(self, event: Event) -> bool | None:
        event.data["value_names"] = ["Value1", "Value2", "Value3", "Value4"]
        return True

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        form = [
            {"name": "host", "label": "MQTT Host", "type": "text", "value": self._config.get("host", "127.0.0.1")},
            {"name": "port", "label": "MQTT Port", "type": "number", "value": self._config.get("port", DEFAULT_MQTT_PORT)},
            {"name": "topic", "label": "Topic Filter", "type": "text", "value": self._config.get("topic", "#")},
            {"name": "username", "label": "Username", "type": "text", "value": self._config.get("username", "")},
            {"name": "password", "label": "Password", "type": "password", "value": self._config.get("password", "")},
            {"name": "parse_json", "label": "Parse JSON", "type": "select", "value": self._config.get("parse_json", 0), "options": [
                {"value": 0, "label": "No"}, {"value": 1, "label": "Yes"},
            ]},
            {"name": "apply_mappings", "label": "Apply Mappings", "type": "select", "value": self._config.get("apply_mappings", 0), "options": [
                {"value": 0, "label": "No"}, {"value": 1, "label": "Yes"},
            ]},
            {"name": "apply_filters", "label": "Apply Filters", "type": "select", "value": self._config.get("apply_filters", 0), "options": [
                {"value": 0, "label": "No"}, {"value": 1, "label": "Yes"},
            ]},
            {"name": "send_events", "label": "Generate Events", "type": "checkbox", "value": self._config.get("send_events", False)},
            {"name": "deduplicate", "label": "Deduplicate Events", "type": "checkbox", "value": self._config.get("deduplicate", False)},
            {"name": "queue_depth", "label": "Max Event Queue Depth", "type": "number", "value": self._config.get("queue_depth", 0)},
            {"name": "replace_char", "label": "Replace Char (comma)", "type": "text", "value": self._config.get("replace_char", "")},
        ]
        for i in range(1, 6):
            form.append({"name": f"topic_{i}", "label": f"MQTT Topic {i}", "type": "text", "value": self._config.get(f"topic_{i}", "")})
        event.data["form"] = form
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_get_devicevaluecount(self, event: Event) -> bool | None:
        parse_json = int(self._config.get("parse_json") or 0)
        event.data["value_count"] = 4 if parse_json == 1 else 1
        return True

    
    async def on_plugin_get_discovery_vtypes(self, event: Event) -> bool | None:
        parse_json = int(self._config.get("parse_json") or 0)
        if parse_json == 1:
            event.data["vtypes"] = [SENSOR_V_TYPE_SINGLE] * 4
        else:
            event.data["vtypes"] = [SENSOR_V_TYPE_SINGLE]
        return True

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        parse_json = int(self._config.get("parse_json") or 0)
        if parse_json == 1:
            from rpieasy2.core.rpiconst import SENSOR_TYPE_QUAD
            event.data["vtype"] = SENSOR_TYPE_QUAD
        else:
            from rpieasy2.core.rpiconst import SENSOR_TYPE_SINGLE
            event.data["vtype"] = SENSOR_TYPE_SINGLE
        return True

    def get_task_values(self, task_config: dict[str, Any]) -> dict[str, Any]:
        return {"Value1": "", "Value2": "", "Value3": "", "Value4": ""}

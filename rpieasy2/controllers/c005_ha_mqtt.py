from __future__ import annotations

import asyncio
import re
import hashlib
import json
import logging
from typing import Any

import aiomqtt

from rpieasy2.core.controller_base import ControllerBase
from rpieasy2.core.device_properties import DeviceFlag
from rpieasy2.core.events import Event, get_event_bus
from rpieasy2.core.rpiconst import (
    DEFAULT_MQTT_PORT,
    SENSOR_V_TYPE_CAN_SET,
    SENSOR_V_TYPE_SWITCH,
    get_discovery_vtypes,
)
from rpieasy2.core.system_vars import resolve_controller_template
from rpieasy2.core import webserver as webserver_mod
from rpieasy2.core.config import get_config

logger = logging.getLogger("rpieasy2.controller.c005")


class C005HomeAssistantMQTT(ControllerBase):
    CONTROLLER_ID = 5
    CONTROLLER_NAME = "Home Assistant MQTT"
    CONTROLLER_HAS_MQTT = True
    usesMQTT = True
    usesTemplate = True
    usesAccount = True
    usesPassword = True
    usesExtCreds = True
    usesID = False
    defaultPort = 1883
    mqttAutoDiscover = True

    def __init__(self):
        super().__init__()
        self._client: aiomqtt.Client | None = None
        self._discovery_prefix = "homeassistant"
        self._availability_topic: str = ""
        self._online_msg: str = "online"
        self._offline_msg: str = "offline"
        self._ctrl_config: dict[str, Any] = {}
        # runtime sets to avoid repeated discovery publishes and to track last publish times
        self._published_discovery: set[str] = set()
        self._last_task_publish: dict[int, float] = {}
        self._last_published_values: dict[int, dict[str, str]] = {}
        self._can_set_tasks: set[int] = set()
        # map normalized state_topic -> (task_index, safe_value_name) for quick reverse-lookup
        self._state_topic_map: dict[str, tuple[int, str]] = {}
        # track which command topics we've already subscribed to to avoid subscribe spam
        self._subscribed_cmd_topics: set[str] = set()

    async def on_controller_init(self, event: Event) -> bool | None:
        config: dict[str, Any] = event.data.get("controller_config", {})
        self._ctrl_config = config
        # remember which controller slot we are (used for status reporting)
        try:
            self._controller_index = int(event.controller_index)
        except Exception:
            self._controller_index = None
        # Store config values and start a resilient background MQTT loop
        self._ctrl_config = config
        self._discovery_prefix = config.get("autodiscoverytopic", "homeassistant")
        self._online_msg = config.get("onlinemessage", "online")
        # lwtconnectmessage seemed misnamed in older config; fall back sensibly
        self._offline_msg = config.get("lwtdisconnectmessage", config.get("lwtdisconnectmessage", "offline")) or "offline"

        will_topic = config.get("controllerlwttopic", "")
        self._availability_topic = will_topic or (config.get("controllersubscribe", "").split("/")[0] or "rpieasy2") + "/status"

        # start background runner
        # build reverse mapping from configured tasks to state topics so UI updates
        # work even if discovery was already published earlier
        try:
            self._rebuild_state_topic_map()
        except Exception:
            logger.exception("Failed to rebuild state topic map at init")
        # start a memory-safe periodic cleanup task to prune stale mappings
        try:
            asyncio.create_task(self._state_topic_cleanup_loop())
            logger.debug("State topic map cleanup loop started")
        except Exception:
            logger.exception("Failed to start state topic map cleanup loop")
        # Clear the published discovery set on init to prevent unbounded growth
        self._published_discovery.clear()
        # start MQTT client loop only if enabled
        if config.get("controllerenabled", config.get("enabled", True)):
            asyncio.create_task(self._run_client_loop())
        else:
            logger.info("MQTT controller disabled, not starting client loop")
        return True

    def _rebuild_state_topic_map(self) -> None:
        """Reconstruct the mapping of state_topic -> (task_index, safe_value_name)
        based on configured tasks and the controller publish template. This is
        executed at controller startup so the controller can map incoming
        commands/state updates to tasks even if discovery was published earlier.
        """
        try:
            cfg = get_config()
            tasks = cfg.data.get("tasks", [])
            ctrl_config = self._ctrl_config or {}
            raw_template = ctrl_config.get("controllerpublish", ctrl_config.get("topic", "%sysname%/%tskname%/%valname%"))
            for ti, task in enumerate(tasks):
                # Only consider tasks assigned to this controller slot
                ctrl_idx = getattr(self, "_controller_index", None)
                if ctrl_idx is not None:
                    send_flag = task.get(f"TDSD{ctrl_idx}", False)
                    if not send_flag and task.get("TDSD") is not None:
                        send_flag = bool(task.get("TDSD"))
                else:
                    send_flag = False
                if not send_flag:
                    continue

                found_any = False
                # Try up to 4 value name slots (TDVN1..4) - matches on_controller_send behavior
                for vi in range(1, 5):
                    vname_local = task.get(f"TDVN{vi}")
                    if not vname_local:
                        continue
                    found_any = True
                    safe_vlocal = re.sub(r"[^A-Za-z0-9_\-]", "_", str(vname_local)).strip("_") or "value"
                    topic_local = raw_template.replace("%valname%", safe_vlocal)
                    try:
                        topic_local = resolve_controller_template(topic_local, task_index=ti, task_config=task, task_values={}, controller_config=ctrl_config)
                    except Exception:
                        pass
                    self._state_topic_map[topic_local.rstrip('/')] = (ti, safe_vlocal)

                # If no TDVN entries, create a sensible default mapping for single-value tasks
                if not found_any:
                    safe_vlocal = "value"
                    topic_local = raw_template.replace("%valname%", safe_vlocal)
                    try:
                        topic_local = resolve_controller_template(topic_local, task_index=ti, task_config=task, task_values={}, controller_config=ctrl_config)
                    except Exception:
                        pass
                    self._state_topic_map[topic_local.rstrip('/')] = (ti, safe_vlocal)
        except Exception:
            logger.exception("Error rebuilding state topic map")
        # Expose the mapping to the webserver runtime state so it can be inspected
        try:
            idx = getattr(self, "_controller_index", None)
            if idx is not None:
                webserver_mod.set_controller_runtime_state(idx, {
                    "mqtt_state_map": dict(self._state_topic_map),
                    "published_discovery": list(self._published_discovery),
                    "subscribed_cmd_topics": list(self._subscribed_cmd_topics),
                })
        except Exception:
            # Do not fail startup for inability to report runtime state
            logger.exception("Failed to publish controller runtime mapping")

    async def _state_topic_cleanup_loop(self) -> None:
        """Periodically cleanup invalid or stale state_topic mappings and last-publish records.

        Runs in the background to prevent growth of internal maps when tasks change.
        """
        while True:
            try:
                self._state_topic_cleanup()
            except Exception:
                logger.exception("State topic map cleanup failed")
            # run cleanup every 60 seconds; adjust as needed
            await asyncio.sleep(60)

    def _state_topic_cleanup(self) -> None:
        """Remove invalid entries from _state_topic_map and prune _last_task_publish.

        - Drop topics whose task_index is out of range or task disabled for this controller.
        - Cap the map size to prevent memory growth.
        - Remove _last_task_publish entries for tasks that no longer exist or are disabled.
        """
        try:
            cfg = get_config()
            tasks = cfg.data.get("tasks", [])
            ctrl_idx = getattr(self, "_controller_index", None)
            to_remove: list[str] = []
            for topic, (ti, _vname) in list(self._state_topic_map.items()):
                # invalid index
                if ti is None or ti < 0 or ti >= len(tasks):
                    to_remove.append(topic)
                    continue
                # ensure task is enabled for this controller
                task = tasks[ti]
                send_flag = False
                if ctrl_idx is not None:
                    send_flag = task.get(f"TDSD{ctrl_idx}", False)
                    if not send_flag and task.get("TDSD") is not None:
                        send_flag = bool(task.get("TDSD"))
                if not send_flag:
                    to_remove.append(topic)
            for t in to_remove:
                self._state_topic_map.pop(t, None)
            # simple size limit to avoid memory blowups
            MAX_MAP_SIZE = 2000
            if len(self._state_topic_map) > MAX_MAP_SIZE:
                for k in list(self._state_topic_map.keys())[: len(self._state_topic_map) - MAX_MAP_SIZE]:
                    del self._state_topic_map[k]
                logger.info("State topic map pruned to limit (%d entries)", MAX_MAP_SIZE)

            # prune _last_task_publish: remove entries for tasks that no longer exist or are disabled
            stale_task_idxs: list[int] = []
            for ti in list(self._last_task_publish.keys()):
                if ti is None or ti < 0 or ti >= len(tasks):
                    stale_task_idxs.append(ti)
                    continue
                task = tasks[ti]
                send_flag = False
                if ctrl_idx is not None:
                    send_flag = task.get(f"TDSD{ctrl_idx}", False)
                    if not send_flag and task.get("TDSD") is not None:
                        send_flag = bool(task.get("TDSD"))
                if not send_flag:
                    stale_task_idxs.append(ti)
            for ti in stale_task_idxs:
                self._last_task_publish.pop(ti, None)
            if stale_task_idxs:
                logger.debug("Pruned _last_task_publish entries: %s", stale_task_idxs)
        except Exception:
            logger.exception("Error during state topic map cleanup")

    async def _run_client_loop(self) -> None:
        backoff = 1
        while True:
            config = self._ctrl_config or {}
            if not config.get("controllerenabled", config.get("enabled", True)):
                await asyncio.sleep(5)
                continue
            try:
                host = config.get("controllerip", config.get("host", "127.0.0.1"))
                port = int(config.get("controllerport", config.get("port", DEFAULT_MQTT_PORT)))
                user = config.get("controlleruser", config.get("username", "")) or None
                password = config.get("controllerpassword", config.get("password", "")) or None

                will = None
                will_msg = config.get("lwtdisconnectmessage", "offline") or "offline"
                will_retain = config.get("willretain", False)
                if config.get("sendlwttobroker", False) and self._availability_topic:
                    try:
                        resolved_will_topic = resolve_controller_template(self._availability_topic, controller_config=config)
                    except Exception:
                        resolved_will_topic = self._availability_topic
                    will = aiomqtt.Will(topic=resolved_will_topic, payload=will_msg, retain=will_retain)

                logger.info("Connecting to MQTT %s:%s", host, port)
                # aiomqtt.Client is used as an async context manager
                try:
                    async with aiomqtt.Client(hostname=host, port=port, username=user, password=password, will=will) as client:
                        self._client = client

                        # subscribe discovery control topics
                        try:
                            await client.subscribe(f"{self._discovery_prefix}/+/+/set")
                            if config.get("discoverytriggertopic"):
                                await client.subscribe(config["discoverytriggertopic"])
                        except Exception:
                            logger.exception("Failed to subscribe discovery/control topics")

                        # publish online availability
                        try:
                            await self._publish_availability(True)
                            # update runtime connection state (do not persist to saved config)
                            try:
                                idx = getattr(self, "_controller_index", None)
                                if idx is not None:
                                    webserver_mod.set_controller_runtime_state(idx, {"connected": True, "last_error": ""})
                            except Exception:
                                logger.exception("Failed to update runtime controller connected state")
                        except Exception:
                            logger.exception("Failed to publish availability after connect")

                        # reset backoff after successful connect
                        backoff = 20

                        # trigger autodiscovery for all tasks now that MQTT is connected
                        try:
                            await self._trigger_all_autodiscovery()
                        except Exception:
                            logger.exception("Error triggering autodiscovery after connect")

                        # start listening loop - blocks until connection broken
                        await self._listen()
                finally:
                    # ensure self._client is cleared when context exits
                    # clear client and mark disconnected
                    try:
                        idx = getattr(self, "_controller_index", None)
                        if idx is not None:
                            webserver_mod.set_controller_runtime_state(idx, {"connected": False, "last_error": ""})
                    except Exception:
                        logger.exception("Failed to update runtime controller disconnected state")
                    self._client = None
            except Exception as e:
                # Log MqttError without full traceback if it's a common connection issue
                if isinstance(e, aiomqtt.MqttError):
                    logger.error("MQTT connection error: %s", e)
                else:
                    logger.exception("MQTT client loop encountered an error, will retry")
                try:
                    idx = getattr(self, "_controller_index", None)
                    if idx is not None:
                        webserver_mod.set_controller_runtime_state(idx, {"connected": False, "last_error": str(e)})
                except Exception:
                    pass
            finally:
                # cleanup client
                try:
                    if self._client:
                        await self._client.disconnect()
                except Exception:
                    pass
                self._client = None

            # Exponential backoff with cap
            await asyncio.sleep(min(backoff, 30))
            backoff = min(backoff * 2, 30)

    async def _publish_availability(self, online: bool) -> None:
        if not self._client or not self._availability_topic:
            return
        try:
            msg = self._online_msg if online else self._offline_msg
            # resolve system/controller templates in availability topic before publishing
            try:
                topic = resolve_controller_template(self._availability_topic, controller_config=self._ctrl_config)
            except Exception:
                topic = self._availability_topic
            await self._client.publish(topic, msg, retain=True)
        except Exception as e:
            logger.error(f"HA MQTT availability publish failed: {e}")

    async def _listen(self) -> None:
        try:
            # messages is an async iterator; iterate directly
            async for msg in self._client.messages:
                topic = msg.topic.value
                payload = msg.payload.decode().strip()
                # discovery trigger handling: if trigger topic received with 'online', republish availability
                if self._ctrl_config.get("discoverytriggertopic") and topic == self._ctrl_config["discoverytriggertopic"]:
                    logger.debug("Discovery trigger received on %s: %r", topic, payload)
                    if payload.lower() == "online":
                        await self._publish_availability(True)
                    continue

                logger.debug("PLUGIN_WRITE received on %s: %r", topic, payload)

                # try to parse JSON payload for convenience
                parsed = {}
                try:
                    parsed = json.loads(payload)
                    # Only accept parsed_json if it's a mapping; other JSON types (number, string)
                    # would confuse code that expects a dict. Normalize non-dict to empty dict.
                    if not isinstance(parsed, dict):
                        parsed = {}
                except Exception:
                    parsed = {}

                # Validate payload and potentially parsed JSON structure
                if not payload or (parsed and not parsed): # Empty payload or empty parsed JSON
                    logger.warning("Received empty payload or parsed JSON for topic %s", topic)
                    continue

                # Determine task index for this topic (if we have a mapping) so
                # plugins receive task_index and can update stored task values.
                task_idx_for_ev = None
                try:
                    norm = topic.rstrip('/')
                    mapping = self._state_topic_map.get(norm)
                    if not mapping and topic.endswith('/set'):
                        mapping = self._state_topic_map.get(topic[:-4].rstrip('/'))
                    if mapping:
                        task_idx_for_ev = mapping[0]
                except Exception:
                    task_idx_for_ev = None

                # Plugins historically expect the payload string in string1 and topic in string2
                if task_idx_for_ev is not None:
                    logger.info("PLUGIN_WRITE mapped to task_index=%s", task_idx_for_ev)
                    ev = Event(type="PLUGIN_WRITE", string1=payload, string2=topic, parsed_json=parsed, task_index=task_idx_for_ev)
                else:
                    logger.info("PLUGIN_WRITE has no task mapping for topic %s", topic)
                    ev = Event(type="PLUGIN_WRITE", string1=payload, string2=topic, parsed_json=parsed)
                await get_event_bus().publish(ev)

                # After plugin write handlers run, they may populate ev.data["values"] or
                # ev.data["named_values"] to indicate resulting state(s). If present,
                # publish those values back to MQTT so Home Assistant sees the updated state.
                try:
                    values = ev.data.get("values") or {}
                    named_values = ev.data.get("named_values") or {}
                    # prefer named_values if provided, fall back to values
                    publish_map = named_values if named_values else values
                    # determine base state_topic for publishing (useful also for fallback)
                    if topic.endswith("/set"):
                        state_topic = topic[:-4].rstrip('/')
                    else:
                        state_topic = topic

                    if publish_map:
                        # If this message was a command_topic (ends with '/set'), the
                        # corresponding state topic is the same without the trailing '/set'.
                        if topic.endswith("/set"):
                            # strip the trailing '/set' and any leftover slash so the
                            # resulting state_topic matches discovery state_topic
                            state_topic = topic[:-4].rstrip('/')
                        else:
                            state_topic = topic

                        # If the publish_map contains a single entry, and its name matches
                        # the last segment of state_topic, publish directly to that topic.
                        if len(publish_map) == 1:
                            name, val = next(iter(publish_map.items()))
                            # sanitize name to match how discovery built topics
                            safe_name = re.sub(r"[^A-Za-z0-9_\-]", "_", str(name)).strip("_") or "value"
                            last_seg = state_topic.split("/")[-1]
                            if safe_name == last_seg:
                                logger.info("Publishing %s -> %s", state_topic, val)
                                try:
                                    await self._client.publish(state_topic, str(val))
                                except Exception as e:
                                    logger.error(f"HA MQTT post-write publish failed: {e}")
                            else:
                                # publish to state_topic as-is for single unnamed value
                                logger.info("Publishing %s -> %s", state_topic, val)
                                try:
                                    await self._client.publish(state_topic, str(val))
                                except Exception as e:
                                    logger.error(f"HA MQTT post-write publish failed: {e}")
                        else:
                            # multiple values: publish each under the same base path
                            base = state_topic.rsplit('/', 1)[0]
                            for name, val in publish_map.items():
                                safe_name = re.sub(r"[^A-Za-z0-9_\-]", "_", str(name)).strip("_") or "value"
                                dest = f"{base}/{safe_name}"
                                logger.info("Publishing %s -> %s", dest, val)
                                try:
                                    await self._client.publish(dest, str(val))
                                except Exception as e:
                                    logger.error(f"HA MQTT post-write publish failed for {dest}: {e}")

                        # Try to update webserver runtime task values so the /devices UI reflects the change.
                        try:
                            # prefer a direct reverse-lookup mapping built at discovery time
                            norm_state = state_topic.rstrip('/')
                            mapping = self._state_topic_map.get(norm_state)
                            # if we got a command topic, also try the equivalent state topic
                            if not mapping and state_topic.endswith('/set'):
                                mapping = self._state_topic_map.get(state_topic[:-4].rstrip('/'))
                            if mapping:
                                ti, _ = mapping
                                cfg = get_config()
                                tasks = cfg.data.get("tasks", [])
                                if 0 <= ti < len(tasks):
                                    task = tasks[ti]
                                    # publish a PLUGIN_READ to update UI (already ran above, but ensure event posted)
                                    read_ev2 = Event(type="PLUGIN_READ", task_index=ti, data={"values": publish_map, "task_config": task})
                                    await get_event_bus().publish(read_ev2)
                        except Exception:
                            logger.exception("Failed to update webserver task values after PLUGIN_WRITE")
                    else:
                        # Plugin did not return explicit values. Try to perform a PLUGIN_READ
                        # for this task so the plugin can report its stored/current values
                        # immediately. This avoids waiting for the task interval.
                        try:
                            read_vals = {}
                            read_named = {}
                            # prefer task_index from the PLUGIN_WRITE event if present
                            read_task_idx = ev.task_index if getattr(ev, 'task_index', -1) is not None and ev.task_index >= 0 else None
                            if read_task_idx is None:
                                # fall back to mapping lookup
                                read_task_idx = None
                                try:
                                    norm = topic.rstrip('/')
                                    mapping = self._state_topic_map.get(norm)
                                    if not mapping and topic.endswith('/set'):
                                        mapping = self._state_topic_map.get(topic[:-4].rstrip('/'))
                                    if mapping:
                                        read_task_idx = mapping[0]
                                except Exception:
                                    read_task_idx = None

                            if read_task_idx is not None:
                                logger.debug("Performing fallback PLUGIN_READ for task %s", read_task_idx)
                                read_ev = Event(type="PLUGIN_READ", task_index=read_task_idx, data={})
                                await get_event_bus().publish(read_ev)
                                read_named = read_ev.data.get("named_values") or {}
                                read_vals = read_ev.data.get("values") or {}
                            # choose read result if available
                            result_map = read_named if read_named else read_vals
                            if result_map:
                                logger.info("PLUGIN_READ returned values for task %s: %s", read_task_idx, result_map)
                                # publish results similarly to normal publish_map branch
                                if len(result_map) == 1:
                                    name, val = next(iter(result_map.items()))
                                    safe_name = re.sub(r"[^A-Za-z0-9_\-]", "_", str(name)).strip("_") or "value"
                                    last_seg = state_topic.split("/")[-1]
                                    if safe_name == last_seg:
                                        logger.info("Publishing %s -> %s", state_topic, val)
                                        try:
                                            await self._client.publish(state_topic, str(val))
                                        except Exception as e:
                                            logger.error(f"HA MQTT post-write fallback publish (single value) failed: {e}")
                                    else:
                                        # publish to state_topic as-is for single unnamed value
                                        logger.info("Publishing %s -> %s", state_topic, val)
                                        try:
                                            await self._client.publish(state_topic, str(val))
                                        except Exception as e:
                                            logger.error(f"HA MQTT post-write fallback publish (single value, mismatched name) failed: {e}")
                                else:
                                    base = state_topic.rsplit('/', 1)[0]
                                    for name, val in result_map.items():
                                        safe_name = re.sub(r"[^A-Za-z0-9_\-]", "_", str(name)).strip("_") or "value"
                                        dest = f"{base}/{safe_name}"
                                        logger.debug("Publishing %s -> %s", dest, val)
                                        try:
                                            await self._client.publish(dest, str(val))
                                        except Exception as e:
                                            logger.error(f"HA MQTT post-write fallback publish (multi-value) failed for {dest}: {e}")
                                # also try to update webserver via PLUGIN_READ event if mapping exists
                                try:
                                    norm_state = state_topic.rstrip('/')
                                    mapping = self._state_topic_map.get(norm_state)
                                    if not mapping and state_topic.endswith('/set'):
                                        mapping = self._state_topic_map.get(state_topic[:-4].rstrip('/'))
                                    if mapping:
                                        ti, _ = mapping
                                        cfg = get_config()
                                        tasks = cfg.data.get("tasks", [])
                                        if 0 <= ti < len(tasks):
                                            task = tasks[ti]
                                            # publish a PLUGIN_READ to update UI (already ran above, but ensure event posted)
                                            read_ev2 = Event(type="PLUGIN_READ", task_index=ti, data={"values": result_map, "task_config": task})
                                            await get_event_bus().publish(read_ev2)
                                except Exception:
                                    logger.exception("Failed to update webserver task values after fallback PLUGIN_READ")
                            else:
                                # No read results either; fallback to publishing payload raw
                                logger.debug("No values from PLUGIN_WRITE or PLUGIN_READ; publishing original payload to %s", state_topic)
                                try:
                                    await self._client.publish(state_topic, payload)
                                except Exception as e:
                                    logger.error(f"HA MQTT post-write fallback publish (raw payload) failed: {e}")
                        except Exception:
                            logger.exception("Failed to handle fallback PLUGIN_READ after PLUGIN_WRITE")
                except Exception:
                    logger.exception("Failed to publish state after PLUGIN_WRITE")
        except Exception as e:
            logger.error(f"HA MQTT listener error: {e}")

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
                    logger.error(f"HA MQTT rule publish failed: {e}")
                    return False
            return False

        config: dict[str, Any] = event.data.get("task_config", {})
        values: dict[str, Any] = event.data.get("values", {})
        ctrl_config = values.get("controller", {}) or self._ctrl_config
        task_name = config.get("TDN", config.get("name", "rpieasy2_task"))
        unit = config.get("unit", 1)

        raw_template = ctrl_config.get("controllerpublish", ctrl_config.get("topic", "%sysname%/%tskname%/%valname%"))
        named_values = values.get("named_values", values)
        value_names = values.get("value_names", list(named_values.keys()))

        # prefer task output type (TDNUM_out) or TDSF for discovery vtypes
        sensor_type = int(config.get("TDSF", config.get("TDNUM_out", 0)) or 0)
        device_properties = config.get("device_properties", {}) or {}

        # If task config does not declare a sensor type, try to infer from plugin metadata
        if not sensor_type:
            try:
                pid = int(config.get("plugin_id", config.get("plugin", 0)) or 0)
                if pid:
                    # plugin info cache is maintained by webserver; import lazily
                    from rpieasy2.core.webserver import _plugin_info as _PLUGIN_INFO
                    entry = _PLUGIN_INFO.get(pid)
                    if entry and entry.get("device_properties"):
                        device_properties = entry.get("device_properties") or {}
                        sensor_type = int(device_properties.get("vtype", 0) or 0)
            except Exception:
                pass

        has_state_class = device_properties.get("mqtt_state_class", False)

        # Get per-value discovery vtypes from the plugin via PLUGIN_GET_DISCOVERY_VTYPES event
        plugin_vtypes = None
        can_set_values: set[int] = set()
        try:
            disc_ev = Event(
                type="PLUGIN_GET_DISCOVERY_VTYPES",
                task_index=event.task_index,
                sensor_type=sensor_type,
                data={"num_values": len(value_names)}
            )
            await get_event_bus().publish(disc_ev)
            raw_vtypes = disc_ev.data.get("vtypes")
            if raw_vtypes and len(raw_vtypes) >= len(value_names):
                plugin_vtypes = []
                for vi, raw_vt in enumerate(raw_vtypes):
                    if raw_vt & SENSOR_V_TYPE_CAN_SET:
                        can_set_values.add(vi)
                        raw_vt &= ~SENSOR_V_TYPE_CAN_SET
                    plugin_vtypes.append(raw_vt)
        except Exception:
            pass
        if can_set_values:
            try:
                self._can_set_tasks.add(int(event.task_index))
            except Exception:
                pass

        if plugin_vtypes:
            vtypes = plugin_vtypes
        else:
            # Fall back to default expansion from base sensor type
            vtypes = get_discovery_vtypes(sensor_type, len(value_names))

        uid_base = hashlib.md5(f"{task_name}{unit}".encode()).hexdigest()[:8]
        _cfg = get_config()
        sysname = _cfg.data.get("system", {}).get("sysname") or _cfg.data.get("system", {}).get("name") or f"RPIEasy_{unit}"
        safe_sysname = re.sub(r"[^A-Za-z0-9_\-]", "_", str(sysname)).strip("_") or "rpieasy"
        safe_taskname = re.sub(r"[^A-Za-z0-9_\-]", "_", str(task_name)).strip("_") or "task"
        device_id = f"rpieasy2_{safe_sysname}_{safe_taskname}"
        device_name = f"{sysname} {task_name}"

        try:
            autodiscovery_enabled = ctrl_config.get("enableautodiscovery", True)
            name_override = ctrl_config.get("name_override", "")

            # determine if this task's values are allowed to be published now (respect task interval)
            import time as _time
            try:
                task_idx = int(event.task_index) if getattr(event, 'task_index', None) is not None else -1
            except Exception:
                task_idx = -1
            allowed_publish = True
            try:
                if task_idx >= 0:
                    task_interval = config.get("TDT")
                    if task_interval is None:
                        task_interval = config.get("interval")
                    # check if value actually changed since last publish
                    values_changed = False
                    if task_idx >= 0:
                        last_vals = self._last_published_values.get(task_idx, {})
                        for vname, vval in named_values.items():
                            if str(last_vals.get(vname, "")) != str(vval):
                                values_changed = True
                                break
                    if task_interval and not values_changed:
                        last = self._last_task_publish.get(task_idx, 0.0)
                        if (_time.time() - last) < float(task_interval):
                            allowed_publish = False
                        else:
                            self._last_task_publish[task_idx] = _time.time()
                    elif task_interval and values_changed:
                        self._last_task_publish[task_idx] = _time.time()
            except Exception:
                allowed_publish = True

            # if this controller slot is not selected for the task, skip publishing for it
            ctrl_idx = getattr(self, "_controller_index", None)
            if ctrl_idx is not None:
                send_flag = config.get(f"TDSD{ctrl_idx}", False)
                # also accept older style enabled flag mapping
                if not send_flag and config.get("TDSD") is not None:
                    send_flag = bool(config.get("TDSD"))
            else:
                # controller index unknown (shouldn't normally happen) - do not assume enabled
                send_flag = False
            if not send_flag:
                return True

            for i, vname in enumerate(value_names):
                vval = named_values.get(vname, "")

                # check per-value value type override (TDTV1..4)
                vtype_ovr = config.get(f"TDTV{i+1}", 0)
                if vtype_ovr and int(vtype_ovr) > 0:
                    vtypes_ovr = get_discovery_vtypes(int(vtype_ovr), 1)
                    vtype = vtypes_ovr[0] if vtypes_ovr else 1
                else:
                    vtype = vtypes[i] if i < len(vtypes) else 1

                if autodiscovery_enabled:
                    import rpieasy2.core.ha_discovery as discovery_mod
                    ha_component = discovery_mod.get_ha_component(vtype)
                    ha_device_class = discovery_mod.get_ha_device_class(vtype)
                    ha_unit = discovery_mod.get_ha_unit(vtype)

                    # Values flagged with CAN_SET get a command_topic
                    if i in can_set_values:
                        ha_component = "switch"
                    elif vtype == SENSOR_V_TYPE_SWITCH:
                        plugin_id = int(config.get("plugin_id", config.get("plugin", 0)) or 0)
                        if plugin_id:
                            webserver_mod._lazy_load_plugin(plugin_id)
                            plugin_entry = webserver_mod._plugin_info.get(plugin_id)
                            if plugin_entry and plugin_entry.get("class"):
                                plugin_cls = plugin_entry["class"]
                                if hasattr(plugin_cls, 'on_plugin_write'):
                                    ha_component = "switch"
                else:
                    ha_component = "sensor"
                    ha_device_class = None
                    ha_unit = None

                # check per-value unit of measure override (TUOM1..4)
                uom_ovr = config.get(f"TUOM{i+1}", 0)
                if uom_ovr and int(uom_ovr) > 0:
                    if autodiscovery_enabled:
                        import rpieasy2.core.ha_discovery as discovery_mod
                        uom_unit = discovery_mod.get_uom_unit(int(uom_ovr))
                        if uom_unit:
                            ha_unit = uom_unit

                # sanitize value name for topics / unique ids: replace spaces and unsafe chars
                if autodiscovery_enabled:
                    import rpieasy2.core.ha_discovery as discovery_mod
                    safe_vname = discovery_mod.sanitize_name(vname)
                else:
                    safe_vname = re.sub(r"[^A-Za-z0-9_\-]", "_", str(vname)).strip("_") or "value"

                topic = raw_template.replace("%valname%", safe_vname)
                topic = resolve_controller_template(topic, task_index=event.task_index,
                                                     task_config=config, task_values=values,
                                                     controller_config=ctrl_config)

                uid = f"rpieasy2_{uid_base}_{safe_vname}"
                entity_name = f"{task_name} {vname}".strip()

                if autodiscovery_enabled:
                    import rpieasy2.core.ha_discovery as discovery_mod
                    await discovery_mod.publish_discovery(
                        controller=self,
                        event=event,
                        config=config,
                        values=values,
                        ctrl_config=ctrl_config,
                        task_idx=task_idx,
                        vname=vname,
                        vtype=vtype,
                        safe_vname=safe_vname,
                        topic=topic,
                        uid=uid,
                        entity_name=entity_name,
                        unit=unit,
                        device_id=device_id,
                        device_name=device_name,
                        ha_component=ha_component,
                        ha_device_class=ha_device_class,
                        ha_unit=ha_unit,
                        has_state_class=has_state_class,
                    )

                if allowed_publish:
                    try:
                        await self._client.publish(topic, str(vval))
                        if task_idx >= 0:
                            self._last_published_values.setdefault(task_idx, {})[vname] = str(vval)
                    except Exception as e:
                        logger.error(f"HA MQTT value publish failed: {e}")
            return True
        except Exception as e:
            logger.error(f"HA MQTT publish failed: {e}")
            return False

    async def _trigger_all_autodiscovery(self) -> None:
        """Triggers autodiscovery for all currently enabled tasks.

        Called after a successful MQTT connection to ensure discovery messages
        are sent with correct settings (e.g. command_topic for CAN_SET plugins).
        Clears the published-discovery cache and triggers PLUGIN_READ for each
        enabled task so the normal value publish flow re-publishes discovery.
        """
        cfg = get_config()
        tasks = cfg.data.get("tasks", [])
        ctrl_idx = getattr(self, "_controller_index", None)
        if ctrl_idx is None:
            logger.warning("Cannot trigger autodiscovery: controller index not set.")
            return

        self._published_discovery.clear()
        for ti, task in enumerate(tasks):
            enabled = task.get("TDE", task.get("enabled", True))
            if not enabled:
                continue
            send_flag = task.get(f"TDSD{ctrl_idx}", False)
            if not send_flag and task.get("TDSD") is not None:
                send_flag = bool(task.get("TDSD"))
            if not send_flag:
                continue
            # Trigger a PLUGIN_READ so the normal flow re-publishes discovery
            await get_event_bus().publish(Event(type="PLUGIN_READ", task_index=ti))
        logger.debug("Autodiscovery triggered for all enabled tasks (%d tasks).", len(tasks))

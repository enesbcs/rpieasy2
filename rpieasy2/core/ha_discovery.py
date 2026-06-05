from __future__ import annotations

import json
import logging
import re
from typing import Any

from rpieasy2.core.rpiconst import (
    SENSOR_V_TYPE_AQI,
    SENSOR_V_TYPE_CO2,
    SENSOR_V_TYPE_CURRENT,
    SENSOR_V_TYPE_DIRECTION,
    SENSOR_V_TYPE_DISTANCE,
    SENSOR_V_TYPE_DURATION,
    SENSOR_V_TYPE_ENERGY,
    SENSOR_V_TYPE_ENERGY_DISTANCE,
    SENSOR_V_TYPE_FACTOR,
    SENSOR_V_TYPE_GAS,
    SENSOR_V_TYPE_HUM,
    SENSOR_V_TYPE_ILLUMINANCE,
    SENSOR_V_TYPE_KWH,
    SENSOR_V_TYPE_LEVEL,
    SENSOR_V_TYPE_NOX,
    SENSOR_V_TYPE_PM1_0,
    SENSOR_V_TYPE_PM2_5,
    SENSOR_V_TYPE_PM10,
    SENSOR_V_TYPE_PRESSURE,
    SENSOR_V_TYPE_RAIN,
    SENSOR_V_TYPE_SIGNAL,
    SENSOR_V_TYPE_SPEED,
    SENSOR_V_TYPE_SWITCH,
    SENSOR_V_TYPE_TEMP,
    SENSOR_V_TYPE_UV,
    SENSOR_V_TYPE_VA,
    SENSOR_V_TYPE_VAR,
    SENSOR_V_TYPE_VOLTAGE,
    SENSOR_V_TYPE_WATT,
    SENSOR_V_TYPE_WATER_FLOW,
    SENSOR_V_TYPE_WEIGHT,
    SENSOR_V_TYPE_BARO,
    SENSOR_V_TYPE_WIND,
    UOM_DEGC, UOM_DEGF, UOM_K, UOM_PERCENT, UOM_HPA, UOM_BAR, UOM_V, UOM_W, UOM_KW,
    UOM_KWH, UOM_A, UOM_VA, UOM_LUX, UOM_UV_INDEX, UOM_PPM, UOM_KMH, UOM_MPH, UOM_HZ,
    UOM_B, UOM_KB, UOM_MB, UOM_GB, UOM_TB,
)

logger = logging.getLogger("rpieasy2.ha_discovery")

_VTYPE_TO_HA: dict[int, dict[str, Any]] = {
    SENSOR_V_TYPE_TEMP: {"device_class": "temperature", "unit": "\u00b0C"},
    SENSOR_V_TYPE_HUM: {"device_class": "humidity", "unit": "%"},
    SENSOR_V_TYPE_PRESSURE: {"device_class": "pressure", "unit": "hPa"},
    SENSOR_V_TYPE_BARO: {"device_class": "pressure", "unit": "hPa"},
    SENSOR_V_TYPE_ILLUMINANCE: {"device_class": "illuminance", "unit": "lx"},
    SENSOR_V_TYPE_CO2: {"device_class": "carbon_dioxide", "unit": "ppm"},
    SENSOR_V_TYPE_DISTANCE: {"device_class": "distance", "unit": "cm"},
    SENSOR_V_TYPE_SPEED: {"device_class": "speed", "unit": "km/h"},
    SENSOR_V_TYPE_WIND: {"device_class": "wind_speed", "unit": "m/s"},
    SENSOR_V_TYPE_RAIN: {"device_class": "precipitation", "unit": "mm"},
    SENSOR_V_TYPE_DIRECTION: {"device_class": "wind_direction", "unit": "\u00b0"},
    SENSOR_V_TYPE_LEVEL: {"device_class": "power"},
    SENSOR_V_TYPE_WATT: {"device_class": "power", "unit": "W"},
    SENSOR_V_TYPE_KWH: {"device_class": "energy", "unit": "kWh"},
    SENSOR_V_TYPE_ENERGY: {"device_class": "energy", "unit": "kWh"},
    SENSOR_V_TYPE_VOLTAGE: {"device_class": "voltage", "unit": "V"},
    SENSOR_V_TYPE_CURRENT: {"device_class": "current", "unit": "A"},
    SENSOR_V_TYPE_VA: {"device_class": "apparent_power", "unit": "VA"},
    SENSOR_V_TYPE_VAR: {"device_class": "reactive_power", "unit": "var"},
    SENSOR_V_TYPE_FACTOR: {"device_class": "power_factor", "unit": "%"},
    SENSOR_V_TYPE_GAS: {"device_class": "gas"},
    SENSOR_V_TYPE_UV: {"device_class": "illuminance", "unit": "UV index"},
    SENSOR_V_TYPE_SWITCH: {"component": "switch"},
    SENSOR_V_TYPE_WEIGHT: {"device_class": "weight", "unit": "kg"},
    SENSOR_V_TYPE_PM1_0: {"device_class": "pm1", "unit": "\u00b5g/m\u00b3"},
    SENSOR_V_TYPE_PM2_5: {"device_class": "pm25", "unit": "\u00b5g/m\u00b3"},
    SENSOR_V_TYPE_PM10: {"device_class": "pm10", "unit": "\u00b5g/m\u00b3"},
    SENSOR_V_TYPE_AQI: {"device_class": "aqi"},
    SENSOR_V_TYPE_NOX: {"device_class": "nitrogen_dioxide", "unit": "\u00b5g/m\u00b3"},
    SENSOR_V_TYPE_WATER_FLOW: {"device_class": "volume_flow_rate", "unit": "m\u00b3/h"},
    SENSOR_V_TYPE_ENERGY_DISTANCE: {"device_class": "energy_distance", "unit": "kWh/100km"},
    SENSOR_V_TYPE_SIGNAL: {"device_class": "signal_strength", "unit": "dBm"},
    SENSOR_V_TYPE_DURATION: {"device_class": "duration"},
}

_VTYPE_DEFAULT: dict[str, Any] = {"device_class": None, "unit": None}

_UOM_MAP: dict[int, str] = {
    UOM_DEGC: "\u00b0C", UOM_DEGF: "\u00b0F", UOM_K: "K", UOM_PERCENT: "%",
    UOM_HPA: "hPa", UOM_BAR: "bar", UOM_V: "V", UOM_W: "W", UOM_KW: "kW",
    UOM_KWH: "kWh", UOM_A: "A", UOM_VA: "VA", UOM_LUX: "lx",
    UOM_UV_INDEX: "UV index", UOM_PPM: "ppm", UOM_KMH: "km/h",
    UOM_MPH: "mph", UOM_HZ: "Hz",
    UOM_B: "B", UOM_KB: "kB", UOM_MB: "MB", UOM_GB: "GB", UOM_TB: "TB",
}


def get_ha_component(vtype: int) -> str:
    info = _VTYPE_TO_HA.get(vtype, _VTYPE_DEFAULT)
    return info.get("component", "sensor")


def get_ha_device_class(vtype: int) -> str | None:
    info = _VTYPE_TO_HA.get(vtype, _VTYPE_DEFAULT)
    return info.get("device_class")


def get_ha_unit(vtype: int) -> str | None:
    info = _VTYPE_TO_HA.get(vtype, _VTYPE_DEFAULT)
    return info.get("unit")


def get_uom_unit(uom: int) -> str | None:
    return _UOM_MAP.get(uom)


def sanitize_name(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_\-]", "_", str(name)).strip("_")
    return safe or "value"


async def publish_discovery(
    controller: Any,
    event: Any,
    config: dict[str, Any],
    values: dict[str, Any],
    ctrl_config: dict[str, Any],
    task_idx: int,
    vname: str,
    vtype: int,
    safe_vname: str,
    topic: str,
    uid: str,
    entity_name: str,
    unit: int,
    device_id: str,
    device_name: str,
    ha_component: str,
    ha_device_class: str | None,
    ha_unit: str | None,
    has_state_class: bool,
) -> None:
    """Constructs the MQTT discovery payload and publishes it under the proper topic."""
    from rpieasy2.core.system_vars import resolve_controller_template
    from rpieasy2.core.config import get_config
    from rpieasy2.core import webserver as webserver_mod

    if uid in controller._published_discovery:
        return

    try:
        resolved_avail = resolve_controller_template(controller._availability_topic, controller_config=ctrl_config)
    except Exception:
        resolved_avail = controller._availability_topic

    from rpieasy2.core.rpiconst import BUILD, build_to_date_str

    dp: dict[str, Any] = {
        "name": entity_name,
        "~": topic,
        "state_topic": "~",
        "unique_id": uid,
        "availability_topic": resolved_avail,
        "payload_available": controller._online_msg,
        "payload_not_available": controller._offline_msg,
        "device": {
            "identifiers": [device_id],
            "name": device_name,
            "manufacturer": "RPIEasy",
            "model": "Raspberry Pi Sensor",
            "sw_version": build_to_date_str(BUILD),
        },
    }

    if ha_device_class:
        dp["device_class"] = ha_device_class
    if ha_unit:
        dp["unit_of_measurement"] = ha_unit

    if ha_component == "switch":
        cmd_topic = topic.rstrip('/') + '/set'
        dp["command_topic"] = cmd_topic
        dp["payload_on"] = "1"
        dp["payload_off"] = "0"
        try:
            if cmd_topic not in controller._subscribed_cmd_topics:
                await controller._client.subscribe(cmd_topic)
                controller._subscribed_cmd_topics.add(cmd_topic)
                logger.debug("Subscribed to command topic: %s", cmd_topic)
                try:
                    idx = getattr(controller, "_controller_index", None)
                    if idx is not None:
                        webserver_mod.set_controller_runtime_state(idx, {
                            "mqtt_state_map": dict(controller._state_topic_map),
                            "published_discovery": list(controller._published_discovery),
                            "subscribed_cmd_topics": list(controller._subscribed_cmd_topics),
                        })
                except Exception:
                    logger.exception("Failed to publish controller runtime mapping after subscribe")
        except Exception as e:
            logger.error(f"Failed to subscribe command topic {cmd_topic}: {e}")

    if has_state_class:
        dp["state_class"] = "measurement"

    discovery_template = ctrl_config.get("autodiscoverytopic", "homeassistant/%devclass%/%unique_id%")
    discovery_topic = discovery_template.replace("%unique_id%", uid)
    discovery_topic = discovery_topic.replace("%devclass%", ha_component)
    discovery_topic = discovery_topic.replace("%component%", ha_component)
    configsuffix = ctrl_config.get("configsuffix", "/config") or "/config"
    if not discovery_topic.endswith(configsuffix):
        discovery_topic = discovery_topic.rstrip('/') + configsuffix
    discovery_topic = resolve_controller_template(discovery_topic, task_index=event.task_index,
                                                   task_config=config, task_values=values,
                                                   controller_config=ctrl_config)

    try:
        retain_discovery = ctrl_config.get("retaindiscovery", False)
        await controller._client.publish(discovery_topic, json.dumps(dp), retain=retain_discovery)
        controller._published_discovery.add(uid)
        try:
            if task_idx >= 0:
                controller._state_topic_map[topic.rstrip('/')] = (task_idx, safe_vname)
        except Exception:
            pass
        try:
            idx = getattr(controller, "_controller_index", None)
            if idx is not None:
                webserver_mod.set_controller_runtime_state(idx, {
                    "mqtt_state_map": dict(controller._state_topic_map),
                    "published_discovery": list(controller._published_discovery),
                    "subscribed_cmd_topics": list(controller._subscribed_cmd_topics),
                })
        except Exception:
            logger.exception("Failed to publish controller runtime mapping after discovery publish")
    except Exception as e:
        logger.error(f"HA MQTT discovery publish failed: {e}")

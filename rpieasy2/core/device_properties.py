from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum, IntFlag
from typing import Any


class OutputDataType(IntEnum):
    DEFAULT = 0
    SIMPLE = 1
    ALL = 2


class DeviceFlag(IntFlag):
    NONE = 0
    PULL_UP_OPTION = 1 << 0
    INVERSE_LOGIC_OPTION = 1 << 1
    FORMULA_OPTION = 1 << 2
    CUSTOM = 1 << 3
    SEND_DATA_OPTION = 1 << 4
    TIMER_OPTION = 1 << 5
    TIMER_OPTIONAL = 1 << 6
    DECIMALS_ONLY = 1 << 7
    DUPLICATE_DETECTION = 1 << 8
    EXIT_TASK_BEFORE_SAVE = 1 << 9
    ERROR_STATE_VALUES = 1 << 10
    PLUGIN_STATS = 1 << 11
    NO_DEVICE_SETTINGS = 1 << 12
    CUSTOM_VTYPE_VAR = 1 << 13
    MQTT_STATE_CLASS = 1 << 14
    I2C_MAX_100KHZ = 1 << 15
    I2C_NO_DEVICE_CHECK = 1 << 16


DEVICE_FLAG_NAMES: dict[DeviceFlag, str] = {
    DeviceFlag.PULL_UP_OPTION: "PullUpOption",
    DeviceFlag.INVERSE_LOGIC_OPTION: "InverseLogicOption",
    DeviceFlag.FORMULA_OPTION: "FormulaOption",
    DeviceFlag.CUSTOM: "Custom",
    DeviceFlag.SEND_DATA_OPTION: "SendDataOption",
    DeviceFlag.TIMER_OPTION: "TimerOption",
    DeviceFlag.TIMER_OPTIONAL: "TimerOptional",
    DeviceFlag.DECIMALS_ONLY: "DecimalsOnly",
    DeviceFlag.DUPLICATE_DETECTION: "DuplicateDetection",
    DeviceFlag.EXIT_TASK_BEFORE_SAVE: "ExitTaskBeforeSave",
    DeviceFlag.ERROR_STATE_VALUES: "ErrorStateValues",
    DeviceFlag.PLUGIN_STATS: "PluginStats",
    DeviceFlag.NO_DEVICE_SETTINGS: "NoDeviceSettings",
    DeviceFlag.CUSTOM_VTYPE_VAR: "CustomVTypeVar",
    DeviceFlag.MQTT_STATE_CLASS: "MQTTStateClass",
    DeviceFlag.I2C_MAX_100KHZ: "I2CMax100kHz",
    DeviceFlag.I2C_NO_DEVICE_CHECK: "I2CNoDeviceCheck",
}


@dataclass
class DeviceProperties:
    type: int = 1
    vtype: int = 1
    value_count: int = 1
    ports: int = 0
    pull_up_option: bool = False
    inverse_logic_option: bool = False
    formula_option: bool = False
    custom: bool = False
    send_data_option: bool = True
    timer_option: bool = True
    timer_optional: bool = False
    decimals_only: bool = False
    duplicate_detection: bool = False
    exit_task_before_save: bool = True
    error_state_values: bool = False
    plugin_stats: bool = False
    no_device_settings: bool = False
    custom_vtype_var: bool = False
    mqtt_state_class: bool = False
    output_data_type: OutputDataType = OutputDataType.DEFAULT
    i2c_max100khz: bool = False
    i2c_no_device_check: bool = False

    def to_flags(self) -> DeviceFlag:
        flags = DeviceFlag.NONE
        mapping = [
            ("pull_up_option", DeviceFlag.PULL_UP_OPTION),
            ("inverse_logic_option", DeviceFlag.INVERSE_LOGIC_OPTION),
            ("formula_option", DeviceFlag.FORMULA_OPTION),
            ("custom", DeviceFlag.CUSTOM),
            ("send_data_option", DeviceFlag.SEND_DATA_OPTION),
            ("timer_option", DeviceFlag.TIMER_OPTION),
            ("timer_optional", DeviceFlag.TIMER_OPTIONAL),
            ("decimals_only", DeviceFlag.DECIMALS_ONLY),
            ("duplicate_detection", DeviceFlag.DUPLICATE_DETECTION),
            ("exit_task_before_save", DeviceFlag.EXIT_TASK_BEFORE_SAVE),
            ("error_state_values", DeviceFlag.ERROR_STATE_VALUES),
            ("plugin_stats", DeviceFlag.PLUGIN_STATS),
            ("no_device_settings", DeviceFlag.NO_DEVICE_SETTINGS),
            ("custom_vtype_var", DeviceFlag.CUSTOM_VTYPE_VAR),
            ("mqtt_state_class", DeviceFlag.MQTT_STATE_CLASS),
            ("i2c_max100khz", DeviceFlag.I2C_MAX_100KHZ),
            ("i2c_no_device_check", DeviceFlag.I2C_NO_DEVICE_CHECK),
        ]
        for field_name, flag in mapping:
            if getattr(self, field_name, False):
                flags |= flag
        return flags

    def get_flag_names(self) -> list[str]:
        flags = self.to_flags()
        return [name for flag, name in DEVICE_FLAG_NAMES.items() if flags & flag]

    def has_flag(self, flag: DeviceFlag) -> bool:
        return bool(self.to_flags() & flag)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        for k in ("type", "vtype", "value_count", "ports", "output_data_type",
                  "pull_up_option", "inverse_logic_option", "formula_option",
                  "custom", "send_data_option", "timer_option", "timer_optional",
                  "decimals_only", "duplicate_detection", "exit_task_before_save",
                  "error_state_values", "plugin_stats", "no_device_settings",
                  "custom_vtype_var", "mqtt_state_class", "i2c_max100khz",
                  "i2c_no_device_check"):
            d[k] = getattr(self, k)
        d["flags"] = int(self.to_flags())
        d["flag_names"] = self.get_flag_names()
        return d

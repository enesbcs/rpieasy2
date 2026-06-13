from __future__ import annotations

import logging
from typing import Any

from rpieasy2.core.device_properties import DeviceProperties
from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_USB, SENSOR_TYPE_SWITCH

logger = logging.getLogger("rpieasy2.plugin.p517")

_VUSB_VID = 0x16c0
_VUSB_PID = 0x05df


class _USBRelay:
    def __init__(self):
        import hid
        self.compdevs: list[list[Any]] = []
        self.relaynum = 0
        self.serialnum = ""
        self._h: Any = None
        self._scan()

    def _scan(self) -> None:
        import hid
        self.compdevs = []
        self.relaynum = 0
        self.serialnum = ""
        try:
            for d in hid.enumerate(_VUSB_VID, _VUSB_PID):
                prod = d.get("product_string", "") or ""
                if prod and prod[:-1] != "USBRelay":
                    continue
                if prod:
                    try:
                        rel_count = int(prod[-1:])
                    except ValueError:
                        rel_count = 1
                else:
                    rel_count = 1
                hidev = hid.device()
                hidev.open_path(d["path"])
                result = hidev.get_feature_report(1, 9)
                logger.debug("USB Relay: opened %s, get_feature_report(1,9) returned %d bytes: %s",
                             d["path"], len(result) if result else 0, result)
                if not result or len(result) <= 5:
                    logger.debug("USB Relay: trying report_id=0, size=8")
                    result = hidev.get_feature_report(0, 8)
                    logger.debug("USB Relay: get_feature_report(0,8) returned %d bytes: %s",
                                 len(result) if result else 0, result)
                hidev.close()
                if result and len(result) > 5:
                    resstr = "".join(chr(result[i]) for i in range(5))
                    tarr = [rel_count, d["path"], resstr]
                    self.compdevs.append(tarr)
                    if not self.serialnum:
                        self.relaynum = rel_count
                        self.serialnum = resstr
        except Exception as e:
            logger.debug("USB Relay scan error: %s", e)

    def getcompatibledevlist(self) -> list[list[Any]]:
        return self.compdevs

    def getrelaynum(self) -> int:
        return self.relaynum

    def getserialnum(self) -> str:
        return self.serialnum

    def _open(self, dev_path: Any) -> bool:
        import hid
        try:
            if self._h is not None:
                self._h.close()
            self._h = hid.device()
            self._h.open_path(dev_path)
            self._h.set_nonblocking(1)
            return True
        except Exception:
            self._h = None
            return False

    def _ensure_open(self, id_serial: str | None) -> bool:
        if self._h is not None and self.serialnum == id_serial:
            return True
        for d in self.compdevs:
            if d[2] == id_serial:
                self.relaynum = int(d[0])
                self.serialnum = id_serial
                return self._open(d[1])
        return self._scan_and_open(id_serial)

    def _scan_and_open(self, id_serial: str | None) -> bool:
        self._scan()
        for d in self.compdevs:
            if d[2] == id_serial:
                self.relaynum = int(d[0])
                self.serialnum = id_serial
                return self._open(d[1])
        return False

    def state(self, relay: int, on: bool | None = None) -> bool | None:
        import hid
        if self._h is None:
            return None
        if on is None:
            try:
                report = self._h.get_feature_report(1, 8)
                if len(report) < 8:
                    return None
                statuses = [int(x) for x in list("{0:08b}".format(report[7]))]
                statuses.reverse()
                return bool(statuses[relay - 1]) if 1 <= relay <= len(statuses) else None
            except Exception:
                return None
        else:
            try:
                if relay == 0:
                    msg = [0xFE] if on else [0xFC]
                else:
                    msg = [0xFF, relay] if on else [0xFD, relay]
                self._h.send_feature_report(msg)
                return on
            except Exception:
                return None


try:
    _USB_RELAY = _USBRelay()
except Exception as e:
    logger.warning("USB Relay module init failed: %s", e)
    _USB_RELAY = None  # type: ignore[assignment]


class P517USBRelay(PluginBase):
    PLUGIN_ID = 517
    PLUGIN_NAME = "Output - USB Relay"
    PLUGIN_VALUES = 1
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_USB,
        vtype=SENSOR_TYPE_SWITCH,
        value_count=1,
        send_data_option=True,
        timer_option=True,
        formula_option=False,
    )

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._relay_state: int = 0
        self._detected: bool = False

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._relay_state = 0
        self._detected = False
        if _USB_RELAY is None:
            logger.warning("hid module not available, USB Relay disabled")
            return True
        try:
            devlist = _USB_RELAY.getcompatibledevlist()
            if len(devlist) > 0:
                self._detected = True
                did = self._config.get("device_id", "")
                try:
                    rnum = int(self._config.get("relay_num", 1))
                except (ValueError, TypeError):
                    rnum = 1
                if did:
                    _USB_RELAY._ensure_open(did)
                    try:
                        st = _USB_RELAY.state(rnum)
                        if st is not None:
                            self._relay_state = int(st)
                    except Exception:
                        pass
                logger.info("USB Relay detected, %d device(s) found", len(devlist))
            else:
                logger.warning("No USB Relay devices found")
        except Exception as e:
            logger.error("USB Relay init error: %s", e)
        return True

    async def on_plugin_read(self, event: Event) -> bool | None:
        did = self._config.get("device_id", "")
        try:
            rnum = int(self._config.get("relay_num", 1))
        except (ValueError, TypeError):
            rnum = 1
        if did and rnum > 0:
            try:
                _USB_RELAY._ensure_open(did)
                st = _USB_RELAY.state(rnum)
                if st is not None:
                    self._relay_state = int(st)
            except Exception:
                pass
        event.data["values"] = {"Relay": str(self._relay_state)}
        event.data["named_values"] = {"Relay": str(self._relay_state)}
        event.data["value_names"] = ["Relay"]
        return True

    async def on_plugin_write(self, event: Event) -> bool | None:
        sv = event.data.get("values", {})
        if sv:
            raw = next(iter(sv.values()), "0")
            val = 1 if str(raw).lower() in ("1", "on", "true") else 0
            self._set_relay(val)
            event.data["values"] = {"Relay": str(self._relay_state)}
            event.data["named_values"] = {"Relay": str(self._relay_state)}
            event.data["value_names"] = ["Relay"]
            return True
        command = (event.string1 or "").strip().lower()
        if command.startswith("usbrelay"):
            parts = command.split(",")
            try:
                rname = parts[1].strip()
                rnum = int(parts[2].strip())
                val = int(parts[3].strip())
            except (IndexError, ValueError):
                return False
            cfg_id = self._config.get("device_id", "")
            try:
                cfg_num = int(self._config.get("relay_num", 1))
            except (ValueError, TypeError):
                cfg_num = 1
            if rname and rname.lower() == cfg_id.lower():
                if rnum == cfg_num:
                    self._set_relay(val)
                else:
                    _USB_RELAY._ensure_open(cfg_id)
                    _USB_RELAY.state(rnum, on=bool(val))
                event.data["values"] = {"Relay": str(self._relay_state)}
                return True
        return False

    def _set_relay(self, val: int) -> None:
        self._relay_state = val
        did = self._config.get("device_id", "")
        try:
            rnum = int(self._config.get("relay_num", 1))
        except (ValueError, TypeError):
            rnum = 1
        if did and rnum > 0:
            try:
                _USB_RELAY._ensure_open(did)
                _USB_RELAY.state(rnum, on=bool(val))
            except Exception:
                pass

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        form = []
        missing_deps = []
        try:
            import hid
            hid.device
        except Exception:
            missing_deps.append("hidapi")
        import os
        udev_file = "/etc/udev/rules.d/99-usbrelay.rules"
        if not os.path.exists(udev_file):
            missing_deps.append("usbrelay_udev")
        if missing_deps:
            form.append({"name": "_dep_warning", "label": "⚠ Missing: " + ", ".join(missing_deps) +
                         ". <a href='/pluginlist' style='font-weight:bold;'>Install from plugin list →</a>",
                         "type": "warning"})
        cur_id = self._config.get("device_id", "")
        rnum = 1
        try:
            try:
                rnum = int(self._config.get("relay_num", 1))
            except (ValueError, TypeError):
                pass
            if _USB_RELAY is None:
                raise RuntimeError("hid module not available")
            devlist = _USB_RELAY.getcompatibledevlist()
            detected_ids = [d[2] for d in devlist if len(d) > 2]
            dev_opts = [{"value": dev_id, "label": dev_id} for dev_id in detected_ids]
            if not dev_opts:
                dev_opts = [{"value": "", "label": "No USB relays found"}]
            elif cur_id and not any(p["value"] == cur_id for p in dev_opts):
                dev_opts.append({"value": cur_id, "label": cur_id})
            form.append({"name": "device_id", "label": "Device ID", "type": "select",
                         "value": cur_id, "options": dev_opts})
            relay_count = len(detected_ids)
            if relay_count > 0:
                rel_opts = [{"value": str(r), "label": str(r)} for r in range(1, relay_count + 1)]
                form.append({"name": "relay_num", "label": "Relay number on device",
                             "type": "select", "value": str(rnum), "options": rel_opts})
        except Exception:
            form.append({"name": "device_id", "label": "Device ID (USB)", "type": "text",
                         "value": cur_id})
            form.append({"name": "relay_num", "label": "Relay number", "type": "number",
                         "value": str(rnum)})
        event.data["form"] = form
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config["device_id"] = ""
        self._config["relay_num"] = 1
        return True

    async def on_plugin_get_device_value_names(self, event: Event) -> bool | None:
        event.data["value_names"] = ["Relay"]
        return True

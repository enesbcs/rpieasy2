from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import struct
import time
from typing import Any

import serial

from rpieasy2.core.device_properties import DeviceProperties
from rpieasy2.core.events import Event
from rpieasy2.core.plugin_base import PluginBase
from rpieasy2.core.rpiconst import DEVICE_TYPE_SERIAL, SENSOR_TYPE_DUAL, SENSOR_TYPE_SINGLE, SENSOR_TYPE_TRIPLE

logger = logging.getLogger("rpieasy2.plugin.p514")

_SENSOR_TYPE_MAP = {1: SENSOR_TYPE_SINGLE, 2: SENSOR_TYPE_DUAL, 3: SENSOR_TYPE_TRIPLE}

FINGERPRINT_STARTCODE = 0xEF01
FINGERPRINT_COMMANDPACKET = 0x01
FINGERPRINT_ACKPACKET = 0x07
FINGERPRINT_DATAPACKET = 0x02
FINGERPRINT_ENDDATAPACKET = 0x08
FINGERPRINT_VERIFYPASSWORD = 0x13
FINGERPRINT_GETSYSTEMPARAMETERS = 0x0F
FINGERPRINT_TEMPLATEINDEX = 0x1F
FINGERPRINT_TEMPLATECOUNT = 0x1D
FINGERPRINT_READIMAGE = 0x01
FINGERPRINT_CONVERTIMAGE = 0x02
FINGERPRINT_CREATETEMPLATE = 0x05
FINGERPRINT_STORETEMPLATE = 0x06
FINGERPRINT_SEARCHTEMPLATE = 0x04
FINGERPRINT_LOADTEMPLATE = 0x07
FINGERPRINT_DELETETEMPLATE = 0x0C
FINGERPRINT_CLEARDATABASE = 0x0D
FINGERPRINT_DOWNLOADCHARACTERISTICS = 0x08
FINGERPRINT_OK = 0x00
FINGERPRINT_ERROR_COMMUNICATION = 0x01
FINGERPRINT_ERROR_WRONGPASSWORD = 0x13
FINGERPRINT_ERROR_NOFINGER = 0x02
FINGERPRINT_ERROR_READIMAGE = 0x03
FINGERPRINT_ERROR_MESSYIMAGE = 0x06
FINGERPRINT_ERROR_FEWFEATUREPOINTS = 0x07
FINGERPRINT_ERROR_INVALIDIMAGE = 0x15
FINGERPRINT_ERROR_CHARACTERISTICSMISMATCH = 0x0A
FINGERPRINT_ERROR_INVALIDPOSITION = 0x0B
FINGERPRINT_ERROR_FLASH = 0x18
FINGERPRINT_ERROR_NOTEMPLATEFOUND = 0x09
FINGERPRINT_ERROR_LOADTEMPLATE = 0x0C
FINGERPRINT_ERROR_DELETETEMPLATE = 0x10
FINGERPRINT_ERROR_CLEARDATABASE = 0x11
FINGERPRINT_ERROR_DOWNLOADCHARACTERISTICS = 0x0D


class _PyFingerprint:
    def __init__(self, port: str = "/dev/ttyUSB0", baudRate: int = 57600,
                 address: int = 0xFFFFFFFF, password: int = 0x00000000):
        if baudRate < 9600 or baudRate > 115200 or baudRate % 9600 != 0:
            raise ValueError("Invalid baud rate")
        self.__address = address
        self.__password = password
        self.__serial = serial.Serial(port=port, baudrate=baudRate,
                                      bytesize=serial.EIGHTBITS, timeout=2)
        if self.__serial.isOpen():
            self.__serial.close()
        self.__serial.open()

    def close(self) -> None:
        if self.__serial is not None and self.__serial.isOpen():
            self.__serial.close()

    @staticmethod
    def __rightShift(n: int, x: int) -> int:
        return (n >> x & 0xFF)

    @staticmethod
    def __leftShift(n: int, x: int) -> int:
        return (n << x)

    @staticmethod
    def __bitAtPosition(n: int, p: int) -> int:
        twoP = 1 << p
        return int((n & twoP) > 0)

    @staticmethod
    def __byteToString(byte: int) -> bytes:
        return struct.pack("@B", byte)

    @staticmethod
    def __stringToByte(string: bytes) -> int:
        return struct.unpack("@B", string)[0]

    def __writePacket(self, packetType: int, packetPayload: tuple) -> None:
        s = self.__serial
        s.write(self.__byteToString(self.__rightShift(FINGERPRINT_STARTCODE, 8)))
        s.write(self.__byteToString(self.__rightShift(FINGERPRINT_STARTCODE, 0)))
        s.write(self.__byteToString(self.__rightShift(self.__address, 24)))
        s.write(self.__byteToString(self.__rightShift(self.__address, 16)))
        s.write(self.__byteToString(self.__rightShift(self.__address, 8)))
        s.write(self.__byteToString(self.__rightShift(self.__address, 0)))
        s.write(self.__byteToString(packetType))
        packetLength = len(packetPayload) + 2
        s.write(self.__byteToString(self.__rightShift(packetLength, 8)))
        s.write(self.__byteToString(self.__rightShift(packetLength, 0)))
        packetChecksum = packetType + self.__rightShift(packetLength, 8) + self.__rightShift(packetLength, 0)
        for i in range(len(packetPayload)):
            s.write(self.__byteToString(packetPayload[i]))
            packetChecksum += packetPayload[i]
        s.write(self.__byteToString(self.__rightShift(packetChecksum, 8)))
        s.write(self.__byteToString(self.__rightShift(packetChecksum, 0)))

    def __readPacket(self) -> tuple[int, list[int]]:
        receivedPacketData: list[int] = []
        i = 0
        while True:
            receivedFragment = self.__serial.read()
            if len(receivedFragment) != 0:
                receivedFragment = self.__stringToByte(receivedFragment)
            else:
                continue
            receivedPacketData.insert(i, receivedFragment)
            i += 1
            if i >= 12:
                if (receivedPacketData[0] != self.__rightShift(FINGERPRINT_STARTCODE, 8) or
                        receivedPacketData[1] != self.__rightShift(FINGERPRINT_STARTCODE, 0)):
                    raise Exception("Invalid packet header")
                packetPayloadLength = self.__leftShift(receivedPacketData[7], 8)
                packetPayloadLength |= self.__leftShift(receivedPacketData[8], 0)
                if i < packetPayloadLength + 9:
                    continue
                packetType = receivedPacketData[6]
                packetChecksum = packetType + receivedPacketData[7] + receivedPacketData[8]
                packetPayload = []
                for j in range(9, 9 + packetPayloadLength - 2):
                    packetPayload.append(receivedPacketData[j])
                    packetChecksum += receivedPacketData[j]
                receivedChecksum = self.__leftShift(receivedPacketData[i - 2], 8)
                receivedChecksum |= self.__leftShift(receivedPacketData[i - 1], 0)
                if receivedChecksum != packetChecksum:
                    raise Exception("Checksum mismatch")
                return (packetType, packetPayload)

    def verifyPassword(self) -> bool:
        payload = (FINGERPRINT_VERIFYPASSWORD,
                   self.__rightShift(self.__password, 24),
                   self.__rightShift(self.__password, 16),
                   self.__rightShift(self.__password, 8),
                   self.__rightShift(self.__password, 0))
        self.__writePacket(FINGERPRINT_COMMANDPACKET, payload)
        ptype, ppayload = self.__readPacket()
        if ptype != FINGERPRINT_ACKPACKET:
            raise Exception("Not an ack packet")
        if ppayload[0] == FINGERPRINT_OK:
            return True
        if ppayload[0] == FINGERPRINT_ERROR_WRONGPASSWORD:
            return False
        raise Exception(f"verifyPassword error: 0x{ppayload[0]:02X}")

    def readImage(self) -> bool:
        self.__writePacket(FINGERPRINT_COMMANDPACKET, (FINGERPRINT_READIMAGE,))
        ptype, ppayload = self.__readPacket()
        if ptype != FINGERPRINT_ACKPACKET:
            raise Exception("Not an ack packet")
        if ppayload[0] == FINGERPRINT_OK:
            return True
        if ppayload[0] == FINGERPRINT_ERROR_NOFINGER:
            return False
        raise Exception(f"readImage error: 0x{ppayload[0]:02X}")

    def convertImage(self, charBufferNumber: int = 0x01) -> bool:
        self.__writePacket(FINGERPRINT_COMMANDPACKET, (FINGERPRINT_CONVERTIMAGE, charBufferNumber))
        ptype, ppayload = self.__readPacket()
        if ptype != FINGERPRINT_ACKPACKET:
            raise Exception("Not an ack packet")
        if ppayload[0] == FINGERPRINT_OK:
            return True
        if ppayload[0] == FINGERPRINT_ERROR_MESSYIMAGE:
            raise Exception("Image too messy")
        if ppayload[0] == FINGERPRINT_ERROR_FEWFEATUREPOINTS:
            raise Exception("Too few feature points")
        if ppayload[0] == FINGERPRINT_ERROR_INVALIDIMAGE:
            raise Exception("Invalid image")
        raise Exception(f"convertImage error: 0x{ppayload[0]:02X}")

    def createTemplate(self) -> bool:
        self.__writePacket(FINGERPRINT_COMMANDPACKET, (FINGERPRINT_CREATETEMPLATE,))
        ptype, ppayload = self.__readPacket()
        if ptype != FINGERPRINT_ACKPACKET:
            raise Exception("Not an ack packet")
        if ppayload[0] == FINGERPRINT_OK:
            return True
        if ppayload[0] == FINGERPRINT_ERROR_CHARACTERISTICSMISMATCH:
            return False
        raise Exception(f"createTemplate error: 0x{ppayload[0]:02X}")

    def storeTemplate(self, positionNumber: int, charBufferNumber: int = 0x01) -> int:
        capacity = self.getStorageCapacity()
        if positionNumber < 0 or positionNumber >= capacity:
            raise ValueError("Invalid position number")
        self.__writePacket(FINGERPRINT_COMMANDPACKET,
                           (FINGERPRINT_STORETEMPLATE, charBufferNumber,
                            self.__rightShift(positionNumber, 8),
                            self.__rightShift(positionNumber, 0)))
        ptype, ppayload = self.__readPacket()
        if ptype != FINGERPRINT_ACKPACKET:
            raise Exception("Not an ack packet")
        if ppayload[0] == FINGERPRINT_OK:
            return positionNumber
        raise Exception(f"storeTemplate error: 0x{ppayload[0]:02X}")

    def searchTemplate(self, charBufferNumber: int = 0x01, positionStart: int = 0, count: int = -1) -> tuple[int, int]:
        if count > 0:
            templatesCount = count
        else:
            templatesCount = self.getStorageCapacity()
        self.__writePacket(FINGERPRINT_COMMANDPACKET,
                           (FINGERPRINT_SEARCHTEMPLATE, charBufferNumber,
                            self.__rightShift(positionStart, 8),
                            self.__rightShift(positionStart, 0),
                            self.__rightShift(templatesCount, 8),
                            self.__rightShift(templatesCount, 0)))
        ptype, ppayload = self.__readPacket()
        if ptype != FINGERPRINT_ACKPACKET:
            raise Exception("Not an ack packet")
        if ppayload[0] == FINGERPRINT_OK:
            pos = self.__leftShift(ppayload[1], 8) | self.__leftShift(ppayload[2], 0)
            score = self.__leftShift(ppayload[3], 8) | self.__leftShift(ppayload[4], 0)
            return (pos, score)
        if ppayload[0] == FINGERPRINT_ERROR_NOTEMPLATEFOUND:
            return (-1, -1)
        raise Exception(f"searchTemplate error: 0x{ppayload[0]:02X}")

    def loadTemplate(self, positionNumber: int, charBufferNumber: int = 0x01) -> bool:
        self.__writePacket(FINGERPRINT_COMMANDPACKET,
                           (FINGERPRINT_LOADTEMPLATE, charBufferNumber,
                            self.__rightShift(positionNumber, 8),
                            self.__rightShift(positionNumber, 0)))
        ptype, ppayload = self.__readPacket()
        if ptype != FINGERPRINT_ACKPACKET:
            raise Exception("Not an ack packet")
        if ppayload[0] == FINGERPRINT_OK:
            return True
        raise Exception(f"loadTemplate error: 0x{ppayload[0]:02X}")

    def deleteTemplate(self, positionNumber: int, count: int = 1) -> bool:
        capacity = self.getStorageCapacity()
        if positionNumber < 0 or positionNumber >= capacity:
            raise ValueError("Invalid position number")
        self.__writePacket(FINGERPRINT_COMMANDPACKET,
                           (FINGERPRINT_DELETETEMPLATE,
                            self.__rightShift(positionNumber, 8),
                            self.__rightShift(positionNumber, 0),
                            self.__rightShift(count, 8),
                            self.__rightShift(count, 0)))
        ptype, ppayload = self.__readPacket()
        if ptype != FINGERPRINT_ACKPACKET:
            raise Exception("Not an ack packet")
        if ppayload[0] == FINGERPRINT_OK:
            return True
        if ppayload[0] == FINGERPRINT_ERROR_DELETETEMPLATE:
            return False
        raise Exception(f"deleteTemplate error: 0x{ppayload[0]:02X}")

    def clearDatabase(self) -> bool:
        self.__writePacket(FINGERPRINT_COMMANDPACKET, (FINGERPRINT_CLEARDATABASE,))
        ptype, ppayload = self.__readPacket()
        if ptype != FINGERPRINT_ACKPACKET:
            raise Exception("Not an ack packet")
        if ppayload[0] == FINGERPRINT_OK:
            return True
        if ppayload[0] == FINGERPRINT_ERROR_CLEARDATABASE:
            return False
        raise Exception(f"clearDatabase error: 0x{ppayload[0]:02X}")

    def getTemplateCount(self) -> int:
        self.__writePacket(FINGERPRINT_COMMANDPACKET, (FINGERPRINT_TEMPLATECOUNT,))
        ptype, ppayload = self.__readPacket()
        if ptype != FINGERPRINT_ACKPACKET:
            raise Exception("Not an ack packet")
        if ppayload[0] == FINGERPRINT_OK:
            return self.__leftShift(ppayload[1], 8) | self.__leftShift(ppayload[2], 0)
        raise Exception(f"getTemplateCount error: 0x{ppayload[0]:02X}")

    def getStorageCapacity(self) -> int:
        return self.getSystemParameters()[2]

    def getTemplateIndex(self, page: int) -> list[bool]:
        if page < 0 or page > 3:
            raise ValueError("Invalid index page")
        self.__writePacket(FINGERPRINT_COMMANDPACKET, (FINGERPRINT_TEMPLATEINDEX, page))
        ptype, ppayload = self.__readPacket()
        if ptype != FINGERPRINT_ACKPACKET:
            raise Exception("Not an ack packet")
        if ppayload[0] == FINGERPRINT_OK:
            result = []
            for pageElement in ppayload[1:]:
                for p in range(8):
                    result.append(self.__bitAtPosition(pageElement, p) == 1)
            return result
        raise Exception(f"getTemplateIndex error: 0x{ppayload[0]:02X}")

    def getSystemParameters(self) -> tuple:
        self.__writePacket(FINGERPRINT_COMMANDPACKET, (FINGERPRINT_GETSYSTEMPARAMETERS,))
        ptype, ppayload = self.__readPacket()
        if ptype != FINGERPRINT_ACKPACKET:
            raise Exception("Not an ack packet")
        if ppayload[0] == FINGERPRINT_OK:
            status = self.__leftShift(ppayload[1], 8) | self.__leftShift(ppayload[2], 0)
            sysid = self.__leftShift(ppayload[3], 8) | self.__leftShift(ppayload[4], 0)
            capacity = self.__leftShift(ppayload[5], 8) | self.__leftShift(ppayload[6], 0)
            security = self.__leftShift(ppayload[7], 8) | self.__leftShift(ppayload[8], 0)
            addr = (((ppayload[9] << 8 | ppayload[10]) << 8 | ppayload[11]) << 8 | ppayload[12])
            pktlen = self.__leftShift(ppayload[13], 8) | self.__leftShift(ppayload[14], 0)
            baud = self.__leftShift(ppayload[15], 8) | self.__leftShift(ppayload[16], 0)
            return (status, sysid, capacity, security, addr, pktlen, baud)
        raise Exception(f"getSystemParameters error: 0x{ppayload[0]:02X}")

    def downloadCharacteristics(self, charBufferNumber: int = 0x01) -> list[int]:
        self.__writePacket(FINGERPRINT_COMMANDPACKET,
                           (FINGERPRINT_DOWNLOADCHARACTERISTICS, charBufferNumber))
        ptype, ppayload = self.__readPacket()
        if ptype != FINGERPRINT_ACKPACKET:
            raise Exception("Not an ack packet")
        if ppayload[0] != FINGERPRINT_OK:
            raise Exception(f"downloadCharacteristics error: 0x{ppayload[0]:02X}")
        completePayload: list[int] = []
        while ptype != FINGERPRINT_ENDDATAPACKET:
            ptype, ppayload = self.__readPacket()
            if ptype not in (FINGERPRINT_DATAPACKET, FINGERPRINT_ENDDATAPACKET):
                raise Exception("Not a data packet")
            completePayload.extend(ppayload)
        return completePayload


class P514FPM(PluginBase):
    PLUGIN_ID = 514
    PLUGIN_NAME = "ID - Serial Fingerprint Module"
    PLUGIN_VALUES = 3
    DEVICE_PROPERTIES = DeviceProperties(
        type=DEVICE_TYPE_SERIAL,
        vtype=SENSOR_TYPE_SINGLE,
        value_count=3,
        send_data_option=True,
        timer_option=True,
        formula_option=True,
        custom_vtype_var=True,
    )

    def __init__(self):
        super().__init__()
        self._config: dict[str, Any] = {}
        self._fp: _PyFingerprint | None = None
        self._initialized: bool = False
        self._read_in_progress: bool = False
        self._init_count: int = 0
        self._current_vtype: int = SENSOR_TYPE_SINGLE
        self._values: list[str] = ["0", "0", "0"]
        self._ind_types: list[int] = [0, 0, 0]

    async def on_plugin_init(self, event: Event) -> bool | None:
        self._config = event.data.get("task_config", {})
        self._read_in_progress = False
        self._init_count = 0
        self._update_ind_types()
        port = str(self._config.get("serial_port", "")).strip()
        if not port or port == "0":
            logger.warning("FPM: no serial port configured")
            return True
        await asyncio.to_thread(self._do_init, port)
        if not self._initialized:
            self._init_count += 1
            if self._init_count > 2:
                logger.error("FPM: init failed 3 times, disabling")
                self._config["enabled"] = False
        return True

    def _do_init(self, port: str) -> None:
        time.sleep(0.5)
        try:
            if self._fp is not None:
                self._fp.close()
        except Exception:
            pass
        self._fp = None
        self._initialized = False
        try:
            time.sleep(2)
            self._fp = _PyFingerprint(port, 57600, 0xFFFFFFFF, 0)
            time.sleep(0.5)
            if not self._fp.verifyPassword():
                logger.error("FPM password wrong")
                self._fp.close()
                self._fp = None
                return
            self._initialized = True
            logger.info("FPM initialized on %s", port)
        except Exception as e:
            logger.error("FPM init error: %s", e)
            self._fp = None
            self._initialized = False

    def _update_ind_types(self) -> None:
        vc = 0
        for i in range(3):
            raw = self._config.get(f"ind{i}", 0)
            if raw is None or isinstance(raw, str):
                raw = 0
            self._ind_types[i] = int(raw)
            if self._ind_types[i] > 0:
                vc = i + 1
        if vc == 0:
            vc = 1
        self._current_vtype = _SENSOR_TYPE_MAP.get(vc, SENSOR_TYPE_SINGLE)

    async def on_plugin_read(self, event: Event) -> bool | None:
        if not self._initialized or not self._fp or self._read_in_progress:
            return False
        self._read_in_progress = True
        try:
            await asyncio.to_thread(self._do_scan)
        except Exception as e:
            logger.error("FPM scan error: %s", e)
        finally:
            self._read_in_progress = False

        vals = {}
        for i in range(3):
            if self._ind_types[i] > 0:
                vname = self._config.get(f"TDVN{i + 1}", f"Value {i + 1}")
                vals[vname] = self._values[i]
        if not vals:
            vals["Value 1"] = self._values[0]
        event.data["values"] = vals
        return True

    def _do_scan(self) -> None:
        scantime = 0.8 if 0 < self._config.get("TDT", 60) < 2 else 2
        st = time.time()
        readok = False
        while not self._fp.readImage():
            if time.time() - st >= scantime:
                break
        else:
            readok = True

        pos = -1
        if readok:
            try:
                self._fp.convertImage(0x01)
                result = self._fp.searchTemplate()
                pos = result[0]
            except Exception:
                pass

        changed = False
        for v in range(3):
            it = self._ind_types[v]
            if it == 0:
                continue
            if it == 1:
                new_val = "1" if pos >= 0 else "0"
            elif it == 2:
                new_val = str(pos)
            elif it == 3:
                if pos >= 0:
                    try:
                        self._fp.loadTemplate(pos, 0x01)
                        chars = str(self._fp.downloadCharacteristics(0x01)).encode("utf-8")
                        new_val = hashlib.sha256(chars).hexdigest()
                    except Exception:
                        new_val = "0"
                else:
                    new_val = "0"
            else:
                continue
            if self._values[v] != new_val:
                self._values[v] = new_val
                changed = True

        if changed:
            self._do_send_data()

    def _do_send_data(self) -> None:
        bus = self._event_bus
        idx = self._task_index
        if idx < 0:
            return
        vals = {}
        for i in range(3):
            if self._ind_types[i] > 0:
                vname = self._config.get(f"TDVN{i + 1}", f"Value {i + 1}")
                vals[vname] = self._values[i]
        if not vals:
            vals["Value 1"] = self._values[0]
        from rpieasy2.core.config import get_config
        cfg = get_config()
        for ctrl_idx, ctrl_cfg in enumerate(cfg.data.get("controllers", [])):
            if not ctrl_cfg.get("controllerenabled", ctrl_cfg.get("enabled", True)):
                continue
            if not self._config.get(f"TDSD{ctrl_idx}", True):
                continue
            send_ev = Event(
                type="CONTROLLER_SEND",
                task_index=idx,
                controller_index=ctrl_idx,
                data={
                    "task_config": self._config,
                    "values": {
                        "named_values": dict(vals),
                        "value_names": list(vals.keys()),
                        "controller": dict(ctrl_cfg),
                    },
                },
            )
            asyncio.create_task(bus.publish(send_ev))

    async def on_plugin_write(self, event: Event) -> bool | None:
        sv = event.data.get("values", {})
        if sv:
            raw = next(iter(sv.values()), "").strip().lower()
            if raw in ("enroll", "search"):
                await asyncio.to_thread(self._do_manage, raw, -1)
                return True
        cmd = (event.string1 or "").strip().lower()
        if cmd.startswith("finger,"):
            parts = cmd.split(",")
            action = parts[1].strip() if len(parts) > 1 else ""
            fpnum = -1
            if len(parts) > 2:
                try:
                    fpnum = int(parts[2].strip())
                except ValueError:
                    pass
            await asyncio.to_thread(self._do_manage, action, fpnum)
            return True
        return False

    def _do_manage(self, action: str, fpnum: int) -> str:
        if not self._initialized or not self._fp:
            logger.warning("FPM not initialized")
            return "not_initialized"
        if action == "enroll":
            return self._do_enroll()
        elif action == "search":
            return self._do_search()
        elif action == "delete":
            return self._do_delete(fpnum)
        elif action == "clear":
            return self._do_clear()
        return "unknown"

    def _do_enroll(self) -> str:
        logger.info("FPM: place finger onto scanner")
        readok = True
        st = time.time()
        while not self._fp.readImage():
            if time.time() - st >= 4:
                readok = False
                break
        if not readok:
            logger.info("FPM enroll read failed")
            return "read_failed"
        self._fp.convertImage(0x01)
        result = self._fp.searchTemplate()
        if result[0] >= 0:
            logger.error("FPM template already exists at #%d", result[0])
            return "exists"
        self._fp.convertImage(0x02)
        self._fp.createTemplate()
        posnum = self._fp.getTemplateCount()
        self._fp.storeTemplate(posnum)
        logger.info("FPM template #%d added", posnum)
        return f"added:{posnum}"

    def _do_search(self) -> str:
        logger.info("FPM: place finger onto scanner")
        readok = True
        st = time.time()
        while not self._fp.readImage():
            if time.time() - st >= 4:
                readok = False
                break
        if not readok:
            logger.info("FPM search read failed")
            return "read_failed"
        self._fp.convertImage(0x01)
        result = self._fp.searchTemplate()
        if result[0] >= 0:
            logger.info("FPM found template at #%d (score=%d)", result[0], result[1])
            return f"found:{result[0]}:{result[1]}"
        logger.info("FPM template not found")
        return "not_found"

    def _do_delete(self, fpnum: int) -> str:
        if fpnum < 0:
            return "invalid_position"
        if self._fp.deleteTemplate(fpnum):
            logger.info("FPM template #%d deleted", fpnum)
            return f"deleted:{fpnum}"
        logger.error("FPM template #%d deletion failed", fpnum)
        return "delete_failed"

    def _do_clear(self) -> str:
        if self._fp.clearDatabase():
            logger.info("FPM all templates deleted")
            return "cleared"
        logger.error("FPM database clear failed")
        return "clear_failed"

    async def on_plugin_get_devicevtype(self, event: Event) -> bool | None:
        event.data["sensor_type"] = self._current_vtype
        return True

    async def on_plugin_get_device_value_names(self, event: Event) -> bool | None:
        names = []
        for i in range(3):
            if self._ind_types[i] > 0:
                names.append(self._config.get(f"TDVN{i + 1}", f"Value {i + 1}"))
        if not names:
            names = ["Value 1"]
        event.data["value_names"] = names
        return True

    async def on_plugin_webform_load(self, event: Event) -> bool | None:
        form = []
        port = str(self._config.get("serial_port", "")).strip()
        try:
            import serial.tools.list_ports
            ports_found = serial.tools.list_ports.comports()
            port_options = [{"value": p.device, "label": p.device} for p in ports_found]
            if not port_options:
                port_options = [{"value": "", "label": "No serial ports found"}]
            elif port and not any(p["value"] == port for p in port_options):
                port_options.append({"value": port, "label": port})
        except Exception:
            port_options = [{"value": port or "", "label": port or "/dev/ttyUSB0"}]
        form.append({"name": "serial_port", "label": "Serial Device", "type": "select",
                     "value": port, "options": port_options})

        ind_opts = [
            {"value": 0, "label": "None"},
            {"value": 1, "label": "Valid"},
            {"value": 2, "label": "Position"},
            {"value": 3, "label": "SHA2"},
        ]
        for i in range(3):
            form.append({"name": f"ind{i}", "label": f"Indicator {i + 1}", "type": "select",
                         "value": self._config.get(f"ind{i}", 0), "options": ind_opts})

        if self._initialized and self._fp:
            try:
                count = self._fp.getTemplateCount()
                capacity = self._fp.getStorageCapacity()
                form.append({"type": "text", "name": "_fpm_status",
                             "label": "Stored fingerprints",
                             "value": f"{count}/{capacity}"})
            except Exception:
                pass

        form.append({"type": "text", "name": "_fpm_help",
                     "label": "Management",
                     "value": "Use commands: finger,enroll / finger,search "
                              "/ finger,delete,<id> / finger,clear"})
        event.data["form"] = form
        return True

    async def on_plugin_webform_save(self, event: Event) -> bool | None:
        self._config.update(event.data.get("form_data", {}))
        self._update_ind_types()
        return True

    async def on_plugin_set_defaults(self, event: Event) -> bool | None:
        self._config["serial_port"] = ""
        self._config["ind0"] = 0
        self._config["ind1"] = 0
        self._config["ind2"] = 0
        return True

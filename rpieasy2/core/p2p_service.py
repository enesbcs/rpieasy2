from __future__ import annotations

import asyncio
import logging
import socket
import struct
import time
from typing import Any

from rpieasy2.core.events import Event, get_event_bus
from rpieasy2.core.rpiconst import (
    BUILD,
    DEFAULT_P2P_PORT,
    DEFAULT_WEB_PORT,
    DNS_QUERY_PORT,
    GOOGLE_DNS,
    NODE_TYPE_ID_RPI_EASY_STD,
    NODE_TYPE_NAMES,
    P2P_BROADCAST_INTERVAL,
    P2P_NODE_TIMEOUT,
    P2P_CLEANUP_INTERVAL,
    build_to_date_str,
    get_local_ip,
)

logger = logging.getLogger("rpieasy2.p2p_service")

P2P_DEFAULT_PORT = DEFAULT_P2P_PORT
BROADCAST_INTERVAL = P2P_BROADCAST_INTERVAL
NODE_TIMEOUT = P2P_NODE_TIMEOUT
RPI_EASY_NODE_TYPE = NODE_TYPE_ID_RPI_EASY_STD
NODE_STRUCT_SIZE = 66
P2P_PREFIX = bytes([255, 1])
P2P_PACKET_SIZE = NODE_STRUCT_SIZE + 2

_nodes: dict[int, dict[str, Any]] = {}
_node_lock = asyncio.Lock()
_stop_event = asyncio.Event()
_listener_ready = asyncio.Event()
_active_port: int = 0
_transport: asyncio.DatagramTransport | None = None


def _build_number() -> int:
    return BUILD


def _get_mac() -> bytes:
    for iface in ["eth0", "enp3s0", "enp4s0", "enp2s0", "enp0s31f6", "enx*", "wlan0", "wlp*"]:
        try:
            import glob
            for path in glob.glob(f"/sys/class/net/{iface}/address"):
                with open(path) as f:
                    mac = f.read().strip()
                    if mac and mac != "00:00:00:00:00:00":
                        return bytes.fromhex(mac.replace(":", ""))
        except Exception:
            pass
    return b"\x00\x00\x00\x00\x00\x00"


def get_discovered_nodes() -> dict[int, dict[str, Any]]:
    return dict(_nodes)


def get_p2p_port() -> int:
    return _active_port


def p2p_sendto(payload: bytes, addr: str = "255.255.255.255", port: int | None = None) -> None:
    tr = _transport
    if tr is None:
        return
    try:
        tr.sendto(payload, (addr, port if port is not None else _active_port))
    except Exception as e:
        logger.debug(f"P2P sendto failed: {e}")


async def _update_node(unit: int, data: dict[str, Any]):
    async with _node_lock:
        _nodes[unit] = data


def _parse_node_struct(data: bytes) -> dict[str, Any] | None:
    if len(data) < NODE_STRUCT_SIZE:
        return None
    try:
        fields = struct.unpack("<6B4B B H 25s B H 6B B B B B B I B B I I", data)
    except Exception:
        return None
    idx = 0
    sta_mac = bytes(fields[idx:idx+6]); idx += 6
    ip_bytes = fields[idx:idx+4]; idx += 4
    unit = fields[idx]; idx += 1
    build = fields[idx]; idx += 1
    name_raw: bytes = fields[idx]; idx += 1
    node_type = fields[idx]; idx += 1
    web_port = fields[idx]; idx += 1
    ap_mac = bytes(fields[idx:idx+6]); idx += 6
    load_raw = fields[idx]; idx += 1
    distance = fields[idx]; idx += 1
    time_source = fields[idx]; idx += 1
    channel = fields[idx]; idx += 1
    bitfield = fields[idx]; idx += 1
    last_updated = fields[idx]; idx += 1
    version = fields[idx]; idx += 1
    _unused = fields[idx]; idx += 1
    unix_sec = fields[idx]; idx += 1
    unix_frac = fields[idx]; idx += 1

    name = name_raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace").strip()
    ip_str = ".".join(str(b) for b in ip_bytes) if any(ip_bytes) else ""
    rssi = bitfield & 0x3F
    return {
        "unit": unit, "name": name, "build": str(build),
        "node_type": node_type, "ip": ip_str,
        "load_raw": load_raw, "web_port": web_port,
        "sta_mac": sta_mac.hex(), "time_source": time_source,
        "version": version, "unix_sec": unix_sec,
        "rssi": rssi, "distance": distance, "channel": channel,
    }


async def _handle_p2p_binary(data: bytes, addr: tuple[str, int]):
    if len(data) < 3 or data[0] != 255 or data[1] != 1:
        return
    payload = data[2:]
    now = time.time()

    if len(payload) >= 66:
        parsed = _parse_node_struct(payload)
        if parsed is None:
            return
        unit = parsed["unit"]
        ip_str = parsed["ip"] or addr[0]
        type_str = NODE_TYPE_NAMES.get(parsed["node_type"], f"ESP Easy ({parsed['node_type']})")
        web_port = parsed.get("web_port", DEFAULT_WEB_PORT)
        node_build = build_to_date_str(int(parsed["build"])) if parsed["build"] else parsed["build"]
        await _update_node(unit, {
            "id": unit, "name": parsed["name"], "build": node_build,
            "type": type_str, "ip": ip_str, "web_port": web_port,
            "load": "-", "age": str(int(now)), "last_seen": now,
        })
    elif len(payload) >= 39:
        mac = payload[0:6]
        ip_bytes = payload[6:10]
        unit = payload[10]
        build_raw = struct.unpack("<H", payload[11:13])[0]
        name_raw = payload[13:38]
        node_type = payload[38]
        ip_str = ".".join(str(b) for b in ip_bytes) if any(ip_bytes) else addr[0]
        name = name_raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace").strip()
        type_str = NODE_TYPE_NAMES.get(node_type, f"ESP Easy ({node_type})")
        node_build = build_to_date_str(build_raw) if build_raw else str(build_raw)
        await _update_node(unit, {
            "id": unit, "name": name, "build": node_build,
            "type": type_str, "ip": ip_str, "web_port": DEFAULT_WEB_PORT,
            "load": "-", "age": str(int(now)), "last_seen": now,
        })
    elif len(payload) >= 11:
        mac = payload[0:6]
        ip_bytes = payload[6:10]
        unit = payload[10]
        ip_str = ".".join(str(b) for b in ip_bytes) if any(ip_bytes) else addr[0]
        await _update_node(unit, {
            "id": unit, "name": f"Unit {unit}", "build": "",
            "type": "ESP Easy", "ip": ip_str, "web_port": DEFAULT_WEB_PORT,
            "load": "-", "age": str(int(now)), "last_seen": now,
        })


async def _handle_p2p_sensor_info(data: bytes, addr: tuple[str, int]) -> None:
    bus = get_event_bus()
    ev = Event(
        type="P2P_SENSOR_INFO",
        data={"raw": data, "addr": addr},
    )
    await bus.publish(ev)


async def _handle_p2p_sensor_info_pull(data: bytes, addr: tuple[str, int]) -> None:
    bus = get_event_bus()
    ev = Event(
        type="P2P_SENSOR_INFO_PULL",
        data={"raw": data, "addr": addr},
    )
    await bus.publish(ev)


async def _handle_p2p_sensor_data_pull(data: bytes, addr: tuple[str, int]) -> None:
    bus = get_event_bus()
    ev = Event(
        type="P2P_SENSOR_DATA_PULL",
        data={"raw": data, "addr": addr},
    )
    await bus.publish(ev)


async def _handle_p2p_sensor_data(data: bytes, addr: tuple[str, int]) -> None:
    bus = get_event_bus()
    ev = Event(
        type="P2P_SENSOR_DATA",
        data={"raw": data, "addr": addr},
    )
    await bus.publish(ev)


async def _udp_listener():
    global _transport
    loop = asyncio.get_event_loop()

    class P2PProtocol(asyncio.DatagramProtocol):
        def connection_made(self, tr):
            global _transport
            _transport = tr
            _listener_ready.set()
            logger.info("P2P listening on port %d", _active_port)

        def datagram_received(self, data: bytes, addr: tuple[str, int]):
            if len(data) < 2 or data[0] != 255:
                return
            msg_type = data[1]
            if msg_type == 1:
                asyncio.ensure_future(_handle_p2p_binary(data, addr))
            elif msg_type == 2:
                asyncio.ensure_future(_handle_p2p_sensor_info_pull(data, addr))
            elif msg_type == 3:
                asyncio.ensure_future(_handle_p2p_sensor_info(data, addr))
            elif msg_type == 4:
                asyncio.ensure_future(_handle_p2p_sensor_data_pull(data, addr))
            elif msg_type == 5:
                asyncio.ensure_future(_handle_p2p_sensor_data(data, addr))

        def error_received(self, exc):
            logger.debug(f"P2P error: {exc}")

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind(("0.0.0.0", _active_port))
        sock.setblocking(False)
        await loop.create_datagram_endpoint(lambda: P2PProtocol(), sock=sock)
    except Exception as e:
        logger.warning(f"P2P listen failed on port {_active_port}: {e}")
        return

    try:
        await _stop_event.wait()
    finally:
        tr = _transport
        _transport = None
        if tr:
            tr.close()


def _make_node_struct(unit: int, name: str, ip: str, node_type: int,
                      build: int, web_port: int, mac: bytes) -> bytes:
    ip_parts = [int(x) for x in ip.split(".", 3)]
    while len(ip_parts) < 4:
        ip_parts.append(0)
    name_encoded = name.encode("utf-8", errors="replace")[:24]
    name_padded = name_encoded + b"\x00" * (25 - len(name_encoded))
    load_raw = 127
    rssi_scaled = 32
    bitfield = rssi_scaled & 0x3F
    last_updated = int(time.time())
    return struct.pack(
        "<6B4BBH25sBH6BBBBBBIBBII",
        *mac, *ip_parts, unit, build, name_padded, node_type,
        web_port,
        0, 0, 0, 0, 0, 0,
        0, 0, 0, 0,
        bitfield, last_updated, 1, 0, 0, 0,
    )


async def _broadcast_presence(cfg_data: dict):
    unit = cfg_data.get("system", {}).get("unit", 1)
    name = cfg_data.get("system", {}).get("name", "RPiEasy")
    build = _build_number()
    web_port = cfg_data.get("system", {}).get("web_port", DEFAULT_WEB_PORT)
    ip = get_local_ip()
    mac = _get_mac()

    node_struct = _make_node_struct(unit, name, ip, RPI_EASY_NODE_TYPE,
                                    build, web_port, mac)
    payload = P2P_PREFIX + node_struct
    p2p_sendto(payload)

    await _update_node(unit, {
        "id": unit, "name": name, "build": build_to_date_str(build),
        "type": f"RPiEasy ({RPI_EASY_NODE_TYPE})", "ip": ip,
        "web_port": web_port,
        "load": "-", "age": "0", "last_seen": time.time(),
    })


async def _broadcast_loop(cfg):
    await _listener_ready.wait()
    while not _stop_event.is_set():
        cfg_data = cfg.data if hasattr(cfg, "data") else cfg
        await _broadcast_presence(cfg_data)
        await asyncio.sleep(BROADCAST_INTERVAL)


async def _cleanup_loop():
    await _listener_ready.wait()
    while not _stop_event.is_set():
        now = time.time()
        async with _node_lock:
            stale = [uid for uid, nd in _nodes.items()
                     if now - nd.get("last_seen", 0) > NODE_TIMEOUT]
            for uid in stale:
                del _nodes[uid]
        await asyncio.sleep(P2P_CLEANUP_INTERVAL)


async def start_p2p_service(cfg, scheduler):
    global _active_port
    cfg_data = cfg.data if hasattr(cfg, "data") else cfg
    _active_port = int(cfg_data.get("system", {}).get("p2p_port", P2P_DEFAULT_PORT))
    if _active_port <= 0:
        logger.debug("P2P disabled (port=0)")
        return

    _stop_event.clear()
    asyncio.create_task(_udp_listener())
    asyncio.create_task(_broadcast_loop(cfg))
    asyncio.create_task(_cleanup_loop())
    logger.debug("P2P service started on port %d", _active_port)


def stop_p2p_service():
    _stop_event.set()
    _listener_ready.clear()
    logger.debug("P2P service stopped")

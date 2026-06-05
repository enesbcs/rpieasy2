from __future__ import annotations

import logging
import os
from typing import Any

from aiohttp import web

from rpieasy2.core.config import get_config
from rpieasy2.core.rpiconst import build_to_date_str
from rpieasy2.core.system_vars import resolve_template

logger = logging.getLogger("rpieasy2.custompage")

_CUSTOM_DIR: str | None = None


def _get_custom_dir() -> str:
    global _CUSTOM_DIR
    if _CUSTOM_DIR is None:
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        _CUSTOM_DIR = os.path.join(base, "custom")
        os.makedirs(_CUSTOM_DIR, exist_ok=True)
    return _CUSTOM_DIR


def _get_engine(request: web.Request) -> Any | None:
    return request.app.get("rules_engine")


def parse_template(text: str, request: web.Request) -> str:
    engine = _get_engine(request)
    task_values = getattr(engine, "_task_values", None)
    task_names = getattr(engine, "_task_names", None)
    return resolve_template(text, task_values, task_names)


def _render_dashboard_content(request: web.Request) -> str:
    cfg = get_config()
    engine = _get_engine(request)
    rows = ""
    for i, task in enumerate(cfg.data.get("tasks", [])):
        pid = task.get("plugin_id") or task.get("plugin", 0)
        if not pid:
            continue
        name = task.get("TDN", task.get("name", f"Task {i + 1}"))
        if engine:
            tv = engine._task_values if hasattr(engine, "_task_values") else {}
            vals = tv.get(i, {})
        else:
            vals = {}
        val_strs = " | ".join(f"{k}={v}" for k, v in vals.items()) if vals else "-"
        rows += f"<tr><td>{i + 1}</td><td>{name}</td><td>{val_strs}</td></tr>\n"

    node_info = ""
    try:
        from rpieasy2.core.p2p_service import get_discovered_nodes
        nodes = get_discovered_nodes()
        for uid, nd in nodes.items():
            if uid == cfg.data.get("system", {}).get("unit", 0):
                continue
            node_info += f"<tr><td>{nd.get('id', uid)}</td><td>{nd.get('name', '')}</td><td>{nd.get('ip', '-')}</td></tr>\n"
    except Exception:
        pass

    html = f"""<!DOCTYPE html>
<html>
    <head><title>Dashboard - RPIEasy</title>
<style>
body {{ font-family: sans-serif; margin: 20px; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
th {{ background-color: #4CAF50; color: white; }}
tr:nth-child(even) {{ background-color: #f2f2f2; }}
h2 {{ color: #333; }}
</style>
</head>
<body>
<h1>RPIEasy Dashboard</h1>
<h2>Task Values</h2>
<table><tr><th>#</th><th>Name</th><th>Values</th></tr>
{rows}
</table>
<h2>P2P Nodes</h2>
<table><tr><th>Unit</th><th>Name</th><th>IP</th></tr>
{node_info}
</table>
</body>
</html>"""
    return html


async def handle_custom(request: web.Request) -> web.Response:
    tail = request.match_info.get("tail", "").lstrip("/")

    cmd = request.query.get("cmd", "")
    if cmd:
        engine = _get_engine(request)
        if engine and hasattr(engine, "_execute_command") and hasattr(engine, "is_enabled") and engine.is_enabled():
            from rpieasy2.core.rules_engine import RuleCommand
            rc = RuleCommand(cmd)
            if rc.name:
                try:
                    ctx = {
                        "task_values": {},
                        "vars": engine._vars if hasattr(engine, "_vars") else {},
                        "str_vars": engine._str_vars if hasattr(engine, "_str_vars") else {},
                    }
                    await engine._execute_command(rc, ctx)
                except Exception as e:
                    logger.warning(f"Custom page cmd failed: {e}")

    esp_path: str | None = None
    if tail.endswith(".esp"):
        esp_path = os.path.join(_get_custom_dir(), tail)
    elif tail and "." not in tail:
        esp_path = os.path.join(_get_custom_dir(), tail + ".esp")

    if esp_path and os.path.exists(esp_path) and os.path.isfile(esp_path):
        try:
            with open(esp_path, encoding="utf-8") as f:
                content = f.read()
            content = parse_template(content, request)
            return web.Response(text=content, content_type="text/html")
        except Exception as e:
            logger.error(f"Failed to serve custom page {esp_path}: {e}")
            raise web.HTTPNotFound()

    if not tail or "dashboard" in tail.lower() or "custom" in tail.lower():
        html = _render_dashboard_content(request)
        return web.Response(text=html, content_type="text/html")

    raise web.HTTPNotFound()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cloudflare Fleet register-node agent for grok-register.

Safe skeleton: it does not change the existing web/CLI register flow. It lets a
Render/VPS node register to a fleet hub, heartbeat, poll register
jobs, run selfcheck, and report results. Real registration execution should be
wired explicitly after selfcheck passes.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import requests

FLEET_HUB = os.getenv("FLEET_HUB", "").rstrip("/")
NODE_SECRET = os.getenv("NODE_SECRET", "")
NODE_ID = os.getenv("NODE_ID") or os.getenv("RENDER_SERVICE_ID") or f"register-{uuid.uuid4().hex[:8]}"
NODE_NAME = os.getenv("NODE_NAME", "grok-register-node")
NODE_ROLE = os.getenv("NODE_ROLE", "register")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "10"))
MAIL_API_BASE = os.getenv("MAIL_API_BASE") or os.getenv("CLOUDFLARE_API_BASE") or ""


def log(*args):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), *args, flush=True)


def headers():
    return {"Authorization": f"Bearer {NODE_SECRET}", "Content-Type": "application/json"}


def post(path: str, payload: dict) -> dict:
    r = requests.post(FLEET_HUB + path, headers=headers(), json=payload, timeout=60)
    r.raise_for_status()
    return r.json()


def get(path: str, params: dict | None = None) -> dict:
    r = requests.get(FLEET_HUB + path, headers=headers(), params=params, timeout=60)
    r.raise_for_status()
    return r.json()


def tcp_open(host: str, port: int, timeout: float = 3) -> bool:
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True
    except Exception:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def selfcheck() -> dict:
    out: dict = {
        "python": sys.version.split()[0],
        "cwd": str(Path.cwd()),
        "fleet_hub": FLEET_HUB,
        "mail_api_base": MAIL_API_BASE,
        "chromium": shutil.which("chromium") or shutil.which("chromium-browser") or os.getenv("BROWSER_PATH", ""),
        "xvfb": shutil.which("Xvfb"),
        "display": os.getenv("DISPLAY", ""),
        "proxy": os.getenv("PROXY_URL") or os.getenv("proxy") or os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY") or "",
    }
    try:
        rr = requests.get(MAIL_API_BASE.rstrip("/") + "/health", timeout=12)
        out["mail_health_code"] = rr.status_code
        out["mail_health"] = rr.text[:300]
    except Exception as e:
        out["mail_health_error"] = str(e)
    try:
        import DrissionPage  # type: ignore
        out["drissionpage"] = getattr(DrissionPage, "__version__", "installed")
    except Exception as e:
        out["drissionpage_error"] = str(e)
    try:
        import tkinter  # noqa: F401
        out["tkinter"] = "ok"
    except Exception as e:
        out["tkinter_error"] = str(e)
    return out


def start_health_server():
    port = int(os.getenv("PORT", "10000"))

    class H(BaseHTTPRequestHandler):
        def log_message(self, *args):
            return

        def do_GET(self):
            body = json.dumps({"ok": True, "node_id": NODE_ID, "role": NODE_ROLE, "selfcheck": selfcheck()}, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    threading.Thread(target=lambda: HTTPServer(("0.0.0.0", port), H).serve_forever(), daemon=True).start()
    log("health server on", port)


def handle_task(task: dict) -> dict:
    payload = task.get("payload") or {}
    kind = payload.get("kind") or "register"
    if kind == "selfcheck":
        return {"kind": "selfcheck", "selfcheck": selfcheck()}
    # Intentionally not running account registration automatically in this skeleton.
    # Wire real register flow here after image dependencies/proxy/mail/CPA selfcheck pass.
    return {
        "kind": kind,
        "implemented": False,
        "message": "register execution is not wired yet; this node skeleton only heartbeats/selfchecks safely",
        "selfcheck": selfcheck(),
    }


def main():
    start_health_server()
    sc = selfcheck()
    log("register-node start", NODE_ID, FLEET_HUB)
    post("/api/node/register", {
        "id": NODE_ID,
        "role": NODE_ROLE,
        "name": NODE_NAME,
        "provider": "render" if os.getenv("RENDER") or os.getenv("RENDER_SERVICE_ID") else "generic",
        "url": os.getenv("RENDER_EXTERNAL_URL", ""),
        "image": os.getenv("IMAGE", ""),
        "selfcheck": sc,
        "labels": {"app": "grok-register", "mail": MAIL_API_BASE},
    })
    last_hb = 0.0
    while True:
        try:
            if time.time() - last_hb > 30:
                post("/api/node/heartbeat", {"id": NODE_ID, "role": NODE_ROLE, "name": NODE_NAME, "status": "up", "code": 200, "selfcheck": selfcheck()})
                last_hb = time.time()
            data = get("/api/tasks/poll", {"role": NODE_ROLE, "node_id": NODE_ID})
            task = data.get("task")
            if task:
                log("got task", task.get("id"), task.get("type"))
                try:
                    result = handle_task(task)
                    post("/api/tasks/result", {"task_id": task["id"], "ok": True, "result": result, "node_id": NODE_ID})
                except Exception as e:
                    post("/api/tasks/result", {"task_id": task.get("id"), "ok": False, "error": str(e), "node_id": NODE_ID})
            else:
                time.sleep(POLL_INTERVAL)
        except Exception as e:
            log("loop error", repr(e))
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()

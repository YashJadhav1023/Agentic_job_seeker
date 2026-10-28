"""Built-in scheduler: runs the autopilot and/or the hiring-post scan every N hours, 24/7, inside the API process.

Off by default. Turn on in the profile:
  autopilot:  schedule_every_hours: 3      # 0 = off
  post_scan:  schedule_every_hours: 2      # 0 = off
A tick is skipped when the kill-switch (data/STOP) is on or a run that shares the same browser profile is still
running. Each run uses the profile's normal safety settings (dry-run unless the profile allows live actions).
Disable the thread entirely with JOBHUB_SCHEDULER=0.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta

import core

TICK_SEC = 60
JOBS = {"autopilot": ("autopilot", "autopilot_run"), "posts": ("post_scan", "scan_hiring_posts")}
_thread = None


def _due(key: str, hours: float, now: datetime) -> bool:
    last = core.kv_get(f"scheduler:{key}:last")
    return not last or now - datetime.fromisoformat(last) >= timedelta(hours=hours)


def tick(now: datetime | None = None) -> dict:
    """One scheduler pass. Returns {job: result} for jobs that were due. Safe to call from tests."""
    import autopilot
    import server
    now = now or datetime.now()
    try:
        p = core.load_profile()
    except Exception:  # noqa: BLE001 - a broken profile must not kill the thread
        return {}
    out = {}
    for key, (section, tool) in JOBS.items():
        hours = float((p.get(section) or {}).get("schedule_every_hours") or 0)
        if hours <= 0 or not _due(key, hours, now):
            continue
        if autopilot.stopped():
            out[key] = {"skipped": "kill-switch on"}
            continue
        res = server.HANDLERS[tool]({})
        if res.get("error") and "still running" in res["error"]:
            out[key] = {"skipped": res["error"]}
            continue  # try again on the next tick
        core.kv_set(f"scheduler:{key}:last", now.isoformat(timespec="seconds"))
        core.kv_set(f"scheduler:{key}:result", {"at": now.isoformat(timespec="seconds"),
                                                "run_id": res.get("run_id"), "error": res.get("error")})
        out[key] = res
    return out


def status() -> dict:
    try:
        p = core.load_profile()
    except Exception:  # noqa: BLE001
        p = {}
    out = {"thread_running": bool(_thread and _thread.is_alive())}
    for key, (section, _) in JOBS.items():
        hours = float((p.get(section) or {}).get("schedule_every_hours") or 0)
        last = core.kv_get(f"scheduler:{key}:last")
        nxt = (datetime.fromisoformat(last) + timedelta(hours=hours)).isoformat(timespec="minutes") if (last and hours) else None
        out[key] = {"every_hours": hours, "last": core.kv_get(f"scheduler:{key}:result"), "next": nxt if hours else None}
    return out


def _loop() -> None:
    while True:
        try:
            tick()
        except Exception as e:  # noqa: BLE001
            print(f"scheduler tick failed: {type(e).__name__}: {e}", flush=True)
        time.sleep(TICK_SEC)


def start() -> bool:
    global _thread
    if os.environ.get("JOBHUB_SCHEDULER", "1") == "0" or (_thread and _thread.is_alive()):
        return False
    _thread = threading.Thread(target=_loop, name="jobhub-scheduler", daemon=True)
    _thread.start()
    return True

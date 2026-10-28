"""Built-in scheduler, 24/7 inside the API process. Two kinds of timers:

ALARMS (like a phone alarm): at a clock time on chosen days, run a list of tasks one after another - find HR emails,
send emails, bulk email, LinkedIn apply, Naukri apply, post scan, global search, full autopilot. Every task becomes a
normal background run, so when several start together at most 3 execute and the rest wait in the run queue.
Manage them with alarm_set / alarm_list / alarm_delete / alarm_run_now (or the Schedule screen in the app).

INTERVALS: runs the autopilot and/or the hiring-post scan every N hours.

Off by default. Turn on in the profile:
  autopilot:  schedule_every_hours: 3      # 0 = off
  post_scan:  schedule_every_hours: 2      # 0 = off
A tick is skipped when the kill-switch (data/STOP) is on or a run that shares the same browser profile is still
running. Each run uses the profile's normal safety settings (dry-run unless the profile allows live actions).
Disable the thread entirely with JOBHUB_SCHEDULER=0.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
import time
from datetime import datetime, timedelta

import core

TICK_SEC = 60
QUEUE_TICK_SEC = 5
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
    for al in due_alarms(now):
        core.kv_set(f"alarm:{al['id']}:last", now.date().isoformat())  # once per day, even if a task fails
        if autopilot.stopped():
            out["alarm:" + al["id"]] = {"skipped": "kill-switch on"}
            continue
        res = run_alarm(al)
        core.kv_set(f"alarm:{al['id']}:result", {"at": now.isoformat(timespec="seconds"), "runs": res})
        out["alarm:" + al["id"]] = res
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


# ---------------------------------------------------------------- alarms
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
TASKS = {
    "find_emails": "Find HR emails",
    "send_emails": "Send emails to matched jobs",
    "bulk_email": "Bulk email to HR contacts",
    "linkedin_apply": "LinkedIn Easy Apply",
    "naukri_apply": "Naukri apply",
    "scan_posts": "Scan LinkedIn hiring posts",
    "global_search": "Search all boards",
    "autopilot": "Full autopilot",
}
CATCH_UP_MIN = 90   # a server that was down at the alarm time still runs it if it comes back within this window


def alarms() -> list:
    return core.kv_get("alarms", []) or []


def _save(items: list) -> None:
    core.kv_set("alarms", items)


def _clean(a: dict, existing: dict | None = None) -> dict:
    """Validate an alarm; raises ValueError with a message the app can show."""
    out = dict(existing or {})
    out.update({k: v for k, v in a.items() if k in ("id", "label", "time", "days", "tasks", "enabled", "dry_run", "bulk_limit",
                                                  "auto_apply")})
    t = str(out.get("time", "")).strip()
    try:
        hh, mm = t.split(":")
        assert 0 <= int(hh) <= 23 and 0 <= int(mm) <= 59
    except (ValueError, AssertionError):
        raise ValueError("time must be HH:MM (24 hour), e.g. 09:30") from None
    out["time"] = f"{int(hh):02d}:{int(mm):02d}"
    days = [str(d).lower()[:3] for d in (out.get("days") or [])]
    bad = [d for d in days if d not in DAYS]
    if bad:
        raise ValueError(f"unknown day(s) {bad}; use {DAYS}")
    out["days"] = [d for d in DAYS if d in days]           # empty = every day
    tasks = list(out.get("tasks") or [])
    if tasks == ["all"] or "all" in tasks:
        tasks = [t for t in TASKS if t not in ("global_search", "autopilot")]
    bad = [t for t in tasks if t not in TASKS]
    if bad or not tasks:
        raise ValueError(f"tasks must be a non-empty subset of {list(TASKS)} (or ['all'])")
    out["tasks"] = list(dict.fromkeys(tasks))
    out["enabled"] = bool(out.get("enabled", True))
    out["label"] = str(out.get("label") or " + ".join(TASKS[t] for t in out["tasks"][:2]))[:60]
    out["dry_run"] = None if out.get("dry_run") is None else bool(out["dry_run"])
    out["bulk_limit"] = max(1, min(int(out.get("bulk_limit") or 20), 100))
    out["auto_apply"] = bool(out.get("auto_apply", False))
    return out


def alarm_set(a: dict) -> dict:
    items = alarms()
    cur = next((x for x in items if x["id"] == a.get("id")), None) if a.get("id") else None
    if a.get("id") and not cur:
        return {"error": f"unknown alarm id {a['id']}"}
    try:
        al = _clean(a, cur)
    except ValueError as e:
        return {"error": str(e)}
    if not cur:
        al["id"] = "al-" + uuid.uuid4().hex[:6]
        items.append(al)
    else:
        items[items.index(cur)] = al
    _save(items)
    return {"alarm": al, "next": next_fire(al)}


def alarm_delete(a: dict) -> dict:
    items = alarms()
    keep = [x for x in items if x["id"] != a["id"]]
    if len(keep) == len(items):
        return {"error": f"unknown alarm id {a['id']}"}
    _save(keep)
    return {"deleted": a["id"]}


def next_fire(al: dict, now: datetime | None = None) -> str | None:
    if not al.get("enabled"):
        return None
    now = now or datetime.now()
    hh, mm = (int(x) for x in al["time"].split(":"))
    for add in range(0, 8):
        d = (now + timedelta(days=add)).replace(hour=hh, minute=mm, second=0, microsecond=0)
        pending_today = (add == 0 and d <= now <= d + timedelta(minutes=CATCH_UP_MIN)
                         and core.kv_get(f"alarm:{al['id']}:last") != now.date().isoformat())
        if not (d > now or pending_today):
            continue
        if not al["days"] or DAYS[d.weekday()] in al["days"]:
            return d.isoformat(timespec="minutes")
    return None


def _task_call(task: str, al: dict, p: dict) -> tuple:
    """-> (tool name, args, via_toolrun). Safety stays with the tools: dry-run default, caps, live-action switch."""
    dry = {} if al.get("dry_run") is None else {"dry_run": al["dry_run"]}
    if task == "find_emails":
        locs = [l for l in p["search"].get("locations") or [] if str(l).lower() not in ("all", "remote")] or ["Pune"]
        return "find_hr_emails", {"city": core.rotate("alarm_find_city", locs, 1)[0]}, False
    if task == "send_emails":
        return "autopilot_run", {"discover": False, "apply": False, "email": True, **dry}, False
    if task == "bulk_email":
        return "send_hr_emails", {"limit": al.get("bulk_limit", 20), **dry}, True
    if task == "linkedin_apply":
        return "apply_jobs", {"platform": "linkedin", **dry}, False
    if task == "naukri_apply":
        return "apply_jobs", {"platform": "naukri", **dry}, False
    if task == "scan_posts":
        return "scan_hiring_posts", {"auto_apply": al.get("auto_apply", False), **dry}, False
    if task == "global_search":
        return "global_search", {"auto_apply": al.get("auto_apply", False), **dry}, False
    return "autopilot_run", dict(dry), False


def run_alarm(al: dict) -> list:
    """Start every task of an alarm. Each becomes a queued/running background run; returns what happened to each."""
    import server
    p = core.load_profile()
    out = []
    for task in al["tasks"]:
        tool, args, via_run = _task_call(task, al, p)
        try:
            if via_run:
                res = core.spawn("task-" + tool, [core.PY, str(core.ROOT / "hub" / "toolrun.py"), tool, json.dumps(args)],
                                 core.ROOT, {"PYTHONUNBUFFERED": "1"}, bool(args.get("dry_run", p["safety"]["dry_run_default"])), args)
            else:
                res = server.HANDLERS[tool](args)
        except Exception as e:  # noqa: BLE001 - one broken task must not stop the others
            res = {"error": f"{type(e).__name__}: {e}"}
        out.append({"task": task, "run_id": res.get("run_id"), "queued": bool(res.get("queued")), "error": res.get("error")})
    return out


def alarm_run_now(a: dict) -> dict:
    if a.get("id"):
        al = next((x for x in alarms() if x["id"] == a["id"]), None)
        if not al:
            return {"error": f"unknown alarm id {a['id']}"}
    else:
        try:
            al = _clean({"time": "00:00", **a})
        except ValueError as e:
            return {"error": str(e)}
    if autopilot_stopped():
        return {"error": "kill-switch is on (data/STOP); turn it off first"}
    res = run_alarm(al)
    core.kv_set(f"alarm:{al.get('id', 'adhoc')}:result", {"at": datetime.now().isoformat(timespec="seconds"), "runs": res})
    return {"started": res}


def autopilot_stopped() -> bool:
    import autopilot
    return autopilot.stopped()


def due_alarms(now: datetime) -> list:
    out = []
    for al in alarms():
        if not al.get("enabled"):
            continue
        if al["days"] and DAYS[now.weekday()] not in al["days"]:
            continue
        hh, mm = (int(x) for x in al["time"].split(":"))
        at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if at <= now <= at + timedelta(minutes=CATCH_UP_MIN) and core.kv_get(f"alarm:{al['id']}:last") != now.date().isoformat():
            out.append(al)
    return out


def status() -> dict:
    try:
        p = core.load_profile()
    except Exception:  # noqa: BLE001
        p = {}
    out = {"thread_running": bool(_thread and _thread.is_alive()),
           "alarms": [dict(al, next=next_fire(al), last=core.kv_get(f"alarm:{al['id']}:result")) for al in alarms()]}
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


def _queue_loop() -> None:
    """Start queued runs as soon as a slot frees up (every few seconds), independent of the minute-level scheduler."""
    while True:
        try:
            core.dispatch_queue()
        except Exception as e:  # noqa: BLE001
            print(f"queue dispatch failed: {type(e).__name__}: {e}", flush=True)
        time.sleep(QUEUE_TICK_SEC)


def start() -> bool:
    global _thread
    if os.environ.get("JOBHUB_SCHEDULER", "1") == "0" or (_thread and _thread.is_alive()):
        return False
    try:
        core.clear_stale_browser_locks()  # locks left by a previous container / killed browser
    except Exception as e:  # noqa: BLE001
        print(f"stale lock cleanup failed: {e}", flush=True)
    threading.Thread(target=_queue_loop, name="jobhub-queue", daemon=True).start()
    _thread = threading.Thread(target=_loop, name="jobhub-scheduler", daemon=True)
    _thread.start()
    return True

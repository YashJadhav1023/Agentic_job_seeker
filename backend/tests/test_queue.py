"""Run queue: at most 3 runs execute at once, the rest wait in FIFO order and start by themselves."""
import os
import sys
from pathlib import Path

import core

PASS = [sys.executable, "-c", "pass"]


def _spawn(kind):
    return core.spawn(kind, PASS, core.ROOT, {}, True, {})


def _fake_running(monkeypatch, running: set):
    """Pretend the runs whose ids are in `running` are still executing; everything else has finished."""
    real = core.run_status

    def fake(rid, tail=1):
        r = real(rid, tail)
        if r.get("queued"):
            return r
        if "error" in r:
            return r
        r["running"] = rid in running
        return r

    monkeypatch.setattr(core, "run_status", fake)


def test_default_limit_is_six():
    assert core.MAX_PARALLEL == 6


def test_seventh_and_eighth_run_wait_in_fifo_order(monkeypatch):
    running = set()
    _fake_running(monkeypatch, running)
    ids = []
    for kind in ("career", "hr-pune", "verify", "hr-chennai", "hr-kolkata", "hr-hyderabad"):
        r = _spawn(kind)
        assert "queued" not in r
        running.add(r["run_id"])
        ids.append(r["run_id"])
    seventh, eighth = _spawn("hr-mumbai"), _spawn("hr-delhi")
    assert seventh["queued"] and seventh["position"] == 1
    assert eighth["queued"] and eighth["position"] == 2
    assert core.run_status(seventh["run_id"])["state"] == "queued"

    # nothing free yet: nothing starts
    assert core.dispatch_queue() == []
    # one run finishes -> exactly the oldest queued run starts
    running.discard(ids[0])
    assert core.dispatch_queue() == [seventh["run_id"]]
    running.add(seventh["run_id"])
    assert core.queue_position(eighth["run_id"]) == 1
    # two finish -> the last one starts
    running.discard(ids[1])
    assert core.dispatch_queue() == [eighth["run_id"]]


def test_queue_state_lists_running_and_waiting(monkeypatch):
    running = set()
    _fake_running(monkeypatch, running)
    for kind in ("career", "hr-pune", "verify", "hr-chennai", "hr-kolkata", "hr-hyderabad"):
        running.add(_spawn(kind)["run_id"])
    waiting = _spawn("hr-mumbai")
    st = core.queue_state()
    assert st["max_parallel"] == 6
    assert len(st["running"]) == 6
    assert [q["run_id"] for q in st["queued"]] == [waiting["run_id"]]


def test_cancel_removes_a_queued_run(monkeypatch):
    running = set()
    _fake_running(monkeypatch, running)
    for kind in ("career", "hr-pune", "verify", "hr-chennai", "hr-kolkata", "hr-hyderabad"):
        running.add(_spawn(kind)["run_id"])
    waiting = _spawn("hr-mumbai")
    assert core.cancel_run(waiting["run_id"])["cancelled"] is True
    assert core.queue_position(waiting["run_id"]) == 0
    assert "error" in core.run_status(waiting["run_id"])


def test_same_browser_profile_waits_but_unrelated_run_goes_ahead(monkeypatch):
    running = set()
    _fake_running(monkeypatch, running)
    first = _spawn("apply-naukri")
    running.add(first["run_id"])
    same = _spawn("apply-naukri")            # shares the Naukri profile
    other = _spawn("apply-indeed")           # different profile, free slot
    assert same["queued"] is True
    assert "queued" not in other


def test_child_of_a_running_parent_is_not_blocked_by_it(monkeypatch):
    """The autopilot starts the platform bots itself and waits for them; it must not block its own child."""
    running = set()
    _fake_running(monkeypatch, running)
    parent = _spawn("autopilot")
    running.add(parent["run_id"])
    monkeypatch.setenv("JOBHUB_RUN_ID", parent["run_id"])
    child = _spawn("apply-naukri")
    assert "queued" not in child, child
    monkeypatch.delenv("JOBHUB_RUN_ID")
    stranger = _spawn("apply-naukri")        # not started by the autopilot -> still waits for it
    assert stranger["queued"] is True


def test_stale_browser_locks_are_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "ROOT", tmp_path)
    prof = tmp_path / ".profiles" / "linkedin"
    prof.mkdir(parents=True)
    os.symlink("old-container-host-1868", prof / "SingletonLock")
    os.symlink("12345", prof / "SingletonCookie")
    keep = prof / "Cookies"
    keep.write_text("session")
    cleared = core.clear_stale_browser_locks()
    assert len(cleared) == 2
    assert not os.path.lexists(prof / "SingletonLock")
    assert keep.exists()


def test_locks_of_a_running_browser_are_kept(tmp_path, monkeypatch):
    import subprocess
    monkeypatch.setattr(core, "ROOT", tmp_path)
    prof = tmp_path / ".profiles" / "naukri"
    prof.mkdir(parents=True)
    os.symlink("host-1", prof / "SingletonLock")
    p = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(20)", f"--user-data-dir={prof}"])
    try:
        assert core.clear_stale_browser_locks() == []
        assert os.path.lexists(prof / "SingletonLock")
    finally:
        p.kill()
        p.wait()

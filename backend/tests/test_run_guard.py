"""Overlapping runs on one browser profile wait in the queue instead of crashing with a profile-lock error."""
import sys

import core


def _start(kind):
    # the test process itself is the "running" pid; no .exit file means the run counts as active
    return core.spawn(kind, [sys.executable, "-c", "pass"], core.ROOT, {}, True, {})


def test_same_kind_is_queued_while_running(monkeypatch):
    first = _start("autopilot")
    real = core.run_status
    monkeypatch.setattr(core, "_alive", lambda pid: True)
    monkeypatch.setattr(core, "run_status",
                        lambda rid, tail=1: {"running": True} if rid == first["run_id"] else real(rid, tail))
    again = core.spawn("autopilot", [sys.executable, "-c", "pass"], core.ROOT, {}, True, {})
    assert again.get("queued") and again["position"] == 1 and "error" not in again


def test_linkedin_runs_share_a_profile(monkeypatch):
    first = _start("posts")
    monkeypatch.setattr(core, "run_status", lambda rid, tail=1: {"running": rid == first["run_id"]})
    assert core.running_conflict("apply-linkedin") == first["run_id"]
    assert core.running_conflict("login-linkedin") == first["run_id"]
    assert core.running_conflict("apply-naukri") == ""


def test_finished_run_does_not_block(monkeypatch):
    first = _start("apply-naukri")
    monkeypatch.setattr(core, "run_status", lambda rid, tail=1: {"running": False})
    again = core.spawn("apply-naukri", [sys.executable, "-c", "pass"], core.ROOT, {}, True, {})
    assert "run_id" in again and again["run_id"] != first["run_id"]

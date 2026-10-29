import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hub"))
import login_helper  # noqa: E402


class FakeProc:
    def __init__(self, args, rc):
        self.args, self.returncode = args, rc

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self):
        pass


def _run(monkeypatch, tmp_path, rc, in_container, saved_before):
    monkeypatch.setattr(login_helper, "ROOT", tmp_path)
    monkeypatch.setattr(login_helper, "in_container", lambda: in_container)
    pw = types.SimpleNamespace(chromium=types.SimpleNamespace(executable_path="/chrome"))

    class Ctx:
        def __enter__(self):
            return pw

        def __exit__(self, *a):
            return False
    monkeypatch.setitem(sys.modules, "playwright.sync_api", types.SimpleNamespace(sync_playwright=lambda: Ctx()))
    if saved_before:
        (tmp_path / ".profiles" / "indeed" / "Default").mkdir(parents=True)
        (tmp_path / ".profiles" / "indeed" / "Default" / "Cookies").write_bytes(b"x")
    seen = {}

    def popen(args, stdin=None):
        seen["args"] = args
        return FakeProc(args, rc)
    monkeypatch.setattr(login_helper.subprocess, "Popen", popen)
    return login_helper.main("indeed"), seen["args"]


def test_in_container_chrome_runs_without_the_sandbox_and_opens_the_login_page(monkeypatch, tmp_path):
    code, args = _run(monkeypatch, tmp_path, rc=0, in_container=True, saved_before=True)
    assert "--no-sandbox" in args and args[-1] == login_helper.URLS["indeed"]


def test_outside_a_container_the_sandbox_stays_on(monkeypatch, tmp_path):
    _, args = _run(monkeypatch, tmp_path, rc=0, in_container=False, saved_before=True)
    assert "--no-sandbox" not in args


def test_a_chrome_crash_is_reported_even_with_an_old_saved_session(monkeypatch, tmp_path):
    code, _ = _run(monkeypatch, tmp_path, rc=133, in_container=True, saved_before=True)
    assert code == 1

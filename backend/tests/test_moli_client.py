import json
import pytest
from pathlib import Path
import moli_client as mc

def test_is_moli_available_disabled(monkeypatch):
    monkeypatch.setenv("JOBHUB_DISABLE_MOLI", "1")
    assert mc.is_moli_available() is False

def test_is_moli_available_offline():
    # Unless a server is actually listening on 9222 locally, it should return False
    # (or True if running, but not raise an exception)
    res = mc.is_moli_available()
    assert isinstance(res, bool)

def test_get_storage_state_path_nonexistent():
    assert mc.get_storage_state_path("nonexistent_platform_xyz") is None

def test_get_storage_state_path_existing(tmp_path, monkeypatch):
    monkeypatch.setattr(mc, "ROOT", tmp_path)
    profiles = tmp_path / ".profiles"
    profiles.mkdir(parents=True)
    state_file = profiles / "testplat_state.json"
    state_file.write_text(json.dumps({"cookies": [{"name": "test", "value": "123"}]}))
    
    p = mc.get_storage_state_path("testplat")
    assert p == state_file

def test_sync_moli_context_fallback_when_offline(monkeypatch):
    monkeypatch.setenv("JOBHUB_DISABLE_MOLI", "1")
    class DummyPlaywright:
        pass
    assert mc.sync_moli_context(DummyPlaywright(), "naukri") is None

@pytest.mark.anyio
async def test_async_moli_context_fallback_when_offline(monkeypatch):
    monkeypatch.setenv("JOBHUB_DISABLE_MOLI", "1")
    class DummyPlaywright:
        pass
    assert await mc.async_moli_context(DummyPlaywright(), "linkedin") is None

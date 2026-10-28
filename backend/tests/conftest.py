import sys
from pathlib import Path

import os

import pytest

os.environ.setdefault("JOBHUB_SCHEDULER", "0")  # no background scheduler thread in tests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hub"))
import core  # noqa: E402


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    """Every test gets its own empty hub DB and run dir; the real data/hub.db is never touched."""
    monkeypatch.setattr(core, "DB_PATH", tmp_path / "hub.db")
    monkeypatch.setattr(core, "RUNS", tmp_path)
    return tmp_path


@pytest.fixture
def profile():
    """Real candidate profile. Point JOBHUB_PROFILE at your profile.yaml to run the resume-based tests;
    with only profile.example.yaml (no resume PDF, e.g. in CI) they are skipped."""
    p = core.load_profile()
    if not Path(p["applicant"]["resume_path"]).exists():
        pytest.skip("resume PDF not available (set JOBHUB_PROFILE to a profile with a real resume_path)")
    return p

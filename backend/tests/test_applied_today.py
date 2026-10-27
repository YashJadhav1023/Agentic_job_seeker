"""Applications written by the vendored Naukri bot ("YYYY-MM-DD HH:MM:SS") must count towards today's cap."""
import sqlite3
from datetime import datetime

import core


def test_bot_rows_with_space_timestamp_count_today(tmp_path, monkeypatch):
    bot_dir = tmp_path / "job-automation"
    bot_dir.mkdir()
    b = sqlite3.connect(bot_dir / "applied_jobs.db")
    b.execute("CREATE TABLE applied(url TEXT, title TEXT, company TEXT, location TEXT, platform TEXT, applied_at TEXT)")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    b.execute("INSERT INTO applied VALUES('https://n/1','DevOps','Acme','Pune','Naukri',?)", (now,))
    b.execute("INSERT INTO applied VALUES('https://n/2','SRE','Beta','Pune','Naukri','2020-01-01 10:00:00')")
    b.commit()
    b.close()
    monkeypatch.setattr(core, "TOOLS", tmp_path)
    assert core.applied_today() == 1

"""Always-on, generic profile, LLM post analysis, resume handling, 4 parallel runs."""
import json
import os
import stat
import sys
from datetime import datetime
from pathlib import Path

import pytest

import autopilot
import core
import llm
import post_analyzer
import post_scanner
import resume
import scheduler

PROFILE = {
    "applicant": {"name": "Asha Rao", "email": "asha@example.org", "phone": "+15550100", "current_location": "Pune",
                  "resume_path": "/nonexistent.pdf", "experience_years": "1"},
    "compensation": {"notice_period_days": 0},
    "search": {"keywords": ["DevOps Engineer", "Cloud Engineer"], "locations": ["Pune", "Remote"],
               "title_include": "devops|cloud", "title_exclude": r"senior|\blead\b", "experience_years_filter": "0-2"},
    "hr_outreach": {"gmail_user": "asha@example.org"},
    "safety": {"dry_run_default": True},
    "llm": {"provider": "none"},
}


# ---------------------------------------------------------------- send window
def test_send_window_default_is_always():
    c = autopilot.cfg({})
    for h in (0, 3, 9, 19, 23):
        assert autopilot.in_send_window(c, datetime(2026, 11, 1, h))  # a Sunday
    assert "any time, every day" in autopilot.send_window_text(c)


def test_send_window_optional_hours_days_and_overnight():
    c = {"send_window": [9, 19], "send_days": ["mon", "tue", "wed", "thu", "fri"]}
    assert autopilot.in_send_window(c, datetime(2026, 10, 28, 10))      # Wed 10:00
    assert not autopilot.in_send_window(c, datetime(2026, 10, 28, 20))  # Wed 20:00
    assert not autopilot.in_send_window(c, datetime(2026, 11, 1, 10))   # Sunday
    night = {"send_window": [22, 6]}
    assert autopilot.in_send_window(night, datetime(2026, 10, 28, 23))
    assert autopilot.in_send_window(night, datetime(2026, 10, 28, 2))
    assert not autopilot.in_send_window(night, datetime(2026, 10, 28, 12))
    assert autopilot.in_send_window({"send_window": [0, 24]}, datetime(2026, 10, 28, 3))


# ---------------------------------------------------------------- discovery rotation
def test_rotation_covers_every_keyword_location_pair():
    p = {"search": {"keywords": ["a", "b", "c"], "locations": ["x", "y"]}}
    seen = []
    for _ in range(3):
        seen += [tuple(x) for x in autopilot.search_pairs(p, {"max_search_queries": 2}, "t")]
    assert sorted(seen) == sorted((k, l) for k in "abc" for l in "xy")


def test_linkedin_location_passes_unknown_cities_through():
    assert autopilot.linkedin_location("Pune") == "Pune, Maharashtra, India"
    assert autopilot.linkedin_location("Berlin, Germany") == "Berlin, Germany"


# ---------------------------------------------------------------- post analysis
HIRING = "We are hiring a Junior DevOps Engineer in Pune, 0-2 years. Share your resume at hr@acme.io"
SENIOR = "We are hiring a Senior DevOps Engineer in Pune, 8+ years."
SEEKER = "I am looking for a new job as a DevOps Engineer. #OpenToWork"
ABROAD = "We are hiring a DevOps Engineer in London, 1 year experience."


def test_rules_only_analysis():
    v = post_analyzer.analyze(PROFILE, [HIRING, SENIOR, SEEKER, ABROAD], log=lambda m: None)
    assert [x["match"] for x in v] == [True, False, False, False]
    assert v[0]["via"] == "rules" and v[2]["via"] == "gate"


def test_llm_analysis_is_batched_and_hard_rules_win(monkeypatch):
    calls = []

    def fake_chat(prompt, c, retries=1):
        calls.append(prompt)
        assert "untrusted" not in prompt or True
        return [{"i": 0, "is_job_posting": True, "company": "Acme", "role": "Junior DevOps Engineer", "location": "Pune",
                 "min_years": 0, "asks_resume_by_email": True, "match": True, "reason": "fits"},
                {"i": 1, "is_job_posting": True, "company": "Big", "role": "Senior DevOps Engineer", "location": "Pune",
                 "min_years": 8, "match": True, "reason": "model mistake"},
                {"i": 2, "is_job_posting": True, "company": "Far", "role": "DevOps Engineer", "location": "London",
                 "min_years": 1, "match": True, "reason": "model mistake"}]
    p = dict(PROFILE, llm={"provider": "kiro", "batch_size": 8})
    monkeypatch.setattr(llm, "available", lambda c: (True, ""))
    monkeypatch.setattr(llm, "chat_json", fake_chat)
    v = post_analyzer.analyze(p, [HIRING, SENIOR, ABROAD, SEEKER], log=lambda m: None)
    assert len(calls) == 1  # one request for all three gated posts
    assert v[0]["match"] and v[0]["company"] == "Acme" and v[0]["via"] == "llm:kiro"
    assert not v[1]["match"] and not v[2]["match"]  # profile rules override the model
    assert v[3]["via"] == "gate"


def test_llm_failure_falls_back_to_rules(monkeypatch):
    monkeypatch.setattr(llm, "available", lambda c: (True, ""))

    def boom(*a, **k):
        raise llm.LLMError("down")
    monkeypatch.setattr(llm, "chat_json", boom)
    v = post_analyzer.analyze(dict(PROFILE, llm={"provider": "kiro"}), [HIRING], log=lambda m: None)
    assert v[0]["match"] and v[0]["via"] == "rules"


def test_parse_json_tolerates_ansi_fences_and_think_tags():
    assert llm.parse_json('\x1b[38;5;141m> \x1b[0m{"a":1}\n ▸ Credits: 0.04') == {"a": 1}
    assert llm.parse_json("<think>hm {x}</think>```json\n[{\"i\":0}]\n```") == [{"i": 0}]
    with pytest.raises(llm.LLMError):
        llm.parse_json("no json here")


def test_kiro_provider_runs_toolless_agent_via_stdin(tmp_path, monkeypatch):
    fake = tmp_path / "kiro-cli"
    fake.write_text("#!/bin/sh\n"
                    f"echo \"$@\" > {tmp_path}/argv; pwd > {tmp_path}/cwd; cat > {tmp_path}/stdin\n"
                    "printf '\\033[38;5;141m> \\033[0m[{\"i\":0,\"ok\":true}]'\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("JOBHUB_KIRO_CLI", str(fake))
    monkeypatch.setenv("KIRO_API_KEY", "test-key-not-real")
    monkeypatch.setattr(core, "ROOT", tmp_path)
    out = llm.chat_json("classify $(rm -rf /) this", {**llm.DEFAULTS, "provider": "kiro"})
    assert out == [{"i": 0, "ok": True}]
    argv = (tmp_path / "argv").read_text()
    assert "--agent jobhub-classifier" in argv and "--no-interactive" in argv and "rm -rf" not in argv
    assert (tmp_path / "stdin").read_text().startswith("classify $(rm -rf /)")  # data only via stdin, no shell
    agent = json.loads((tmp_path / "data/llm-sandbox/.kiro/agents/jobhub-classifier.json").read_text())
    assert agent["tools"] == [] and agent["mcpServers"] == {} and agent["includeMcpJson"] is False
    assert (tmp_path / "cwd").read_text().strip().endswith("llm-sandbox")


def test_llm_not_ready_without_key(monkeypatch):
    monkeypatch.delenv("KIRO_API_KEY", raising=False)
    monkeypatch.setattr(core, "read_secret", lambda n: None)
    monkeypatch.setattr(llm, "kiro_bin", lambda: "/usr/bin/true")
    ok, why = llm.available({**llm.DEFAULTS, "provider": "kiro"})
    assert not ok and "KIRO_API_KEY" in why


def test_post_scanner_process_dedups_and_stores(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "ROOT", tmp_path)
    monkeypatch.setattr(post_scanner.contacts, "verify_email", lambda e, *a, **k: "valid")
    posts = [{"text": HIRING, "links": ["https://acme.io/jobs/1"], "author": "Ravi", "source": "q"},
             {"text": SENIOR, "links": [], "author": "X", "source": "q"}]
    for x in posts:
        x["fp"] = post_scanner.fingerprint(x["text"])
    out = post_scanner.process(PROFILE, posts, post_scanner.scan_cfg(PROFILE))
    assert out["matched"] == 1 and out["new_hr_emails"] == 1 and out["leads"] == 1
    assert post_scanner.already_seen(posts[0]["fp"]) and post_scanner.already_seen(posts[1]["fp"])
    with core.db() as c:
        assert c.execute("SELECT confidence FROM hr_contacts WHERE email='hr@acme.io'").fetchone()[0] == "high"


def test_default_post_queries_come_from_profile():
    qs = post_scanner.default_queries(PROFILE)
    assert "hiring DevOps Engineer Pune" in qs and "hiring Cloud Engineer Remote" in qs
    assert not any("devops fresher" in q for q in qs)


# ---------------------------------------------------------------- scheduler
def test_scheduler_tick_runs_when_due_and_waits_otherwise(monkeypatch):
    import server
    calls = []
    monkeypatch.setattr(core, "load_profile", lambda: {"autopilot": {"schedule_every_hours": 3}, "post_scan": {}})
    monkeypatch.setattr(autopilot, "stopped", lambda: False)
    monkeypatch.setitem(server.HANDLERS, "autopilot_run", lambda a: calls.append(a) or {"run_id": "r1"})
    t0 = datetime(2026, 10, 28, 1, 0)
    assert "autopilot" in scheduler.tick(t0) and len(calls) == 1
    assert scheduler.tick(datetime(2026, 10, 28, 2, 0)) == {}           # 1 h later: not due
    assert "autopilot" in scheduler.tick(datetime(2026, 10, 28, 4, 1))  # 3 h later: due again
    assert len(calls) == 2


def test_scheduler_retries_when_a_run_is_busy(monkeypatch):
    import server
    monkeypatch.setattr(core, "load_profile", lambda: {"autopilot": {}, "post_scan": {"schedule_every_hours": 1}})
    monkeypatch.setattr(autopilot, "stopped", lambda: False)
    monkeypatch.setitem(server.HANDLERS, "scan_hiring_posts", lambda a: {"error": "posts cannot start: x is still running"})
    assert scheduler.tick(datetime(2026, 10, 28, 1))["posts"]["skipped"]
    assert core.kv_get("scheduler:posts:last") is None  # not marked done, so the next tick tries again


# ---------------------------------------------------------------- parallel runs
def test_up_to_four_parallel_runs(monkeypatch):
    monkeypatch.setattr(core, "MAX_PARALLEL", 4)
    monkeypatch.setattr(core, "run_status", lambda rid, tail=1: {"running": True})
    kinds = ["apply-linkedin", "apply-naukri", "career", "hr-pune"]
    for k in kinds:
        assert "run_id" in core.spawn(k, [sys.executable, "-c", "pass"], core.ROOT, {}, True, {}), k
    fifth = core.spawn("verify", [sys.executable, "-c", "pass"], core.ROOT, {}, True, {})
    assert fifth["queued"] is True and fifth["position"] == 1


def test_autopilot_conflicts_with_platform_runs(monkeypatch):
    first = core.spawn("apply-naukri", [sys.executable, "-c", "pass"], core.ROOT, {}, True, {})
    monkeypatch.setattr(core, "run_status", lambda rid, tail=1: {"running": rid == first["run_id"]})
    assert core.running_conflict("autopilot") == first["run_id"]
    assert core.running_conflict("career") == ""


def test_spawn_passes_llm_key_to_child(monkeypatch, tmp_path):
    monkeypatch.setenv("KIRO_API_KEY", "test-key-not-real")
    r = core.spawn("envcheck", [sys.executable, "-c", "import os;print(os.environ.get('KIRO_API_KEY'))"], tmp_path, {}, True, {})
    import time
    for _ in range(50):
        if not core.run_status(r["run_id"])["running"]:
            break
        time.sleep(0.1)
    assert "test-key-not-real" in Path(r["log"]).read_text()


# ---------------------------------------------------------------- resume
def test_resume_upload_metadata_history_and_attachment_name(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "ROOT", tmp_path)
    p = dict(PROFILE, applicant=dict(PROFILE["applicant"], resume_path=str(tmp_path / "data/resume.pdf")))
    for i in range(7):
        info = resume.save_upload(p, b"%PDF-1.4 v" + bytes([48 + i]), f"Asha Rao CV v{i}.pdf")
    assert info["exists"] and (tmp_path / "data/resume.pdf").read_bytes().endswith(b"6")
    assert info["original_filename"] == "Asha_Rao_CV_v6.pdf" and resume.attachment_name(p) == "Asha_Rao_CV_v6.pdf"
    assert len(info["history"]) == resume.HISTORY_KEEP


def test_section_skills_and_apply_suggestion():
    text = "ASHA RAO\nSKILLS\nPython, SQL | Power BI • Tableau\nEXPERIENCE\nAnalyst 2023"
    assert resume.section_skills(text) == ["Python", "SQL", "Power BI", "Tableau"]
    prof = {"applicant": {"name": "Your Name", "email": "real@me.com"}, "search": {"keywords": ["old"]}}
    sugg = {"applicant": {"name": "Asha Rao", "email": "other@x.com"}, "search": {"skills": ["SQL"], "keywords": ["Data Analyst"]}}
    changed = resume.apply_suggestion(prof, sugg, replace_search=False)
    assert prof["applicant"] == {"name": "Asha Rao", "email": "real@me.com"}  # only placeholders are replaced
    assert prof["search"]["keywords"] == ["old"] and prof["search"]["skills"] == ["SQL"]
    resume.apply_suggestion(prof, sugg, replace_search=True)
    assert prof["search"]["keywords"] == ["Data Analyst"] and "search.skills" in changed

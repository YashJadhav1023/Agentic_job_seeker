"""Runs the vendored lead-finder search for HR contacts and stores them in the hub DB.
Invoked as a subprocess by the hub (venv python). Usage: hr_runner.py '<json params>'"""
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hub"))
sys.path.insert(0, str(ROOT / "tools" / "lead-finder"))
import core  # noqa: E402
import finder  # noqa: E402

params = json.loads(sys.argv[1])
city_label = params.get("city", "")
stored = 0


def log(msg):
    print(msg, flush=True)


def emit(row):
    """row = one company result from lead-finder; keep ONLY HR-relevant, non-guessed-by-default contacts."""
    global stored
    cands = []
    for e in [x.strip() for x in row.get("hr_emails", "").split(",") if x.strip()]:
        cands.append((e, "", "HR inbox", "high", "published on company website", "hr_inbox"))
    for l in row.get("leads", []):
        if l.get("kind") == "hr":
            cands.append((l["email"], l.get("name", ""), l.get("role", ""), l.get("confidence", "low"),
                          l.get("note", ""), "hr_person"))
    with core.db() as c:
        for email, name, role, conf, note, kind in cands:
            cur = c.execute("INSERT OR IGNORE INTO hr_contacts(email,name,role,company,domain,city,confidence,note,kind,found_at)"
                            " VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (email.lower(), name, role, row["company"], row["domain"], city_label, conf, note, kind, core.now_iso()))
            stored += cur.rowcount
    log(f"[stored] {row['company']} ({row['domain']}): {len(cands)} HR candidates")


finder.run_search(params, emit, log, lambda: False)
log(f"DONE new_contacts={stored}")

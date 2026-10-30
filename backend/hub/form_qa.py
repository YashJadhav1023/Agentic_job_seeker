"""Form question memory: answers you gave once are reused on every later application form.

When career_apply meets a REQUIRED question that neither the profile nor the resume (nor the LLM, if set) can answer, it
stops that form (it never guesses) and records the question here. You answer it once (app: All Tools -> form_questions /
form_answer, or MCP), and from then on every form that asks the same question gets your answer.
Questions are matched on a normalised key (case, spacing, punctuation and a trailing * do not matter).
"""
from __future__ import annotations

import json
import re

import core

_TABLE = """CREATE TABLE IF NOT EXISTS form_qa(
  id INTEGER PRIMARY KEY, qkey TEXT UNIQUE, question TEXT, options TEXT, answer TEXT, company TEXT, seen INTEGER DEFAULT 1,
  asked_at TEXT, answered_at TEXT)"""


def key(question: str) -> str:
    q = re.sub(r"[^a-z0-9+#]+", " ", str(question or "").lower().split("|")[0])
    return re.sub(r"\s+", " ", q).strip()[:200]


def _db():
    c = core.db()
    c.execute(_TABLE)
    return c


def answers() -> dict:
    """{question key: answer} for every answered question (loaded once per batch into the form facts)."""
    with _db() as c:
        return {r["qkey"]: r["answer"] for r in c.execute("SELECT qkey, answer FROM form_qa WHERE answer IS NOT NULL AND answer<>''")}


def record_unanswered(question: str, options: list, company: str = "") -> None:
    k = key(question)
    if len(k) < 3:
        return
    with _db() as c:
        if c.execute("UPDATE form_qa SET seen=seen+1 WHERE qkey=?", (k,)).rowcount:
            return
        c.execute("INSERT INTO form_qa(qkey,question,options,company,asked_at) VALUES(?,?,?,?,?)",
                  (k, str(question)[:300], json.dumps([str(o)[:120] for o in (options or [])][:30]), str(company)[:120], core.now_iso()))


def pending() -> list:
    with _db() as c:
        rows = c.execute("SELECT * FROM form_qa WHERE answer IS NULL OR answer='' ORDER BY seen DESC, id").fetchall()
    return [dict(r, options=json.loads(r["options"] or "[]")) for r in rows]


def all_questions() -> list:
    with _db() as c:
        rows = c.execute("SELECT * FROM form_qa ORDER BY id DESC").fetchall()
    return [dict(r, options=json.loads(r["options"] or "[]")) for r in rows]


def save_answer(qid: int, answer: str) -> bool:
    answer = str(answer or "").strip()[:1000]
    if not answer:
        raise ValueError("answer is empty")
    with _db() as c:
        return bool(c.execute("UPDATE form_qa SET answer=?, answered_at=? WHERE id=?", (answer, core.now_iso(), qid)).rowcount)

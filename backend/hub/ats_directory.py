"""Company -> its own job board, from the bundled open directory (vendor/ats-directory, see NOTICE.md).

lookup("State Street") -> [("workday", "https://statestreet.wd1.myworkdayjobs.com/global")]
"""
from __future__ import annotations

import gzip
import json
import re
from functools import lru_cache
from pathlib import Path

FILE = Path(__file__).resolve().parent.parent / "vendor" / "ats-directory" / "directory.json.gz"
SUFFIX_RX = re.compile(r"\b(private|pvt|limited|ltd|llp|llc|inc|corp(oration)?|technologies|technology|solutions|software|"
                       r"services|systems|india|global|group|labs?|co|company|careers?|jobs|job opportunities)\b\.?", re.I)


def norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", SUFFIX_RX.sub(" ", (name or "").lower()))


@lru_cache(maxsize=1)
def _data() -> dict:
    try:
        with gzip.open(FILE, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"names": {}, "slugs": {}}


def lookup(company: str, slugs: list | None = None) -> list:
    """-> [(ats, board_url)], exact name first, then slug matches. Empty when the company is not in the directory."""
    d = _data()
    out = [tuple(x) for x in d["names"].get(norm(company), [])]
    for s in slugs or []:
        for ats, table in d["slugs"].items():
            url = table.get(s.lower())
            if url and (ats, url) not in out:
                out.append((ats, url))
    return out

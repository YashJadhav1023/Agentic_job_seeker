#!/usr/bin/env python3
"""Build vendor/ats-directory/directory.json.gz: which job-board system (ATS) each company uses, and its board URL.

Sources (both MIT, see vendor/ats-directory/NOTICE.md):
  * kalil0321/ats-scrapers  ats-companies/<ats>.csv          name,slug,url   (name -> board URL)
  * Feashliaa/job-board-aggregator data/<ats>_companies.json  slugs only     (slug -> board URL)

Run again to refresh:  python scripts/build_ats_directory.py
"""
import csv
import gzip
import io
import json
import re
import sys
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "vendor" / "ats-directory" / "directory.json.gz"
ATS_SCRAPERS = "https://raw.githubusercontent.com/kalil0321/ats-scrapers/HEAD/ats-companies/{}.csv"
FEASHLIAA = "https://raw.githubusercontent.com/Riley-Dorrington/job-board-aggregator/main/data/{}_companies.json"
NAMED = ["workday", "oracle", "greenhouse", "lever", "ashby", "smartrecruiters", "workable", "successfactors", "icims",
         "darwinbox", "keka", "taleo", "recruitee", "teamtailor", "bamboohr", "jobvite", "phenom", "eightfold"]
SLUG_ONLY = {"workday": lambda s: "https://{0}.{1}.myworkdayjobs.com/{2}".format(*s.split("|")) if s.count("|") == 2 else "",
             "greenhouse": lambda s: f"https://job-boards.greenhouse.io/{s}",
             "lever": lambda s: f"https://jobs.lever.co/{s}",
             "ashby": lambda s: f"https://jobs.ashbyhq.com/{s}"}
SUFFIX_RX = re.compile(r"\b(private|pvt|limited|ltd|llp|llc|inc|corp(oration)?|technologies|technology|solutions|software|"
                       r"services|systems|india|global|group|labs?|co|company|careers?|jobs|job opportunities)\b\.?", re.I)


def norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", SUFFIX_RX.sub(" ", (name or "").lower()))


def get(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "ajs-directory"}), timeout=60) as r:
        return r.read()


def main() -> int:
    names, slugs = {}, {}
    for ats in NAMED:
        try:
            rows = csv.DictReader(io.StringIO(get(ATS_SCRAPERS.format(ats)).decode("utf-8", "replace")))
        except Exception as e:  # noqa: BLE001
            print(f"skip {ats}: {e}", file=sys.stderr)
            continue
        n = 0
        for r in rows:
            url, key = (r.get("url") or "").strip(), norm(r.get("company_name") or r.get("name") or "")
            if url.startswith("https://") and len(key) >= 2:
                names.setdefault(key, [])
                if [ats, url] not in names[key]:
                    names[key].append([ats, url])
                    n += 1
            sl = (r.get("slug") or "").split("/")[0].lower()
            if sl and url.startswith("https://"):
                slugs.setdefault(ats, {}).setdefault(sl, url)
        print(f"{ats}: {n} named boards")
    for ats, make in SLUG_ONLY.items():
        try:
            items = json.loads(get(FEASHLIAA.format(ats)))
        except Exception as e:  # noqa: BLE001
            print(f"skip feashliaa {ats}: {e}", file=sys.stderr)
            continue
        before = len(slugs.get(ats, {}))
        for s in items:
            url = make(str(s))
            if url:
                slugs.setdefault(ats, {}).setdefault(str(s).split("|")[0].lower(), url)
        print(f"feashliaa {ats}: +{len(slugs[ats]) - before} slugs")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUT, "wt", encoding="utf-8") as fh:
        json.dump({"names": names, "slugs": slugs}, fh, separators=(",", ":"))
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB): {len(names)} names, {sum(map(len, slugs.values()))} slugs")
    return 0


if __name__ == "__main__":
    sys.exit(main())

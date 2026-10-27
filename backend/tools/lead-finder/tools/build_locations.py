"""Split the countries+states+cities dataset into small per-country files for the UI.

Source: https://github.com/dr5hn/countries-states-cities-database (json/countries+states+cities.json)
Usage:  python tools/build_locations.py path/to/countries+states+cities.json
"""
import json
import sys
from pathlib import Path

out = Path(__file__).resolve().parent.parent / "static" / "loc"
out.mkdir(parents=True, exist_ok=True)

data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
index = []
for c in sorted(data, key=lambda c: c["name"]):
    states = [{"n": s["name"], "c": sorted({x["name"] for x in s.get("cities", [])})}
              for s in sorted(c.get("states", []), key=lambda s: s["name"])]
    index.append({"n": c["name"], "i": c["iso2"], "f": c.get("emoji", ""), "s": len(states)})
    (out / f"{c['iso2']}.json").write_text(json.dumps(states, ensure_ascii=False, separators=(",", ":")),
                                           encoding="utf-8")
(out / "countries.json").write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")),
                                    encoding="utf-8")
print(f"wrote {len(index)} countries to {out}")

"""Global search covers every city the user picked (any city, not a fixed list)."""
import json

import core
import globalsearch
import server

P = {"search": {"keywords": ["DevOps Engineer", "Cloud Engineer", "SRE"], "locations": ["Pune", "Mumbai"]}}


def test_explicit_locations_are_all_searched_even_past_the_pair_limit():
    opts = {"locations": ["Pune", "Mumbai", "Hyderabad", "Chennai", "Noida", "Kochi", "Remote"]}
    got = globalsearch.pairs(P, opts)
    assert {l for _, l in got} == set(opts["locations"])            # every picked city in this run


def test_profile_locations_rotate_but_every_run_covers_every_city():
    for _ in range(3):
        got = globalsearch.pairs({"search": {**P["search"], "keywords": [f"k{i}" for i in range(9)],
                                             "locations": ["Pune", "Mumbai", "Hyderabad", "Bangalore"]}}, {})
        assert {l for _, l in got} == {"Pune", "Mumbai", "Hyderabad", "Bangalore"}


def test_comma_separated_location_text_and_dedupe():
    got = globalsearch.pairs(P, {"location": "Chennai, Gurgaon ,chennai", "query": "SRE"})
    assert got == [("SRE", "Chennai"), ("SRE", "Gurgaon")]


def test_server_passes_locations_and_auto_apply(monkeypatch):
    seen = {}
    monkeypatch.setattr(core, "spawn", lambda kind, argv, cwd, env, dry, opts: seen.update(opts=json.loads(argv[-1])) or {"run_id": "r"})
    server.global_search({"locations": ["Pune", "Indore"], "auto_apply": True})
    assert seen["opts"]["locations"] == ["Pune", "Indore"] and seen["opts"]["auto_apply"] is True
    assert "locations" in server.TOOLS["global_search"][1]["properties"]

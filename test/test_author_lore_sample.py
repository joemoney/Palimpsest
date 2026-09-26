"""CR-06 lore highlighting in the sample state: `author_evaluate.lore_injection` says which entries
the design would inject for the sample and the text on the page - key matches (whole word,
case-insensitive), also_when through the real condition code, unlock keeping an entry dormant,
priority ranking and the max_active cutoff. Evaluates the design, since keyed_lore is not built.

Run directly: python3 test/test_author_lore_sample.py
"""
import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _llm_stubs  # noqa: E402,F401
import author_evaluate  # noqa: E402

STORY = {
    "schema_version": 3, "meta": {"title": "T"},
    "protagonist": {"stats": {"quorum": 20}},
    "mechanics": {
        "stats": {"engine": "bounded_counter", "axes": {"quorum": {}}},
        "lore": {"engine": "keyed_lore", "max_active": 2, "entries": [
            {"id": "lark_fixed", "priority": 90, "keys": ["Lark", "the diver"],
             "also_when": {"flag": "lark_aboard"}, "content": "c"},
            {"id": "board", "priority": 50, "keys": ["Board"], "content": "c"},
            {"id": "tally", "priority": 70, "keys": ["Tally"], "content": "c"},
            {"id": "unease", "priority": 60, "keys": ["Descant"],
             "unlock": {"stat": "quorum", "gte": 65}, "content": "c"},
            {"id": "nokeys", "priority": 10, "content": "c"},
        ]},
        "flags": {"declared": [{"id": "lark_aboard", "detect": "d"}]},
    },
}


def states(sample, story=STORY):
    return {r["id"]: r["state"] for r in author_evaluate.lore_injection(story, sample)}


assert author_evaluate.lore_injection({"schema_version": 3, "meta": {"title": "T"}}, {}) == []
assert set(states({}).values()) == {"idle", "dormant"} and states({})["unease"] == "dormant"
print("OK: no text on the page triggers nothing; a story with no lore returns nothing")

got = states({"lore_text": "I ask LARK about the wreck"})
assert got["lark_fixed"] == "injected" and got["board"] == "idle" and got["tally"] == "idle", got
assert states({"lore_text": "Larkspur blooms"})["lark_fixed"] == "idle", "a key is a whole word, not a substring"
assert states({"lore_text": "the diverse crew"})["lark_fixed"] == "idle"
assert states({"lore_text": "then the diver surfaced"})["lark_fixed"] == "injected", "a multi-word key matches"
print("OK: keys match case-insensitively as whole words or phrases")

got = author_evaluate.lore_injection(STORY, {"flags": ["lark_aboard"], "lore_text": "nothing named"})
by = {r["id"]: r for r in got}
assert by["lark_fixed"]["state"] == "injected" and by["lark_fixed"]["why"] == ["its also-when condition holds"], by
print("OK: also_when injects an entry with no key on the page (the Lark-introduction acceptance)")

got = states({"lore_text": "Lark, the Board and Tally"})
assert got["lark_fixed"] == "injected" and got["tally"] == "injected" and got["board"] == "cut", got
print("OK: max_active cuts the lowest priority among the triggered")

dormant = states({"lore_text": "Descant hums"})
assert dormant["unease"] == "dormant", "an unlock that does not hold never injects, even with its key on the page"
assert states({"lore_text": "Descant hums", "stats": {"quorum": 70}})["unease"] == "injected"
print("OK: unlock keeps an entry dormant until its condition holds")

many = copy.deepcopy(STORY)
del many["mechanics"]["lore"]["max_active"]
many["mechanics"]["lore"]["entries"] += [{"id": f"x{i}", "priority": 1, "keys": ["zed"], "content": "c"} for i in range(5)]
got = states({"lore_text": "zed"}, many)
assert sorted(v for k, v in got.items() if k.startswith("x")).count("injected") == 3, got
print("OK: with no max_active the spec's 3 applies")

# it never mutates its inputs, and a hand-typed sample cannot crash it
frozen = copy.deepcopy(STORY)
author_evaluate.lore_injection(STORY, {"lore_text": ["not", "text"], "stats": "x", "flags": 5})
assert STORY == frozen
print("OK: a malformed sample is tolerated and nothing is mutated")

print("\nALL CHECKS PASSED: test_author_lore_sample")

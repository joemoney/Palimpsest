"""backend/conditions.py - CR-02's shared condition grammar (Authoring Tool decision D2).

One test per leaf, one per polarity, plus the things the phase gate names: an unknown flag reads
*false* in `ready_when` and *true* in a gate, and "SYNC 85 and `lark_departed` set" prunes an
ending's `viable_while`. Offline; no LLM, no network.

Run directly: python3 test/test_conditions.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import load_story_engine  # noqa: E402

se = load_story_engine()  # puts backend/ on sys.path and imports the flat modules
import conditions  # noqa: E402
from conditions import CLOSED, OPEN  # noqa: E402

STORY = {
    "mechanics": {
        "stats": {"engine": "bounded_counter", "floor": 0, "ceiling": 100, "axes": {
            "sync": {"tiers": [{"at": 0, "label": "loose"}, {"at": 50, "label": "seated"},
                               {"at": 80, "label": "fused"}]},
            "reach": {},
        }},
        "relationships": {"engine": "scored_axis", "scale": {"min": -100, "max": 100},
                          "axis": {"negative": "cold", "positive": "warm", "description": "regard"},
                          "registers": {"helped": 5},
                          "tiers": [{"at": 25, "label": "warm"}, {"at": -25, "label": "cold"}]},
        "revelations": {"engine": "triggered_reveal", "entries": [
            {"id": "frag_1", "trigger": "t", "content": "c"},
            {"id": "frag_2", "trigger": "t", "content": "c"}]},
        "flags": {"declared": [{"id": "lark_departed", "detect": "Lark left for good"},
                               {"id": "lark_aboard", "detect": "Lark boards"}]},
    },
    "world": {"characters": {"Lark": {}, "Descant": {}}},
    "character_creation": [{"key": "trade", "options": [{"id": "listener"}, {"id": "patcher"}]}],
    "plot": {"subplots": {"sp_a": {}, "sp_b": {}}},
}


def ctx(**over):
    state = {
        "protagonist": {"stats": {"sync": 60, "reach": 10}, "flags": {"active": {}, "archive": {}},
                        "inventory": [], "creation_choices": {"trade": "listener"}, "leverage": []},
        "plot": {"revelations_revealed": {"frag_1": {"turn": 3}}, "current_act": 2,
                 "subplots": {"sp_a": {"status": "completed", "progress": 5},
                              "sp_b": {"status": "active", "progress": 1}}},
        "pacing": {"turn_count": 40},
        "characters": {"Lark": {"relationship": 30, "peak": 60}},
    }
    for k, v in over.items():
        state[k] = v
    return {"story": STORY, "state": state}


def sat(cond, c=None, pol=OPEN, ending=None):
    return conditions.evaluate(cond, c or ctx(), pol, ending)


def yes(cond, **kw):
    r = sat(cond, **kw)
    assert r.satisfied is True and not r.unknown, (cond, r)


def no(cond, **kw):
    r = sat(cond, **kw)
    assert r.satisfied is False and not r.unknown, (cond, r)


# --- polarity is required ---------------------------------------------------------------------
try:
    conditions.evaluate({"flag": "x"}, ctx())
    raise AssertionError("polarity must be a required argument")
except TypeError:
    pass
try:
    conditions.evaluate({"flag": "x"}, ctx(), "maybe")
    raise AssertionError("an unknown polarity must raise")
except ValueError:
    pass
print("OK: polarity is a required argument, and only open/closed are accepted")

# --- absent/empty ------------------------------------------------------------------------------
for empty in (None, {}, [], ""):
    assert sat(empty, pol=CLOSED).satisfied is True
print("OK: an absent or empty condition is satisfied under either polarity")

# --- one test per leaf --------------------------------------------------------------------------
yes({"stat": "sync", "gte": 60}); no({"stat": "sync", "gte": 61})
yes({"stat": "sync", "lte": 60}); no({"stat": "sync", "lte": 59})
yes({"stat": "sync", "between": [50, 70]}); no({"stat": "sync", "between": [61, 70]})
yes({"stat": "sync", "gte": 50, "lte": 70}); no({"stat": "sync", "gte": 50, "lte": 55})
print("OK: stat leaf - gte, lte, between and gte+lte together")

yes({"tier": ["sync", "seated"]}); no({"tier": ["sync", "fused"]}); no({"tier": ["sync", "loose"]})
print("OK: tier leaf reads the current tier, not 'at or above'")

yes({"tier_reached": ["sync", "seated"]}); yes({"tier_reached": ["sync", "loose"]})
no({"tier_reached": ["sync", "fused"]})
logged = ctx()
logged["state"]["mechanics"] = {"stats": {"tier_log": {"sync": ["fused"]}}}
yes({"tier_reached": ["sync", "fused"]}, c=logged)
print("OK: tier_reached reads tier_log, falling back to the current value as a lower bound")

yes({"revealed": "frag_1"}); no({"revealed": "frag_2"})
yes({"revelation": "frag_1"})
print("OK: revealed leaf (and its legacy `revelation` spelling)")

yes({"flag": "lark_aboard"}, c=ctx(protagonist={**ctx()["state"]["protagonist"],
                                                 "flags": {"active": {}, "archive": {"lark_aboard": 1}}}))
no({"flag": "lark_aboard"})
print("OK: flag leaf reads active UNION archive")

yes({"relationship": "Lark", "tier_gte": "warm"}); no({"relationship": "Lark", "tier_gte": "cold", "tier_lte": "cold"})
no({"relationship": "Lark", "tier_lte": "warm"})  # 30 is above warm's threshold of 25
no({"relationship": "Lark", "tier_lte": "cold"}); yes({"relationship": "Lark", "tier_gte": "cold"})
yes({"relationship": "Lark", "peak_gte": 55}); no({"relationship": "Lark", "peak_gte": 61})
yes({"relationship": "Lark", "between": [0, 40]}); no({"relationship": "Lark", "between": [31, 40]})
no({"relationship": "Descant", "peak_gte": 0})
print("OK: relationship leaf - tier_gte/tier_lte, peak_gte (falls back to score), between; unmet when unscored")

yes({"creation": {"trade": "listener"}}); no({"creation": {"trade": "patcher"}})
yes({"turn_gte": 40}); no({"turn_gte": 41})
yes({"act_gte": 2}); no({"act_gte": 3})
print("OK: creation, turn_gte and act_gte leaves")

yes({"subplot_status": {"sp_a": "completed"}}); no({"subplot_status": {"sp_a": "active"}})
yes({"subplot_status": {"sp_b": "progressed"}}); yes({"subplot_status": {"sp_a": "progressed"}})
yes({"subplot_status": {"sp_a": "completed", "sp_b": "active"}})
no({"subplot_status": {"sp_a": "completed", "sp_b": "completed"}})
print("OK: subplot_status leaf, including CR-10's `progressed`")

leveraged = ctx()
leveraged["state"]["protagonist"]["leverage"] = [{"kind": "secret", "label": "the Board's ledger"}]
yes({"leverage_kind": "secret"}, c=leveraged); no({"leverage_kind": "debt"}, c=leveraged)
yes({"leverage_label_matches": "ledger"}, c=leveraged); no({"leverage_label_matches": "vault"}, c=leveraged)
tagged = ctx()
tagged["state"]["protagonist"]["inventory"] = [{"label": "key", "tags": ["vault_key"]}, "loose string"]
yes({"item_tag": "vault_key"}, c=tagged); no({"item_tag": "crowbar"}, c=tagged)
print("OK: leverage_kind, leverage_label_matches and item_tag leaves")

ENDING = {"id": "e", "waypoints": [{"id": "w1"}, {"id": "w2"}, {"id": "w3"}]}
done = ctx(mechanics={"endings": {"waypoints_done": {"e.w1": 10, "e.w2": 20, "other.w3": 5}}})
yes({"waypoints_done": 2}, c=done, ending=ENDING); no({"waypoints_done": 3}, c=done, ending=ENDING)
no({"waypoints_done": "all"}, c=done, ending=ENDING)
yes({"waypoints_done": ["w1", "w2"]}, c=done, ending=ENDING); no({"waypoints_done": ["w3"]}, c=done, ending=ENDING)
all_done = ctx(mechanics={"endings": {"waypoints_done": {"e.w1": 1, "e.w2": 2, "e.w3": 3}}})
yes({"waypoints_done": "all"}, c=all_done, ending=ENDING)
no({"waypoints_done": 1}, c=ctx(), ending=ENDING)
print("OK: waypoints_done - 'all', a count, a list; nothing is planted before the ledger exists")

# --- combinators + depth --------------------------------------------------------------------------
yes({"all": [{"stat": "sync", "gte": 50}, {"revealed": "frag_1"}]})
no({"all": [{"stat": "sync", "gte": 50}, {"revealed": "frag_2"}]})
yes({"any": [{"revealed": "frag_2"}, {"revealed": "frag_1"}]})
no({"any": [{"revealed": "frag_2"}, {"turn_gte": 99}]})
yes({"not": {"revealed": "frag_2"}}); no({"not": {"revealed": "frag_1"}})
print("OK: all/any/not compose")

three = {"all": [{"any": [{"not": {"revealed": "frag_1"}}, {"revealed": "frag_1"}]}]}
yes(three)
four = {"all": [{"any": [{"all": [{"not": {"revealed": "frag_1"}}]}]}]}
r4 = sat(four, pol=OPEN)
assert r4.unknown and r4.satisfied is True, "a 4-deep group is malformed: unknown, so open reads true"
assert sat(four, pol=CLOSED).satisfied is False
assert conditions.check(three, STORY) == []
assert any("deep" in p for p in conditions.check(four, STORY))
print("OK: groups nest to depth 3; a 4th level is malformed (unknown at runtime, an error in lint)")

assert sat({"any": []}, pol=CLOSED).satisfied is True and sat({"all": []}, pol=CLOSED).satisfied is True
print("OK: an empty group expresses no requirement, under either polarity")

# --- polarity: the phase gate's named pair --------------------------------------------------------
UNKNOWNS = [
    {"flag": "never_declared"}, {"revealed": "frag_gone"}, {"stat": "ghost", "gte": 1},
    {"relationship": "Nobody", "tier_gte": "warm"}, {"tier": ["sync", "mythic"]},
    {"tier": ["ghost", "x"]}, {"creation": {"nope": "x"}}, {"subplot_status": {"sp_zzz": "active"}},
    {"waypoints_done": "all"}, {"bond": {"a": "b"}}, {"nonsense_key": 1}, {"gte": 5},
]
for u in UNKNOWNS:
    o, c = sat(u, pol=OPEN), sat(u, pol=CLOSED)
    assert o.satisfied is True and o.unknown, (u, o)
    assert c.satisfied is False and c.unknown, (u, c)
print(f"OK: {len(UNKNOWNS)} kinds of unknown referent read true under OPEN and false under CLOSED")

assert sat({"not": {"flag": "never_declared"}}, pol=OPEN).satisfied is True, \
    "negating an unknown must not flip it - `not <typo>` would lock the door"
assert sat({"not": {"flag": "never_declared"}}, pol=CLOSED).satisfied is False
assert sat({"all": [{"revealed": "frag_1"}, {"flag": "never_declared"}]}, pol=CLOSED).satisfied is False
print("OK: `not` does not flip an unknown; an unknown inside an all propagates polarity")

# Same flag, same story: a gate (OPEN) passes it, a ready_when (CLOSED) does not.
gate_mod = se.mechanics.gate
assert gate_mod.satisfied({"flag": "never_declared"}, ctx()) is True
assert conditions.satisfied({"flag": "never_declared"}, ctx(), CLOSED) is False
print("OK: an unknown flag is true in a gate and false in ready_when")

# --- SYNC 85 and lark_departed prunes viable_while -------------------------------------------------
VIABLE = {"not": {"all": [{"stat": "sync", "gte": 85}, {"flag": "lark_departed"}]}}
c85 = ctx(protagonist={**ctx()["state"]["protagonist"], "stats": {"sync": 85, "reach": 10},
                       "flags": {"active": {"lark_departed": 1}, "archive": {}}})
assert conditions.satisfied(VIABLE, c85, OPEN) is False, "SYNC 85 + lark_departed must prune"
assert conditions.satisfied(VIABLE, ctx(), OPEN) is True
c84 = ctx(protagonist={**c85["state"]["protagonist"], "stats": {"sync": 84, "reach": 10}})
assert conditions.satisfied(VIABLE, c84, OPEN) is True
print("OK: 'SYNC 85 and lark_departed set' prunes viable_while, and 84 does not")

# --- legacy forms ----------------------------------------------------------------------------------
assert conditions.normalize({"stat": {"axis": "sync", "at_least": 40, "at_most": 90}}) == \
    {"stat": "sync", "gte": 40, "lte": 90}
assert conditions.normalize({"revelation": "x"}) == {"revealed": "x"}
assert conditions.normalize({"relationship": {"character": "Lark", "peak_gte": 5}}) == \
    {"relationship": "Lark", "peak_gte": 5}
yes({"stat": {"axis": "sync", "at_least": 60}}); no({"stat": {"axis": "sync", "at_most": 59}})
print("OK: legacy stat/revelation/relationship forms are rewritten to the canonical grammar")

# --- proximity --------------------------------------------------------------------------------------
assert sat({"stat": "sync", "gte": 60}).proximity == 1.0
r = sat({"stat": "sync", "gte": 80})  # 20 short of a 0..100 axis
assert abs(r.proximity - 0.8) < 1e-9, r
half = sat({"all": [{"stat": "sync", "gte": 60}, {"revealed": "frag_2"}]})
assert abs(half.proximity - 0.5) < 1e-9, half
assert sat({"any": [{"revealed": "frag_2"}, {"stat": "sync", "gte": 60}]}).proximity == 1.0
assert sat({"revealed": "frag_2"}).proximity == 0.0
assert 0.0 < sat({"turn_gte": 80}).proximity < 1.0
print("OK: proximity - distance-scored stats, mean for all, best for any")

# --- purity ---------------------------------------------------------------------------------------------
import copy  # noqa: E402
before = copy.deepcopy(ctx())
c = ctx()
conditions.evaluate({"all": [{"stat": "sync", "gte": 99}, {"flag": "lark_aboard"}]}, c, CLOSED)
assert c == before, "evaluate() must never mutate ctx"
print("OK: evaluate() leaves ctx untouched")

# --- describe(): plain English, total, never raises ------------------------------------------------
D = conditions.describe
assert D({"stat": "reach", "gte": 50}) == "REACH >= 50"
assert D({"stat": {"axis": "reach", "at_least": 20}}) == "REACH >= 20"
assert D({"stat": "sync", "between": [10, 20]}) == "SYNC 10-20"
assert D({"flag": "lark_departed"}) == "flag: lark_departed"
assert D({"relationship": "Lark", "tier_gte": "warm"}) == "Lark at least warm"
assert D({"relationship": "Lark", "peak_gte": 55}) == "Lark peak >= 55"
assert D({"tier_reached": ["trace", "loud"]}) == "TRACE reached loud"
assert D({"subplot_status": {"sp_a": "completed"}}) == "thread sp_a completed"
assert D({"waypoints_done": "all"}) == "all waypoints done"
assert D({"all": [{"stat": "sync", "gte": 85}, {"flag": "lark_departed"}]}) == "SYNC >= 85 and flag: lark_departed"
assert D({"any": [{"flag": "a"}, {"all": [{"flag": "b"}, {"turn_gte": 9}]}]}) == "flag: a or (flag: b and turn >= 9)"
assert D({"not": {"flag": "a"}}) == "not flag: a"
assert D(None) == "always" and D({}) == "always"
for weird in ("text", 5, [1], {"all": "x"}, {"stat": 5}, {"nonsense": 1}, {"relationship": None}):
    assert isinstance(D(weird), str)
print("OK: describe() renders every leaf and group in plain English and never raises")

# --- check(): the static, template-side half ---------------------------------------------------------------
assert conditions.check({"all": [{"stat": "sync", "gte": 1}, {"revealed": "frag_1"},
                                 {"flag": "lark_departed"}, {"relationship": "Lark", "tier_gte": "warm"},
                                 {"tier_reached": ["sync", "fused"]}, {"creation": {"trade": "patcher"}},
                                 {"subplot_status": {"sp_a": "completed"}}]}, STORY) == []
for cond, needle in [
    ({"flag": "typo"}, "declare"),
    ({"revealed": "frag_9"}, "revelation"),
    ({"stat": "ghost", "gte": 1}, "stat"),
    ({"stat": "sync"}, "needs gte"),
    ({"relationship": "Nobody", "tier_gte": "warm"}, "character"),
    ({"relationship": "Lark"}, "comparator"),
    ({"tier": ["sync", "mythic"]}, "tier"),
    ({"creation": {"trade": "wizard"}}, "option"),
    ({"subplot_status": {"sp_q": "active"}}, "subplot"),
    ({"bond": {}}, "bond"),
    ({"gte": 4}, "gte"),
]:
    problems = conditions.check(cond, STORY)
    assert any(needle in p for p in problems), (cond, problems)
print("OK: check() reports every unknown referent and malformed leaf the evaluator would call unknown")

# --- iter_conditions: every field, with its polarity ----------------------------------------------------------
tpl = {
    "plot": {"main_thread": {"acts": [{"requires": {"flag": "a"}}]},
             "subplots": {"s": {"activate_when": {"flag": "b"}, "fail_when": {"flag": "c"}}}},
    "mechanics": {
        "gate": {"gates": [{"requires": {"flag": "d"}}]},
        "endings": {"entries": [{"id": "e", "viable_while": {"flag": "f"}, "ready_when": {"flag": "g"},
                                 "fail_when": {"flag": "h"},
                                 "waypoints": [{"id": "w", "done_when": {"flag": "i"}}]}]},
    },
}
seen = {c["flag"]: pol for _, c, pol, _ in conditions.iter_conditions(tpl)}
assert seen == {"a": OPEN, "b": OPEN, "c": CLOSED, "d": OPEN, "f": OPEN, "g": CLOSED, "h": CLOSED, "i": CLOSED}, seen
scoped = [e for _, c, _, e in conditions.iter_conditions(tpl) if c["flag"] == "i"][0]
assert scoped["id"] == "e"
print("OK: iter_conditions walks every condition field and states its polarity and ending scope")

print("\nALL CHECKS PASSED: test_conditions")

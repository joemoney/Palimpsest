"""CR-06 `mechanics.lore` / `keyed_lore` (backend/mechanics/lore.py).

Keys on the page, `also_when`, `unlock`, priority and the `max_active` cut, stickiness, the
prompt budget, and the P-2 absence. Run directly: python3 test/test_lore.py"""
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import load_story_engine  # noqa: E402

se = load_story_engine()
import mechanics  # noqa: E402
from mechanics import lore  # noqa: E402

CFG = {"engine": "keyed_lore", "max_active": 2, "entries": [
    {"id": "lark", "priority": 90, "keys": ["Lark", "the diver"], "sticky_turns": 2, "content": "Lark is fixed."},
    {"id": "board", "priority": 50, "keys": ["Board"], "content": "The Board is competent."},
    {"id": "dock", "priority": 70, "keys": ["dock"], "content": "Docks stink."},
    {"id": "late", "priority": 99, "keys": ["secret"], "unlock": {"flag": "met"}, "content": "Late knowledge."},
    {"id": "cond", "priority": 10, "also_when": {"flag": "alarm"}, "content": "Alarm lore."},
]}


def make(action="", last="", turn=5, flags=(), cfg=CFG, hit=None):
    story = {"world": {}, "mechanics": {"lore": copy.deepcopy(cfg)}}
    state = {"pacing": {"turn_count": turn}, "history": {"recent_turns": [f"Player: x\nNarrator: {last}"]},
             "protagonist": {"flags": {"active": {f: {} for f in flags}, "archive": {}}},
             "mechanics": {"lore": {"hit": dict(hit or {})}}}
    return {"story": se.state_store.freeze(story), "state": state, "player_action": action}


def injected(ctx):
    return [e["id"] for e, _ in lore.select(ctx["story"]["mechanics"]["lore"], ctx)]


assert injected(make()) == []
assert injected(make(action="I follow LARK")) == ["lark"]
assert injected(make(last="the diver waits")) == ["lark"]
assert injected(make(action="Larkspur")) == [], "whole words only"
print("OK: keys match the action or last narration, case-insensitively, as whole words")

assert injected(make(action="Lark at the dock, the Board watches")) == ["lark", "dock"], "priority cut at max_active"
print("OK: highest priority wins, up to max_active")

assert injected(make(action="secret")) == [] and injected(make(action="secret", flags=["met"])) == ["late"]
assert injected(make(flags=["alarm"])) == ["cond"]
print("OK: unlock keeps an entry dormant; also_when triggers without a key")

ctx = make(action="Lark")
lore.touch(CFG, ctx)
assert ctx["state"]["mechanics"]["lore"]["hit"] == {"lark": 5}
later = make(turn=7, hit={"lark": 5})
assert injected(later) == ["lark"], "sticky for sticky_turns"
assert injected(make(turn=8, hit={"lark": 5})) == []
print("OK: sticky_turns keeps an entry after its trigger, then lets it go")

cut = make(action="Lark dock Board")
lore.touch(CFG, cut)
assert "board" not in cut["state"]["mechanics"]["lore"]["hit"], "a cut entry does not go sticky"
print("OK: a cut entry records no hit")

ctx = make(action="Lark")
text = mechanics.prompt_sections(ctx)["lore.entries"]
assert text.strip().startswith("LORE:") and "Lark is fixed." in text
assert "lore.entries" not in mechanics.prompt_sections(make())
print("OK: LORE section only when something triggered")

big = copy.deepcopy(CFG)
big["entries"][0]["content"] = "x" * 3000
try:
    lore.ENGINE.check_config(big)
    raise SystemExit("expected a budget error")
except ValueError as e:
    assert "budget" in str(e)
print("OK: entries that cannot fit the budget are refused at load")

plain = se.state_store.freeze({"world": {}, "mechanics": {}})
assert mechanics.bound_for(plain, "lore") is None
print("OK: no lore block, no engine")
print("\nALL CHECKS PASSED: test_lore")

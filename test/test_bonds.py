"""CR-11 `mechanics.bonds` / `scored_bonds` (backend/mechanics/bonds.py).

Directional scores, a closed event vocabulary, tier labels only in the prompt, and the three
eviction promises: authored pairs never go, a generated character's bonds leave with them, and a
character pinned by an active side thread is exempt. Run directly: python3 test/test_bonds.py"""
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import load_story_engine  # noqa: E402

se = load_story_engine()
import conditions  # noqa: E402
import mechanics  # noqa: E402
from mechanics import bonds  # noqa: E402

SAL, MIRA, GEN = "Salome", "Mira", "Dock Boss"
CFG = {"engine": "scored_bonds", "registers": {"covered": 8, "worked": 3, "betrayed": -15},
       "tiers": [{"at": 25, "label": "warm"}, {"at": -25, "label": "wary"}],
       "seed": [{"from": MIRA, "to": SAL, "score": -20}], "max_generated_bonds": 1,
       "cap_per_window": {"delta": 10, "turns": 3}}


def make(cfg=CFG, present=()):
    story = {"world": {"characters": {SAL: {}, MIRA: {}}}, "mechanics": {"bonds": copy.deepcopy(cfg)}}
    state = {"pacing": {"turn_count": 5}, "characters": {}, "plot": {"current_scene": {"present": list(present)}},
             "mechanics": {"bonds": bonds.ENGINE.init_state(cfg, None)}}
    return {"story": se.state_store.freeze(story), "state": state}


def run(ctx, entries):
    diff = {"bond_events": entries}
    events = mechanics.events_from_diff(ctx, diff)
    mechanics.run_turn_pipeline(ctx, events)
    return events


def score(ctx, a, b):
    return ((ctx["state"]["mechanics"].get("bonds") or {}).get(a) or {}).get(b, {}).get("score", 0)


# seed + direction ---------------------------------------------------------------------------
ctx = make()
assert score(ctx, MIRA, SAL) == -20 and score(ctx, SAL, MIRA) == 0
run(ctx, [{"from": SAL, "to": MIRA, "event": "covered"}])
assert score(ctx, SAL, MIRA) == 8 and score(ctx, MIRA, SAL) == -20, "A->B leaves B->A alone"
run(ctx, [{"from": SAL, "to": MIRA, "event": "worked", "mutual": True}])
assert score(ctx, SAL, MIRA) == 10 and score(ctx, MIRA, SAL) == -17, "mutual moves both (Salome->Mira capped at 10)"
print("OK: seeded bonds open at their value, events are directional, mutual applies both ways")

# vocabulary --------------------------------------------------------------------------------
ctx = make()
ev = run(ctx, [{"from": SAL, "to": MIRA, "event": "invented"}, {"from": SAL, "to": SAL, "event": "covered"},
               "junk", {"from": SAL, "event": "covered"}])
assert ev == [] and score(ctx, SAL, MIRA) == 0
print("OK: an unknown event, a self-bond and malformed entries are dropped")

# the window cap, and the condition leaf reading the same state ------------------------------
ctx = make()
run(ctx, [{"from": SAL, "to": MIRA, "event": "covered"}, {"from": SAL, "to": MIRA, "event": "covered"}])
assert score(ctx, SAL, MIRA) == 10, score(ctx, SAL, MIRA)
assert conditions.satisfied({"bond": [SAL, MIRA], "gte": 10}, ctx, conditions.CLOSED)
print("OK: cap_per_window bounds one bond's movement, and the CR-02 bond leaf reads it")

# eviction: authored pairs stay, generated go closest-to-neutral, pins are exempt ------------
ctx = make()
run(ctx, [{"from": SAL, "to": MIRA, "event": "betrayed"}, {"from": SAL, "to": GEN, "event": "worked"},
          {"from": MIRA, "to": GEN, "event": "betrayed"}])
assert score(ctx, SAL, MIRA) == -10 and score(ctx, MIRA, SAL) == -20, "authored pair never evicted"
assert score(ctx, SAL, GEN) == 0 and score(ctx, MIRA, GEN) == -10, "closest to neutral went first"
ctx = make()
ctx["state"]["mechanics"]["side_threads"] = {"active": [{"cast": {"a": SAL, "b": GEN}}]}
run(ctx, [{"from": SAL, "to": GEN, "event": "worked"}, {"from": MIRA, "to": GEN, "event": "betrayed"}])
assert score(ctx, SAL, GEN) == 3, "a pinned character's bond survives eviction"
print("OK: authored pairs never evicted; generated bonds go closest to neutral; pinned characters exempt")

ctx = make()
run(ctx, [{"from": MIRA, "to": GEN, "event": "betrayed"}])
bonds.drop_character(ctx, GEN)
assert score(ctx, MIRA, GEN) == 0 and GEN not in ctx["state"]["mechanics"]["bonds"].get(MIRA, {})
print("OK: dropping a character removes their bonds in both directions")

# prompt: tier labels only, only for pairs in the scene -------------------------------------
ctx = make(present=[SAL, MIRA])
ctx["state"]["mechanics"]["bonds"][SAL] = {MIRA: {"score": 30, "peak": 30, "opened_turn": 1}}
ctx["state"]["mechanics"]["bonds"][MIRA][SAL]["score"] = -30
text = mechanics.prompt_sections(ctx)["bonds.between_them"]
assert f"{SAL} → {MIRA}: warm" in text and f"{MIRA} → {SAL}: wary" in text
assert not any(ch.isdigit() for ch in text), "never a number"
ctx["state"]["plot"]["current_scene"]["present"] = [SAL]
assert "bonds.between_them" not in mechanics.prompt_sections(ctx), "both must be in the scene"
print("OK: BETWEEN THEM shows tier labels for pairs in the scene, never numbers")

# one observation field, absent module leaves no trace ---------------------------------------
assert [f.name for f in mechanics.observation_fields(make())] == ["bond_events"]
bare = make()
bare["story"] = se.state_store.freeze({"world": {"characters": {}}, "mechanics": {}})
assert mechanics.observation_fields(bare) == [] and mechanics.prompt_sections(bare) == {}
for bad in ({"engine": "scored_bonds"}, {**CFG, "tiers": [{"at": 1, "label": "x", "narration": "n"}]}):
    try:
        bonds.ENGINE.check_config(bad)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
print("OK: exactly one observation field; an absent block adds nothing; registers required, tier narration refused")

print("\nALL CHECKS PASSED: test_bonds")

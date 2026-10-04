"""CR-11 `mechanics.side_threads` / `episodic_threads` (backend/mechanics/episodes.py).

The engine decides when and who; the model only writes the episode. Covers starting (gates,
casting, protected characters, ranking, the default recipe, callbacks), the generated episode's
conditions being confined to `may_move`, the offer's priority order, the observation field, the
end rules (resolved / failed / expired / finale) and vignettes. Run directly:
python3 test/test_episodes.py"""
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import load_story_engine  # noqa: E402

se = load_story_engine()
import conditions  # noqa: E402
import mechanics  # noqa: E402
from mechanics import episodes  # noqa: E402

A, B, C, P = "Ada", "Bram", "Cora", "Pell"
BONDS = {"engine": "scored_bonds", "registers": {"covered": 8}, "tiers": [{"at": 20, "label": "close"}]}
CFG = {
    "engine": "episodic_threads", "start_after_beats": ["respite"], "max_active": 1, "cooldown_turns": 4,
    "max_turns": 6, "protected": [P],
    "recipes": [{
        "id": "rift", "premise": "two people fall out over a small thing",
        "cast": {"a": {"from": "authored"}, "b": {"from": "authored"}},
        "eligible_when": {"bond": ["a", "b"], "gte": 5},
        "may_move": ["bond:a,b", "bond:b,a"], "may_create_npc": True}],
    "vignettes": {"every": 3, "seeds": ["rain on the depot roof"], "subjects": ["location", "character"]},
}


def make(cfg=CFG, turn=10, beat="respite", seed_bonds=None):
    chars = {n: {"description": n} for n in (A, B, C, P)}
    story = {"world": {"characters": chars, "rules": ["No magic."],
                       "locations": {"dock": {"name": "The Dock", "description": "wet"}}},
             "mechanics": {"bonds": copy.deepcopy(BONDS), "side_threads": copy.deepcopy(cfg)}}
    state = {"pacing": {"turn_count": turn, "last_beat": {"type": beat, "intensity": 1}, "armed": {}},
             "characters": {}, "plot": {"endgame": {"requested": False}, "current_scene": {"present": []}},
             "history": {"recent_turns": ["Ada and Bram argued on the dock."]},
             "protagonist": {"inventory": []},
             "mechanics": {"bonds": seed_bonds if seed_bonds is not None else {A: {B: {"score": 12}}},
                           "side_threads": episodes.ENGINE.init_state(cfg, None)}}
    return {"story": se.state_store.freeze(story), "state": state}


def bound(ctx):
    return mechanics.bound_for(ctx["story"], "side_threads")


GEN = {"title": "A Quarrel", "premise": "a small thing", "beats": ["tension", "words", "a rift", "x", "overflow"],
       "resolves_when": {"bond": ["a", "b"], "lte": -5}, "fails_when": {"flag": "stray"},
       "detect": "they stop speaking", "new_character": None}


def start(ctx, gen=GEN):
    b = bound(ctx)
    binding = episodes.choose(b.cfg, ctx)
    thread = episodes.build_thread(b.cfg, ctx, binding, gen)
    mechanics.apply_effects(ctx, [mechanics.Effect("side.start", reason="t", thread=thread)])
    return thread


# starting -----------------------------------------------------------------------------------
ctx = make()
b = bound(ctx)
assert episodes.start_due(b.cfg, ctx)
for tweak, why in ((lambda c: c["state"]["pacing"]["last_beat"].update(type="crisis"), "wrong beat"),
                   (lambda c: c["state"]["plot"]["endgame"].update(requested=True), "ending committed")):
    c = make(); tweak(c); assert not episodes.start_due(bound(c).cfg, c), why
c = make(); c["state"]["mechanics"]["side_threads"]["last_started_turn"] = 8
assert not episodes.start_due(bound(c).cfg, c), "cooldown"
print("OK: a thread starts only on a named beat, with room, past the cooldown, before any ending")

choice = episodes.choose(b.cfg, ctx)
assert choice["recipe"] == "rift" and choice["cast"] == {"a": A, "b": B}, choice
assert P not in choice["cast"].values()
ctx2 = make(seed_bonds={A: {B: {"score": 1}}})
assert episodes.choose(bound(ctx2).cfg, ctx2)["recipe"] == "default", "ineligible authored recipe falls to the default"
ctx3 = make(seed_bonds={})
assert episodes.choose(bound(ctx3).cfg, ctx3) is None, "no bond to choose a pair by, so no thread"
print("OK: casting is eligible_when-gated, never casts a protected character, and defaults on the strongest bond")

# build_thread confines conditions -------------------------------------------------------------
t = start(ctx)
assert t["id"] == "st_001" and len(t["beats"]) == episodes.MAX_BEATS
assert t["resolves_when"] == {"bond": [A, B], "lte": -5}, "slots are bound to names"
assert t["fails_when"] is None, "a leaf outside may_move is nulled"
assert ctx["state"]["mechanics"]["side_threads"]["last_started_turn"] == 10
assert episodes.confine({"bond": ["a", "c"], "lte": 1}, ["bond:a,b"], {"a": A, "b": B}) is None
assert episodes.confine({"any": [{"bond": ["a", "b"], "gte": 1}, {"stat": "x", "gte": 1}]}, ["bond:a,b"], {"a": A, "b": B}) is None
print("OK: generated conditions that stray outside may_move are nulled before they are bound")

# offer, in priority order -------------------------------------------------------------------------
ctx["state"]["pacing"]["turn_count"] = 11
assert episodes.choose_offer(b.cfg, ctx)["kind"] == "thread"
ctx["state"]["pacing"]["armed"] = {"rule": 1}
assert episodes.choose_offer(b.cfg, ctx) is None, "an armed pacing rule outranks a side thread"
ctx["state"]["pacing"]["turn_count"] = 16
assert episodes.choose_offer(b.cfg, ctx)["kind"] == "closing", "the closing line ignores armed rules"
ctx["state"]["pacing"]["armed"] = {}
ctx["state"]["plot"]["endgame"]["requested"] = True
assert episodes.choose_offer(b.cfg, ctx)["kind"] == "closing"
ctx["state"]["pacing"]["turn_count"] = 12
assert episodes.choose_offer(b.cfg, ctx)["kind"] == "wrapup", "ending committed: wrap up, nothing new"
print("OK: offer priority is closing, then wrap-up, then a normal offer, and rules outrank it")

# prompt + observation ----------------------------------------------------------------------------
ctx["state"]["plot"]["endgame"]["requested"] = False
ctx["state"]["pacing"]["turn_count"] = 11
offer = episodes.record_offer(b.cfg, ctx)
text = mechanics.prompt_sections(ctx)["side_threads.offer"]
assert "A Quarrel" in text and "tension" in text and "st_001" not in text and "rift" not in text
ctx["state"]["pacing"]["turn_count"] = 12  # the observation pass runs after the counter moved
fields = mechanics.observation_fields(ctx)
assert "side_thread_progress" in [f.name for f in fields]
ev = mechanics.events_from_diff(ctx, {"side_thread_progress": [{"id": "st_001", "advanced": True, "detect_hit": False},
                                                              {"id": "st_999", "advanced": True}]})
assert [e["id"] for e in ev if e["type"] == "side_progress"] == ["st_001"], "only the offered thread counts"
mechanics.run_turn_pipeline(ctx, ev)
th = ctx["state"]["mechanics"]["side_threads"]["active"][0]
assert th["beat_index"] == 1 and th["last_offered_turn"] == 12
print("OK: the narrator sees title and beat only; the observation field answers for the offered thread")

# end rules ----------------------------------------------------------------------------------------
def settle(c):
    b_ = bound(c)
    effs = b_.engine.settle(b_.cfg, c)
    mechanics.apply_effects(c, effs)
    return [e.payload["outcome"] for e in effs if e.kind == "side.conclude"]


assert settle(ctx) == []
ctx["state"]["mechanics"]["bonds"][A][B]["score"] = -9
assert settle(ctx) == ["resolved"], "resolves_when is read CLOSED against live state"
assert ctx["state"]["mechanics"]["side_threads"]["concluded"][0]["outcome"] == "resolved"
assert ctx["state"]["mechanics"]["side_threads"]["active"] == []
c = make(); start(c); c["state"]["pacing"]["turn_count"] = 17
assert settle(c) == ["expired"], "max_turns is the backstop"
c = make(); start(c); mechanics.apply_effects(c, [mechanics.Effect("side.progress", id="st_001", beat_index=0, detected=True, turn=11)])
assert settle(c) == ["resolved"], "a detect_hit concludes it"
c = make(); start(c); c["state"]["plot"]["endgame"]["requested"] = True
assert settle(c) == [], "first settle after an ending commits only marks the wrap"
c["state"]["pacing"]["turn_count"] = 11
assert settle(c) == ["finale"]
print("OK: threads end resolved, expired or finale, and a typo'd condition never concludes one")

# callbacks --------------------------------------------------------------------------------------
cfg2 = copy.deepcopy(CFG)
cfg2["recipes"].append({"id": "mend", "premise": "p", "cast": {"x": {"from": "followed", "slot": "a"}, "y": {"from": "any"}},
                        "follows": {"recipe": "rift", "outcome": ["resolved"], "min_turns_since": 2}, "may_move": []})
c = make(cfg2); start(c)
settle_ids = []
c["state"]["mechanics"]["bonds"][A][B]["score"] = -9
settle(c)
c["state"]["pacing"]["turn_count"] = 11
assert all(x["recipe"] != "mend" for x in episodes.candidates(bound(c).cfg, c)), "too soon after it ended"
c["state"]["pacing"]["turn_count"] = 20
c["state"]["mechanics"]["bonds"][A][B]["score"] = 12
mend = [x for x in episodes.candidates(bound(c).cfg, c) if x["recipe"] == "mend"]
assert mend and mend[0]["cast"]["x"] == A and mend[0]["followed"] == "st_001", mend
print("OK: a follow-up recipe binds only to a matching, old-enough, unfollowed conclusion")

# vignettes ----------------------------------------------------------------------------------------
c = make(seed_bonds={})
off = episodes.choose_offer(bound(c).cfg, c)
assert off == {"kind": "texture", "subject": "rain on the depot roof"}, off
mechanics.apply_effects(c, [mechanics.Effect("side.vignette", subject=off["subject"], turn=10)])
c["state"]["pacing"]["turn_count"] = 11
assert episodes.choose_offer(bound(c).cfg, c) is None, "spaced by `every`"
c["state"]["pacing"]["turn_count"] = 13
nxt = episodes.choose_offer(bound(c).cfg, c)
assert nxt["kind"] == "texture" and nxt["subject"] != off["subject"], nxt
nobody = copy.deepcopy(CFG); del nobody["vignettes"]
c = make(nobody, seed_bonds={})
assert "vignette_cursor" not in c["state"]["mechanics"]["side_threads"], "P-2: no vignette state without vignettes"
print("OK: vignettes alternate seeds and the least-recent subject, spaced by `every`, absent unless authored")

# protected characters and pinning -------------------------------------------------------------
c = make(); start(c)
from mechanics import bonds as bonds_mod  # noqa: E402
assert bonds_mod.pinned(c) == {A, B}

# generation through story_engine ----------------------------------------------------------------
c = make()
seen = {}


def fake(prompt, **kw):
    seen["prompt"] = prompt
    return copy.deepcopy(GEN)


se.call_llm_json = fake
se.advance_side_threads(c)
assert c["state"]["mechanics"]["side_threads"]["active"][0]["title"] == "A Quarrel"
assert "No magic." in seen["prompt"] and "close" not in seen["prompt"].split("HOW THEY STAND")[0]
assert "rift" not in seen["prompt"] and P not in seen["prompt"].split("EXISTING CHARACTERS")[0]
se.call_llm_json = lambda *a, **k: (_ for _ in ()).throw(ValueError("bad"))
c = make(); se.advance_side_threads(c)
assert c["state"]["mechanics"]["side_threads"]["active"] == [], "a failed generation costs nothing"
print("OK: generation is one call, the prompt carries no recipe id, and a failure starts nothing")

# CR-12 player threads ---------------------------------------------------------------------------
PCFG = copy.deepcopy(CFG)
PCFG["player_threads"] = {"max_active": 1, "confirm": {"reports": 2, "within_turns": 5},
                          "abandon_after_offers": 2, "may_move": ["relationship:cast"]}


def report(c, summary, cont, who=(A,)):
    ev = mechanics.events_from_diff(c, {"side_thread_progress": {"offered": [], "pursuit": {
        "summary": summary, "continues": cont, "with": list(who)}}})
    mechanics.run_turn_pipeline(c, ev)


c = make(PCFG, seed_bonds={})
assert c["state"]["mechanics"]["side_threads"]["pursuit"] is None
assert "pursuit" in [f.schema for f in mechanics.observation_fields(c) if f.name == "side_thread_progress"][0], "asked every turn once authored"
report(c, "fix up the old skiff", False)
assert not c["state"]["mechanics"]["side_threads"]["pursuit"]["ready"], "one report never opens a thread"
report(c, "something else", False)
assert c["state"]["mechanics"]["side_threads"]["pursuit"]["summary"] == "something else", "a new summary restarts"
c["state"]["pacing"]["turn_count"] = 11
report(c, "ignored", True)
cand = c["state"]["mechanics"]["side_threads"]["pursuit"]
assert cand["ready"] and cand["summary"] == "something else" and cand["reports"] == [10, 11]
print("OK: a pursuit opens only once reported `reports` times in the window; a new summary restarts the count")

c["state"]["pacing"]["turn_count"] = 10
c["state"]["mechanics"]["side_threads"]["pursuit"] = None
report(c, "court Pell", False, who=(P,))
report(c, "court Pell", True, who=(P,))
assert c["state"]["mechanics"]["side_threads"]["pursuit"] is None, "a protected character in the cast drops it"
print("OK: a pursuit casting a protected character never opens a thread")

se.call_llm_json = lambda *a, **k: copy.deepcopy(GEN)
c = make(PCFG, seed_bonds={}); c["state"]["pacing"]["last_beat"]["type"] = "crisis"
report(c, "run errands", False); c["state"]["pacing"]["turn_count"] = 11; report(c, "x", True)
se.advance_side_threads(c)
th = c["state"]["mechanics"]["side_threads"]["active"]
assert len(th) == 1 and th[0]["origin"] == "player" and th[0]["recipe"] == "player_pursuit"
assert c["state"]["mechanics"]["side_threads"]["pursuit"] is None
print("OK: a confirmed pursuit becomes an origin=player thread through the same generation call")

# abandonment: offered abandon_after_offers times without advancing
for turn in (12, 13):
    c["state"]["pacing"]["turn_count"] = turn
    episodes.record_offer(bound(c).cfg, c)
    c["state"]["pacing"]["turn_count"] = turn + 1
    mechanics.run_turn_pipeline(c, mechanics.events_from_diff(c, {"side_thread_progress": {"offered": [
        {"id": "st_001", "advanced": False, "detect_hit": False}], "pursuit": None}}))
assert settle(c) == ["abandoned"], c["state"]["mechanics"]["side_threads"]["active"]
print("OK: a player thread offered without progress `abandon_after_offers` times concludes abandoned")

c = make(PCFG, seed_bonds={})
assert se.open_player_thread(c, "help the dockhands") is None
assert c["state"]["mechanics"]["side_threads"]["active"][0]["origin"] == "player"
assert "maximum" in se.open_player_thread(c, "again")
print("OK: add-goal opens a player thread directly, skipping detection, within max_active")

c = make(CFG, seed_bonds={})
assert "side_thread_progress" not in [f.name for f in mechanics.observation_fields(c)], "nothing offered, no player threads: no field"
assert "pursuit" not in c["state"]["mechanics"]["side_threads"]
print("OK: without player_threads the pursuit key does not exist (P-2)")

print("\nALL CHECKS PASSED: test_episodes")

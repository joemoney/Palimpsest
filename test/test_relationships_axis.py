"""The `relationships` / `scored_axis` engine - engine v2 phase 4, port 1.

Two halves, deliberately:

  1. **Pure unit tests, no stubs at all.** `resolve()` is pure (E-4), so the price list, the
     clamp, the tier scan, the window cap and the eviction rule are testable as ordinary
     Python - no `_llm_stubs`, no monkeypatched `call_llm`, no API key. ENGINE_V2_SPEC §9
     says to exploit this hard, and it is the first part of this codebase with the property.
  2. **Integration through the real observation pass**, for the things a unit test cannot
     see: that the field reaches the prompt, that an absent engine leaks nothing in either
     prompt, and that a stray `social` key from the model is ignored by a story that never
     declared the mechanic.

What replaced what: v2 asked for `relationship_changes: {"<name>": <delta>}` - a number the
model invented - and interpolated `axis.description` into the instruction that asked for it.
v3 asks what socially happened (`{"target", "register", "reciprocated"}`) against the story's
own authored vocabulary, and the template's price list turns that into a number. So
`axis.description` is no longer in either prompt, by design (§3.3): a field whose only
consumer was an f-string inside a schema instruction was configuration for a decision the
engine should have been making.

Run directly: python3 test/test_relationships_axis.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import CannedResponses, RecordingLLM, load_story_engine  # noqa: E402

se = load_story_engine()
mechanics = se.mechanics
ENGINE = mechanics.social.ENGINE

EMPTY_DIFF = {
    "subplot_beats": {}, "flags_set": {}, "revelations": {"revealed": [], "eligible": []},
    "inventory": {"gained": [], "used": []}, "social": [], "new_characters": [],
}


def with_story(ctx, mutate):
    story_dict = se.state_store.thaw(ctx["story"])
    mutate(story_dict)
    ctx["story"] = se.state_store.freeze(story_dict)


def fake_ctx(cfg, scores=None, authored=(), turn=0, engine_state=None):
    """A ctx just real enough for a pure resolve() - no save, no template, no LLM."""
    return {
        "story": {"world": {"characters": {name: {} for name in authored}},
                  "mechanics": {"relationships": cfg}},
        "state": {"characters": dict(scores or {}),
                  "pacing": {"turn_count": turn},
                  "mechanics": {"relationships": engine_state} if engine_state else {}},
    }


def beat(target, register, reciprocated=True):
    return {"type": "social", "target": target, "register": register,
            "reciprocated": reciprocated}


# =====================================================================================
# 1. Pure engine rules - no stubs
# =====================================================================================

CFG = {
    "engine": "scored_axis",
    "scale": {"min": -50, "max": 50},
    "axis": {"negative": "wary", "positive": "confiding", "description": "trust and warmth"},
    "registers": {"confided_secret": 8, "kept_faith": 5, "public_slight": -12},
    "tiers": [{"at": 25, "label": "trusted", "narration": "will lie to the constable for you."},
              {"at": -25, "label": "closed off"}],
    "limit": 4,
}

# --- the model never picks a number; the register does -------------------------------
ctx = fake_ctx(CFG, {"Abbott": {"relationship": 0}})
effects = ENGINE.resolve(CFG, ctx, [beat("Abbott", "confided_secret")], [])
assert [e.kind for e in effects] == ["relationships.set"]
assert effects[0].payload["value"] == 8
assert effects[0].payload["delta"] == 8
assert effects[0].reason == "social:confided_secret", effects[0].reason
print("OK: a social beat is priced from the story's register table, not from the model")

# --- several beats in one turn accumulate against one another, not from the same start ---
ctx = fake_ctx(CFG, {"Abbott": {"relationship": 0}})
effects = ENGINE.resolve(CFG, ctx, [beat("Abbott", "kept_faith"), beat("Abbott", "kept_faith")], [])
assert [e.payload["value"] for e in effects] == [5, 10], [e.payload for e in effects]
print("OK: two beats with one target in a turn accumulate rather than both pricing from 0")

# --- clamped to the story's own scale, not a hardcoded +/-100 ------------------------
ctx = fake_ctx(CFG, {"Abbott": {"relationship": 46}})
assert ENGINE.resolve(CFG, ctx, [beat("Abbott", "confided_secret")], [])[0].payload["value"] == 50
ctx = fake_ctx(CFG, {"Abbott": {"relationship": -45}})
assert ENGINE.resolve(CFG, ctx, [beat("Abbott", "public_slight")], [])[0].payload["value"] == -50
print("OK: scores clamp to the authored scale (-50..50 here), not to a hardcoded +/-100")

# --- an unknown register never reaches resolve, because `events` drops it ------------
ctx = fake_ctx(CFG, {"Abbott": {"relationship": 0}})
assert ENGINE.events(CFG, ctx, {"social": [{"target": "Abbott", "register": "invented_by_the_model"}]}) == []
assert ENGINE.events(CFG, ctx, {"social": [{"register": "kept_faith"}]}) == [], "no target, no event"
assert ENGINE.events(CFG, ctx, {"social": ["not a dict"]}) == []
kept = ENGINE.events(CFG, ctx, {"social": [{"target": "Abbott", "register": "kept_faith"}]})
assert kept == [{"type": "social", "target": "Abbott", "register": "kept_faith", "reciprocated": True}]
print("OK: a register outside the authored vocabulary is dropped before it can be priced")

# --- unreciprocated_factor discounts a positive gesture, never a negative one --------
discounting = {**CFG, "unreciprocated_factor": 0.5}
ctx = fake_ctx(discounting, {"Abbott": {"relationship": 0}})
assert ENGINE.resolve(discounting, ctx, [beat("Abbott", "confided_secret", False)], [])[0].payload["value"] == 4
ctx = fake_ctx(discounting, {"Abbott": {"relationship": 0}})
assert ENGINE.resolve(discounting, ctx, [beat("Abbott", "public_slight", False)], [])[0].payload["value"] == -12, \
    "a slight that went unanswered is not a lesser slight"
ctx = fake_ctx(CFG, {"Abbott": {"relationship": 0}})
assert ENGINE.resolve(CFG, ctx, [beat("Abbott", "confided_secret", False)], [])[0].payload["value"] == 8, \
    "with no factor authored, reciprocation must change nothing"
print("OK: unreciprocated_factor discounts positive registers only, and is off by default")

# --- tiers, including the negative direction ----------------------------------------
assert ENGINE.tier_for(CFG, 30)["label"] == "trusted"
assert ENGINE.tier_for(CFG, 25)["label"] == "trusted", "`at` is inclusive"
assert ENGINE.tier_for(CFG, 24) is None
assert ENGINE.tier_for(CFG, -30)["label"] == "closed off"
assert ENGINE.tier_for(CFG, 0) is None
laddered = {**CFG, "tiers": [{"at": 10, "label": "warm"}, {"at": 30, "label": "trusted"}]}
assert ENGINE.tier_for(laddered, 40)["label"] == "trusted", "the highest reached tier wins"
print("OK: tier_for picks the highest tier reached in each direction, inclusive of `at`")

# --- the per-window cap -------------------------------------------------------------
capped = {**CFG, "cap_per_window": {"delta": 10, "turns": 3}}
ctx = fake_ctx(capped, {"Abbott": {"relationship": 0}}, turn=5,
               engine_state={"window": {"Abbott": [[4, 6]]}})
effects = ENGINE.resolve(capped, ctx, [beat("Abbott", "confided_secret")], [])
assert effects[0].payload["delta"] == 4, "6 already spent in the window leaves room for 4 of 8"
ctx = fake_ctx(capped, {"Abbott": {"relationship": 0}}, turn=5,
               engine_state={"window": {"Abbott": [[4, 6], [5, 4]]}})
assert ENGINE.resolve(capped, ctx, [beat("Abbott", "confided_secret")], [])[0].payload["delta"] == 0
ctx = fake_ctx(capped, {"Abbott": {"relationship": 0}}, turn=9,
               engine_state={"window": {"Abbott": [[4, 6], [5, 4]]}})
assert ENGINE.resolve(capped, ctx, [beat("Abbott", "confided_secret")], [])[0].payload["delta"] == 8, \
    "spend older than the window must not count against it"
ctx = fake_ctx(capped, {"Abbott": {"relationship": 0}}, turn=5,
               engine_state={"window": {"Abbott": [[4, 6]]}})
assert ENGINE.resolve(capped, ctx, [beat("Abbott", "public_slight")], [])[0].payload["delta"] == -4, \
    "the cap is on magnitude, in both directions"
print("OK: cap_per_window limits magnitude over a rolling window, both directions")

# --- eviction: closest to neutral first, never an authored character ----------------
roster = {name: {"relationship": score} for name, score in
          [("Neutral", 0), ("Mild", 3), ("Strong", 40), ("Hated", -45), ("Authored", 0)]}
ctx = fake_ctx(CFG, roster, authored=["Authored"])
evicted = [e.payload["target"] for e in ENGINE.resolve(CFG, ctx, [], []) if e.kind == "relationships.evict"]
assert evicted == ["Neutral"], evicted
assert "Authored" not in evicted, "an authored character is never evicted, regardless of score"
print("OK: eviction drops the closest to neutral and never an authored character")

# --- a newcomer is scored before the roster is measured ------------------------------
full = {f"Char {i}": {"relationship": 1 + i} for i in range(4)}
ctx = fake_ctx(CFG, full)
effects = ENGINE.resolve(CFG, ctx, [beat("Newcomer", "confided_secret")], [])
evicted = [e.payload["target"] for e in effects if e.kind == "relationships.evict"]
assert evicted == ["Char 0"], evicted
assert "Newcomer" not in evicted, (
    "a newcomer scored this turn must not be dropped on arrival - at its priced +8 it "
    "outranks every existing entry, and would only be the closest to neutral if eviction "
    "had measured the roster before the beat was applied")
print("OK: a newcomer is priced before eviction measures the roster")

# --- a story that declares the engine and no price list fails loudly ----------------
try:
    ENGINE.resolve({"engine": "scored_axis"}, fake_ctx({}, {}), [], [])
    raise AssertionError("a scored_axis with no registers must not silently do nothing")
except ValueError as exc:
    assert "registers" in str(exc), exc
print("OK: declaring scored_axis without a `registers` price list raises rather than going inert")


# =====================================================================================
# 2. Through the real observation and narration prompts
# =====================================================================================

def observation_prompt(ctx, diff=None):
    recorder = RecordingLLM(lambda p: dict(diff or EMPTY_DIFF))
    se.call_llm_json = recorder
    se.update_progress_from_turn(ctx, "look around", "narration text")
    return recorder.prompts[-1]


# --- the default story's authored axis, scale and vocabulary all reach the prompt ----
ctx = se.state_store.load_state("relaxistest", se.state_store.DEFAULT_STORY_SLUG)
prompt = observation_prompt(ctx)
assert '"social"' in prompt
assert "-100 hostile to +100 devoted" in prompt
assert "confided_in_them" in prompt and "went_behind_their_back" in prompt
assert "relationship_changes" not in prompt, "the v2 field is gone, not merely unused"
assert "trust and warmth built" not in prompt, \
    "axis.description is a display label now (§3.3), never an arithmetic instruction"
print("OK: scale, axis labels and the authored register vocabulary reach the observation prompt")

# --- a custom axis and scale render correctly, and nothing assumes +/-100 ------------
with_story(ctx, lambda s: s["mechanics"]["relationships"].update(
    scale={"min": -50, "max": 50},
    axis={"negative": "disregard", "positive": "devotion", "description": "esteem earned"}))
prompt = observation_prompt(ctx)
assert "-50 disregard to +50 devotion" in prompt, prompt[prompt.find("CURRENT STANDING"):][:200]
print("OK: a custom axis and scale render correctly in the observation prompt")

# --- scores and tier labels reach the narration prompt's PLAYER line -----------------
ctx["state"]["characters"] = {
    "Someone": {"relationship": 5, "first_seen_turn": 0, "introduced": True},
    "Trusted": {"relationship": 60, "first_seen_turn": 0, "introduced": True},
}
narration_prompt = se.build_system_prompt(ctx)
assert "Relationships: Someone +5, Trusted +60 (confiding)" in narration_prompt, narration_prompt
assert "will say the thing Millbrook does not say out loud" in narration_prompt, \
    "a tier carrying authored narration guidance must reach the narrator once it is reached"
print("OK: PLAYER-line scores carry tier labels, and a reached tier's guidance reaches the narrator")

# --- a tier nobody has reached contributes nothing (P-2, applied to tiers) -----------
ctx["state"]["characters"] = {"Someone": {"relationship": 5, "first_seen_turn": 0, "introduced": True}}
assert "will say the thing Millbrook does not say out loud" not in se.build_system_prompt(ctx)
print("OK: an unreached tier contributes no guidance at all")

# --- absent engine: nothing in either prompt, and a stray `social` key is ignored ----
no_rel_ctx = se.state_store.load_state("relaxistest2", se.state_store.DEFAULT_STORY_SLUG)
with_story(no_rel_ctx, lambda s: s["mechanics"].pop("relationships", None))
prompt = observation_prompt(no_rel_ctx, {
    **EMPTY_DIFF, "social": [{"target": "Sneaky", "register": "kindness_shown"}]})
assert '"social"' not in prompt
assert "CURRENT STANDING" not in prompt
assert "Sneaky" not in no_rel_ctx["state"]["characters"], \
    "a stray social beat must be ignored by a story that never declared the mechanic"
assert "Relationships:" not in se.build_system_prompt(no_rel_ctx)
assert "standing is" not in se.build_system_prompt(no_rel_ctx)
print("OK: an absent engine leaks nothing into either prompt and ignores a stray social key")

# --- declared-but-undeclared-engine: a mechanics block with no `engine` key binds nothing.
# This is phase 1's declare-to-bind rule, and it is the thing that makes a half-finished
# port visible instead of silent - the block is there, the mechanic is not. ---
undeclared_ctx = se.state_store.load_state("relaxistest2b", se.state_store.DEFAULT_STORY_SLUG)
with_story(undeclared_ctx, lambda s: s["mechanics"]["relationships"].pop("engine"))
assert mechanics.bound_for(se.state_store.thaw(undeclared_ctx["story"]), "relationships") is None
assert '"social"' not in observation_prompt(undeclared_ctx)
print("OK: a relationships block without an `engine` key binds nothing (declare-to-bind)")

# --- the engine's `limit` overrides the default ---------------------------------------
limited_ctx = se.state_store.load_state("relaxistest3", se.state_store.DEFAULT_STORY_SLUG)
with_story(limited_ctx, lambda s: s["mechanics"]["relationships"].update(limit=2))
limited_ctx["state"]["characters"] = {
    "A": {"relationship": 1, "first_seen_turn": 0, "introduced": False},
    "B": {"relationship": 2, "first_seen_turn": 0, "introduced": False},
}
se.call_llm_json = CannedResponses([
    {**EMPTY_DIFF, "social": [{"target": "C", "register": "confided_in_them"}]},
])
se.update_progress_from_turn(limited_ctx, "meet someone new", "narration text")
assert len(limited_ctx["state"]["characters"]) == 2, limited_ctx["state"]["characters"]
assert "A" not in limited_ctx["state"]["characters"], "closest-to-neutral should be evicted first"
assert "C" in limited_ctx["state"]["characters"]
print("OK: the engine's `limit` overrides the default")

# --- the event log records what was observed, not what was decided (§8.2) ------------
log_ctx = se.state_store.load_state("relaxistest4", se.state_store.DEFAULT_STORY_SLUG)
se.call_llm_json = CannedResponses([
    {**EMPTY_DIFF, "social": [{"target": "Mrs. Abbott", "register": "kindness_shown",
                               "reciprocated": False}]},
])
se.update_progress_from_turn(log_ctx, "offer tea", "narration text")
events = [e for e in log_ctx["state"].get("events", []) if e["type"] == "social"]
assert len(events) == 1, log_ctx["state"].get("events")
assert events[0]["target"] == "Mrs. Abbott" and events[0]["register"] == "kindness_shown"
assert events[0]["reciprocated"] is False
assert "delta" not in events[0], "the log holds the observation; the price is the engine's"
print("OK: the save's event log records the raw social observation, not the priced delta")

print("\nALL CHECKS PASSED: test_relationships_axis")

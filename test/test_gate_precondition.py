"""The `gate` / `precondition` engine and its predicate evaluator - engine v2 phase 6.

Covers the two gates phase 6 names by name, plus the engine/model split §7.4 rests on:

  - **The latching test.** A flag predicate must still read true after the flag has been
    archived out of `flags.active`, because `archive_stale_flags` retires it there on a
    10-turn window while `act_check_frequency` defaults to 12. The naive implementation
    reads `active` alone and is reliably false at exactly the moment it is consulted. This
    is the single most specific thing phase 6 asks to be tested.
  - **No reachable deadlock.** Every way a predicate can be wrong - an unknown revelation
    id, a flag that was never set anywhere, an unimplemented kind, a stat axis the save does
    not carry, an empty `any` - degrades to satisfied rather than blocking forever.
  - **The engine decides, the model only recognises.** `unmet()` must never return a gate
    whose predicate is met, because that list is exactly what the detector shows the model,
    and a model that can see a satisfied gate is a model that can invent a refusal.
  - **§2.2's act half.** The same evaluator pointed at act advancement: an unmet `requires`
    must skip the director call entirely (the engine decides necessity, the model still owns
    sufficiency), an authored `requires` must survive the act merge, and both the latching and
    no-deadlock rules must hold *there* and not merely in the evaluator's unit tests - the
    merge dropping the field silently is exactly how this feature could read as working while
    doing nothing.

Run directly: python3 test/test_gate_precondition.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import CannedResponses, RecordingLLM, load_story_engine  # noqa: E402

se = load_story_engine()
mechanics = se.mechanics
gate = mechanics.gate
ENGINE = gate.ENGINE


def ctx_with(flags_active=None, flags_archive=None, revealed=None, inventory=None, stats=None):
    return {
        "story": {"mechanics": {}},
        "state": {
            "protagonist": {
                "flags": {"active": dict(flags_active or {}), "archive": dict(flags_archive or {})},
                "inventory": list(inventory or []),
                "stats": dict(stats or {}),
            },
            "plot": {"revelations_revealed": dict(revealed or {})},
            "scene": {"location": "loc_a", "summary": "a room"},
        },
    }


# =====================================================================================
# 1. The latching test - phase 6's named gate
# =====================================================================================
archived = ctx_with(flags_active={}, flags_archive={"warned_off": True})
assert gate.satisfied({"flag": "warned_off"}, archived) is True, \
    "a flag predicate must read flags.active UNION flags.archive, not active alone"
active = ctx_with(flags_active={"warned_off": True})
assert gate.satisfied({"flag": "warned_off"}, active) is True
print("OK: a flag predicate still holds once the flag has aged out of flags.active into archive")

assert gate.satisfied({"flag": "never_happened"}, archived) is False
print("OK: a flag that was never set anywhere reads false, so the predicate still means something")

# =====================================================================================
# 2. Leaf kinds
# =====================================================================================
revealed = ctx_with(revealed={"rev_001": {"turn": 3}})
assert gate.satisfied({"revelation": "rev_001"}, revealed) is True
assert gate.satisfied({"revelation": "rev_002"}, revealed) is True, \
    "an unknown revelation id degrades to satisfied - see the no-deadlock rule"
print("OK: a revelation predicate reads revelations_revealed")

tagged = ctx_with(inventory=[{"id": "itm_001", "label": "brass key", "tags": ["key"]}])
assert gate.satisfied({"item_tag": "key"}, tagged) is True
assert gate.satisfied({"item_tag": "crowbar"}, tagged) is False
bare = ctx_with(inventory=["a pre-engine save's bare string"])
assert gate.satisfied({"item_tag": "key"}, bare) is False
print("OK: item_tag reads item tags, and a pre-engine save's bare strings carry none")

statted = ctx_with(stats={"sync": 40})
assert gate.satisfied({"stat": {"axis": "sync", "at_least": 40}}, statted) is True
assert gate.satisfied({"stat": {"axis": "sync", "at_least": 41}}, statted) is False
assert gate.satisfied({"stat": {"axis": "sync", "at_most": 39}}, statted) is False
assert gate.satisfied({"stat": {"axis": "nonexistent", "at_least": 99}}, statted) is True, \
    "an axis the save does not carry degrades - it is unreachable, not merely unmet"
print("OK: a stat predicate honours at_least/at_most and degrades on an unknown axis")

# =====================================================================================
# 3. Combinators
# =====================================================================================
both = ctx_with(flags_archive={"a": True}, revealed={"rev_001": {"turn": 1}})
assert gate.satisfied({"all": [{"flag": "a"}, {"revelation": "rev_001"}]}, both) is True
assert gate.satisfied({"all": [{"flag": "a"}, {"flag": "missing"}]}, both) is False
assert gate.satisfied({"any": [{"flag": "missing"}, {"flag": "a"}]}, both) is True
assert gate.satisfied({"any": [{"flag": "missing"}, {"flag": "gone"}]}, both) is False
assert gate.satisfied({"not": {"flag": "missing"}}, both) is True
assert gate.satisfied({"not": {"flag": "a"}}, both) is False
print("OK: all/any/not compose, and nest")

# =====================================================================================
# 4. No reachable deadlock - every malformed shape degrades to satisfied
# =====================================================================================
empty = ctx_with()
for predicate in (None, {}, [], "nonsense", {"any": []}, {"all": []},
                  {"subplot": "sub_001"}, {"tier": "Mira"}):
    assert gate.satisfied(predicate, empty) is True, f"{predicate!r} must not block forever"
print("OK: an absent, malformed, empty or unimplemented predicate degrades to satisfied")

# =====================================================================================
# 5. The engine half - unmet() never shows the model a gate that is already open
# =====================================================================================
cfg = {
    "engine": "precondition",
    "gates": [
        {"id": "locked", "target": "loc_vault", "requires": {"item_tag": "vault_key"},
         "refusal_hint": "The door does not argue."},
        {"id": "open", "target": "loc_hall", "requires": {"flag": "a"},
         "refusal_hint": "unreachable"},
    ],
}
world = ctx_with(flags_archive={"a": True})
unmet = ENGINE.unmet(cfg, world)
assert [g["id"] for g in unmet] == ["locked"], [g["id"] for g in unmet]
print("OK: unmet() returns only gates whose predicate is currently false")

carrying = ctx_with(flags_archive={"a": True},
                    inventory=[{"id": "itm_001", "label": "vault key", "tags": ["vault_key"]}])
assert ENGINE.unmet(cfg, carrying) == []
print("OK: a gate whose predicate is satisfied is never offered for refusal")

try:
    ENGINE.gates({"engine": "precondition"})
    raise AssertionError("a precondition engine with no gates must not go inert")
except ValueError as exc:
    assert "gates" in str(exc), exc
print("OK: declaring the engine with no gates raises rather than adjudicating nothing")

# =====================================================================================
# 6. The detector - the model may only recognise, never grant
# =====================================================================================
story = se.state_store.freeze({"mechanics": {"gate": cfg}})
ctx = ctx_with(flags_archive={"a": True})
ctx["story"] = story

assert se.detect_gate_refusal({"story": se.state_store.freeze({"mechanics": {}}),
                               "state": ctx["state"]}, "anything") is None
print("OK: a story authoring no gate block makes no call at all")

recorder = RecordingLLM(lambda p: {"blocked": 1, "sentence": "The door does not move."})
se.call_llm_json = recorder
refusal = se.detect_gate_refusal(ctx, "I try the vault door")
assert refusal == {"gate": "locked", "sentence": "The door does not move."}, refusal
prompt = recorder.prompts[-1]
offered = prompt.split("CLOSED TO THE PLAYER RIGHT NOW:")[1].split("PLAYER ACTION")[0]
assert "loc_vault" in offered and "The door does not argue." in offered
assert "loc_hall" not in offered, "a satisfied gate must never reach the detector's prompt"
# Measured on a live run: the model wrote a gate's own id into the player-facing sentence.
# The id is a system identifier, so the fix is to never show it one - not to ask it nicely.
assert "locked" not in offered, "a gate id must never reach the model - it leaks into prose"
print("OK: the detector is shown only unmet gates, by number and authored tone, never by id")

se.call_llm_json = RecordingLLM(lambda p: {"blocked": None, "sentence": ""})
assert se.detect_gate_refusal(ctx, "I sit down and wait") is None
print("OK: a null verdict means the turn proceeds normally")

for invented in (2, 0, -1, 99, "locked", "open"):
    se.call_llm_json = RecordingLLM(lambda p: {"blocked": invented, "sentence": "invented"})
    assert se.detect_gate_refusal(ctx, "I walk into the hall") is None, invented
print("OK: the model cannot invent a refusal - any number outside the unmet list is dropped")

se.call_llm_json = RecordingLLM(lambda p: {"blocked": 1, "sentence": "   "})
assert se.detect_gate_refusal(ctx, "I try the vault door")["sentence"] == "The door does not argue.", \
    "an empty sentence falls back to the authored hint rather than showing the player nothing"
print("OK: an empty sentence falls back to the gate's refusal_hint")

# =====================================================================================
# 7. The hard rail - a gated location is vetoed however the narration got there
# =====================================================================================
# The detector is a model reading prose and misses roughly a third of oblique attempts
# (measured, 6/9 recall). This is the half that never misses: scene_update.location is a
# closed set, so refusing a move needs no judgement.
cfg_vault = {"engine": "precondition", "gates": [
    {"id": "vault", "target": "loc_vault", "requires": {"item_tag": "vault_key"},
     "refusal_hint": "The door does not argue."}]}
assert ENGINE.blocking(cfg_vault, ctx_with(), "loc_vault")["id"] == "vault"
assert ENGINE.blocking(cfg_vault, ctx_with(), "loc_hall") is None
carrying_key = ctx_with(inventory=[{"id": "i1", "label": "key", "tags": ["vault_key"]}])
assert ENGINE.blocking(cfg_vault, carrying_key, "loc_vault") is None
print("OK: blocking() names the gate refusing a location, and nothing once its predicate is met")

sections = mechanics.prompt_sections({"story": se.state_store.freeze({"mechanics": {"gate": cfg_vault}}),
                                      "state": ctx_with()["state"]})
assert "gate.closed" in sections and "The door does not argue." in sections["gate.closed"]
open_sections = mechanics.prompt_sections(
    {"story": se.state_store.freeze({"mechanics": {"gate": cfg_vault}}), "state": carrying_key["state"]})
assert "gate.closed" not in open_sections, "P-2: a story with nothing shut contributes no header"
print("OK: the narrator is told what is shut, and nothing at all when nothing is")

# =====================================================================================
# 8. The refusal path - no narration, no observation pass, no state change
# =====================================================================================
holder = {"ctx": se.state_store.load_state("gatetest", se.state_store.DEFAULT_STORY_SLUG)}
story = se.state_store.thaw(holder["ctx"]["story"])
story["mechanics"]["gate"] = cfg_vault
holder["ctx"]["story"] = se.state_store.freeze(story)
se.state_store.load_state = lambda *a, **k: holder["ctx"]
se.state_store.save_state = lambda c, *a, **k: holder.update(ctx=c)

before = se.state_store.thaw(holder["ctx"]["state"])
narration = RecordingLLM(lambda p: "should never be called")
se.call_llm = narration
se.call_llm_json = RecordingLLM(lambda p: {"blocked": 1, "sentence": "The door does not move."})
try:
    se.take_turn("I try the vault door")
    raise AssertionError("a refused action must not fall through into a turn")
except se.ActionRefused as exc:
    assert exc.sentence == "The door does not move." and exc.gate == "vault", exc
assert narration.prompts == [], "a refused action must never reach the narration call"
assert se.state_store.thaw(holder["ctx"]["state"]) == before, "a refusal must change no state"
print("OK: a refused action raises ActionRefused, with no narration call and no state change")

se.call_llm = CannedResponses(["A scene.\n\nOPTIONS:\n1. a || a\n2. b || b\n3. c || c"])
se.call_llm_json = CannedResponses([
    {"blocked": None, "sentence": ""},
    {"flags_set": {}, "scene_update": {"location": "loc_vault", "summary": "inside the vault",
                                       "present_npcs": []}, "new_characters": []},
])
location_before = holder["ctx"]["state"]["scene"]["location"]
se.take_turn("I walk around")
assert holder["ctx"]["state"]["scene"]["location"] == location_before, \
    "the veto must refuse a gated location even when the narration went there"
assert holder["ctx"]["state"]["scene"]["summary"] == "inside the vault", \
    "only the location is vetoed - the narration the player read is left alone"
print("OK: the veto refuses a gated scene_update.location the detector let through")

# =====================================================================================
# §2.2 - the same evaluator, pointed at act advancement
# =====================================================================================

def act_ctx(requires=None, flags_active=None, flags_archive=None, revealed=None):
    """A save sitting exactly on an act checkpoint, so check_and_advance_act runs its real
    path. `requires` is authored on the act, never in mechanics - a story may carry act
    preconditions with no gate block at all."""
    act = {"act_number": 1, "title": "Arrival", "description": "the first act",
           "completion_signals": ["something happens"]}
    if requires is not None:
        act["requires"] = requires
    story = {
        "meta": {"title": "t"},
        "world": {"characters": {}},
        "plot": {"main_thread": {"acts": [act]}, "pacing": {"act_check_frequency": 1}},
    }
    return {
        "story": se.state_store.freeze(story),
        "state": {
            "protagonist": {
                "flags": {"active": dict(flags_active or {}), "archive": dict(flags_archive or {})},
                "inventory": [], "stats": {},
            },
            "plot": {
                "current_act": 1, "act_completion": {}, "generated_acts": [], "act_history": [],
                "subplots": {}, "completed_subplots": [], "thread_steering": {},
                "revelations_revealed": dict(revealed or {}),
                "endgame": {"requested": False}, "entity_contact_count": 0,
            },
            "pacing": {"turn_count": 12, "turns_since_act_check": 5,
                       "subplots_completed_this_act": 0},
            "history": {"compressed_summary": "", "recent_turns": []},
            "characters": {},
        },
    }


# --- an authored `requires` survives the act merge ------------------------------------
# The merge is a fixed projection, not a copy, so a field it does not name is dropped. That
# is not hypothetical: `requires` was dropped until phase 6's act half went looking for it,
# and every assertion below would have passed anyway with the predicate invisible.
merged = mechanics.current_act(act_ctx(requires={"flag": "warned_off"}))
assert merged.get("requires") == {"flag": "warned_off"}, \
    "an authored requires must reach the caller, or the floor silently does not exist"
assert "requires" not in mechanics.current_act(act_ctx()), \
    "P-2: an act with no requires carries no empty one"
print("OK: an authored act `requires` survives the act merge, and an absent one stays absent")

# --- unmet: no director call at all ----------------------------------------------------
recorder = RecordingLLM(lambda p: {})
se.call_llm_json = recorder
ctx = act_ctx(requires={"flag": "never_set"})
assert se.check_and_advance_act(ctx) is None
assert recorder.prompts == [], \
    "an unmet act precondition must cost no LLM call - that is §2.2's whole point"
print("OK: an unmet act `requires` skips the director call entirely, no verdict, no advance")

# --- met: the director is asked, and may still refuse ----------------------------------
se.call_llm_json = CannedResponses([{"ready": False, "reason": "not yet"}])
ctx = act_ctx(requires={"flag": "warned_off"}, flags_active={"warned_off": {"value": True}})
assert se.check_and_advance_act(ctx) is None
assert ctx["state"]["plot"]["current_act"] == 1, "the director said no and that stands"
print("OK: a met `requires` hands the verdict to the director, which can still refuse (§2.1)")

# --- the latching rule, at the act level ------------------------------------------------
# The evaluator's own latching test is above; this is the one that matters in production,
# because act_check_frequency (12) is longer than the flag's life in `active` (10), so the
# flag is *reliably* in archive by the time an act check consults it.
recorder = RecordingLLM(lambda p: {"ready": False, "reason": "not yet"})
se.call_llm_json = recorder
ctx = act_ctx(requires={"flag": "warned_off"}, flags_archive={"warned_off": {"value": True}})
se.check_and_advance_act(ctx)
assert recorder.prompts, \
    "a flag aged into archive must still satisfy an act precondition, or no act ever advances"
print("OK: an act `requires` on an archived flag still advances - the latching rule holds "
      "where act_check_frequency actually consults it")

# --- no reachable deadlock, at the act level --------------------------------------------
recorder = RecordingLLM(lambda p: {"ready": False, "reason": "not yet"})
se.call_llm_json = recorder
ctx = act_ctx(requires={"revelation": "rev_that_no_template_defines"})
se.check_and_advance_act(ctx)
assert recorder.prompts, \
    "an act requiring an unknown referent must degrade to satisfied, never strand the thread"
print("OK: an act `requires` naming an unknown referent degrades rather than blocking forever")

# --- non-latching referents on an act are warned about, not raised on --------------------
assert gate.non_latching_referents({"all": [{"revelation": "r"}, {"stat": {"axis": "x"}},
                                            {"item_tag": "k"}]}) == ["item_tag", "stat"]
assert gate.non_latching_referents({"all": [{"revelation": "r"}, {"flag": "f"}]}) == []
assert gate.non_latching_referents(None) == [] and gate.non_latching_referents("junk") == []
print("OK: non-latching referents in an act predicate are reported for a warning, and the "
      "reporter itself never raises")

print("\nALL CHECKS PASSED: test_gate_precondition")

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

Run directly: python3 test/test_gate_precondition.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import RecordingLLM, load_story_engine  # noqa: E402

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

recorder = RecordingLLM(lambda p: {"gate_id": "locked", "sentence": "The door does not move."})
se.call_llm_json = recorder
refusal = se.detect_gate_refusal(ctx, "I try the vault door")
assert refusal == {"gate": "locked", "sentence": "The door does not move."}, refusal
prompt = recorder.prompts[-1]
assert "locked" in prompt and "The door does not argue." in prompt
assert "open" not in prompt.split("CLOSED TO THE PLAYER RIGHT NOW:")[1].split("PLAYER ACTION")[0], \
    "a satisfied gate must never reach the detector's prompt"
print("OK: the detector is shown only unmet gates, with their authored tone")

se.call_llm_json = RecordingLLM(lambda p: {"gate_id": None, "sentence": ""})
assert se.detect_gate_refusal(ctx, "I sit down and wait") is None
print("OK: gate_id null means the turn proceeds normally")

se.call_llm_json = RecordingLLM(lambda p: {"gate_id": "open", "sentence": "invented"})
assert se.detect_gate_refusal(ctx, "I walk into the hall") is None, \
    "the model naming a gate that is not unmet must not produce a refusal"
se.call_llm_json = RecordingLLM(lambda p: {"gate_id": "not_a_gate", "sentence": "invented"})
assert se.detect_gate_refusal(ctx, "I do something else") is None
print("OK: the model cannot invent a refusal - an id outside the unmet list is dropped")

se.call_llm_json = RecordingLLM(lambda p: {"gate_id": "locked", "sentence": "   "})
assert se.detect_gate_refusal(ctx, "I try the vault door")["sentence"] == "The door does not argue.", \
    "an empty sentence falls back to the authored hint rather than showing the player nothing"
print("OK: an empty sentence falls back to the gate's refusal_hint")

print("\nALL CHECKS PASSED: test_gate_precondition")

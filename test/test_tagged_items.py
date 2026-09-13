"""The `inventory` / `tagged_items` engine - engine v2 phase 4, port 2.

Same two halves as test_relationships_axis.py: pure `resolve()` unit tests with no stubs at
all (ENGINE_V2_SPEC §9), then integration through the real observation pass for the things a
unit test cannot see.

The port's own headline is the absent-engine case, so it gets the most coverage here. v2
asked *every* story for `items_gained` and `items_lost` on *every* turn - a courtroom drama
with no inventory concept included - because inventory had no module to be absent from.
That was a standing P-2 violation nothing could catch, and it is where phase 4's field-count
reduction actually comes from: two unconditional fields become one declared one.

Run directly: python3 test/test_tagged_items.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import CannedResponses, RecordingLLM, load_story_engine  # noqa: E402

se = load_story_engine()
mechanics = se.mechanics
ENGINE = mechanics.items.ENGINE

EMPTY_DIFF = {
    "subplot_beats": {}, "flags_set": {}, "revelations": {"revealed": [], "eligible": []},
    "inventory": {"gained": [], "used": []}, "social": [], "new_characters": [],
}


def with_story(ctx, mutate):
    story_dict = se.state_store.thaw(ctx["story"])
    mutate(story_dict)
    ctx["story"] = se.state_store.freeze(story_dict)


def fake_ctx(cfg, items=None):
    return {"story": {"mechanics": {"inventory": cfg}},
            "state": {"protagonist": {"inventory": list(items or [])},
                      "pacing": {"turn_count": 0}}}


def apply(cfg, ctx, events):
    """resolve() then apply, so a unit test can assert on resulting state rather than only
    on the effect list - the two are separate steps precisely so both are checkable."""
    mechanics.apply_effects(ctx, ENGINE.resolve(cfg, ctx, events, []))
    return [ENGINE._record(e) for e in ctx["state"]["protagonist"]["inventory"]]


def gained(label, tags=()):
    return {"type": "item_gained", "label": label, "tags": list(tags)}


def used(item):
    return {"type": "item_used", "item": item}


CFG = {"engine": "tagged_items", "tags": ["key", "tool", "food"]}

# =====================================================================================
# 1. Pure engine rules - no stubs
# =====================================================================================

# --- acquisition mints a record with an id -------------------------------------------
ctx = fake_ctx(CFG)
records = apply(CFG, ctx, [gained("a brass key", ["key"]), gained("a tin of biscuits", ["food"])])
assert [r["id"] for r in records] == ["itm_001", "itm_002"], records
assert [r["label"] for r in records] == ["a brass key", "a tin of biscuits"]
assert records[0]["tags"] == ["key"]
print("OK: acquisition stores a record with a minted id, a label and tags")

# --- ids are never reused after a removal -------------------------------------------
records = apply(CFG, ctx, [used("itm_001")])
assert [r["id"] for r in records] == ["itm_002"]
records = apply(CFG, ctx, [gained("a crowbar", ["tool"])])
assert [r["id"] for r in records] == ["itm_002", "itm_003"], \
    "numbering scans for the highest id, so a retired id is never handed out again"
print("OK: a removed item's id is never reused by a later acquisition")

# --- expenditure by id, and the label fallback for pre-engine saves ------------------
ctx = fake_ctx(CFG, ["a torn letter", {"id": "itm_007", "label": "a lantern", "tags": []}])
records = apply(CFG, ctx, [used("itm_007")])
assert [r["label"] for r in records] == ["a torn letter"]
records = apply(CFG, ctx, [used("a torn letter")])
assert records == [], "a bare-string entry from a pre-engine save is still consumable by label"
print("OK: expenditure matches by id, falling back to an exact label for pre-engine entries")

# --- an unmatched expenditure is a no-op, exactly as v2's unmatched items_lost was ---
ctx = fake_ctx(CFG, [{"id": "itm_001", "label": "a brass key", "tags": []}])
assert len(apply(CFG, ctx, [used("itm_404"), used("a sword")])) == 1
print("OK: an expenditure citing something not held changes nothing and does not raise")

# --- gains land before expenditures, so a thing picked up and spent this turn works ---
ctx = fake_ctx(CFG)
records = apply(CFG, ctx, [used("itm_001"), gained("a match", ["tool"])])
assert records == [], "the gain must be visible to the expenditure regardless of event order"
print("OK: gains resolve before expenditures, so a same-turn pickup-and-spend works")

# --- `uses` decrements instead of removing, and runs out ----------------------------
ctx = fake_ctx(CFG, [{"id": "itm_001", "label": "a headlamp", "tags": ["tool"], "uses": 2}])
records = apply(CFG, ctx, [used("itm_001")])
assert records[0]["uses"] == 1, records
records = apply(CFG, ctx, [used("itm_001")])
assert records == [], "the last use removes it"
print("OK: an item with `uses` spends one per expenditure and is removed when they run out")

# --- an item with no `uses` is removed outright: v2's behaviour is the default -------
ctx = fake_ctx(CFG, [{"id": "itm_001", "label": "a match", "tags": []}])
assert apply(CFG, ctx, [used("itm_001")]) == []
print("OK: an item with no authored `uses` is removed outright - v2's behaviour, unchanged")

# --- capacity refuses the gain rather than dropping something already held ----------
capped = {**CFG, "capacity": 2}
ctx = fake_ctx(capped, [{"id": "itm_001", "label": "a rope", "tags": []},
                        {"id": "itm_002", "label": "a flare", "tags": []}])
effects = ENGINE.resolve(capped, ctx, [gained("an axe", ["tool"])], [])
assert [e.kind for e in effects] == ["inventory.refused"], effects
assert effects[0].payload["label"] == "an axe"
records = apply(capped, ctx, [gained("an axe", ["tool"])])
assert [r["label"] for r in records] == ["a rope", "a flare"], \
    "nothing already held may be silently dropped to make room"
print("OK: over capacity the gain is refused and logged; nothing held is dropped")

# --- once room is made, the same gain lands ------------------------------------------
records = apply(capped, ctx, [used("itm_001"), gained("an axe", ["tool"])])
assert [r["label"] for r in records] == ["a flare", "an axe"]
print("OK: a refused gain is not permanent - it lands once something is put down")

# --- events(): malformed entries are dropped before they can be priced --------------
ctx = fake_ctx(CFG)
assert ENGINE.events(CFG, ctx, {"inventory": "not a dict"}) == []
assert ENGINE.events(CFG, ctx, {}) == []
assert ENGINE.events(CFG, ctx, {"inventory": {"gained": [{"tags": ["key"]}], "used": ["", None]}}) == []
flattened = ENGINE.events(CFG, ctx, {"inventory": {"gained": ["a rope"], "used": []}})
assert flattened == [{"type": "item_gained", "label": "a rope", "tags": []}], \
    "a flattened string gain is tolerated - dropping it would be a player-visible loss"
print("OK: malformed observation entries are dropped, and a flattened string gain still lands")


# =====================================================================================
# 2. Through the real observation and narration prompts
# =====================================================================================

def observation_prompt(ctx, diff=None):
    recorder = RecordingLLM(lambda p: dict(diff or EMPTY_DIFF))
    se.call_llm_json = recorder
    se.update_progress_from_turn(ctx, "look around", "narration text")
    return recorder.prompts[-1]


# --- the declared story asks one field, with its authored tag vocabulary ------------
ctx = se.state_store.load_state("taggeditemstest", se.state_store.DEFAULT_STORY_SLUG)
prompt = observation_prompt(ctx)
assert '"inventory"' in prompt and '"gained"' in prompt and '"used"' in prompt
assert "keepsake" in prompt, "an authored tag vocabulary is offered as a closed list"
assert "items_gained" not in prompt and "items_lost" not in prompt, \
    "the two v2 fields are gone, not merely unused"
assert "matching an existing inventory entry exactly" not in prompt, \
    "the exact-string instruction went with the exact-string match"
print("OK: a declared story asks one inventory field carrying its authored tag vocabulary")

# --- CURRENT INVENTORY shows ids to cite, and reflects records ----------------------
ctx["state"]["protagonist"]["inventory"] = [
    {"id": "itm_009", "label": "a brass key", "tags": ["key"]},
    {"id": "itm_010", "label": "a headlamp", "tags": ["tool"], "uses": 3},
]
prompt = observation_prompt(ctx)
assert "itm_009: a brass key [key]" in prompt, prompt[prompt.find("CURRENT INVENTORY"):][:200]
assert "itm_010: a headlamp [tool] (3 uses left)" in prompt
assert "| Inventory: a brass key, a headlamp" in se.build_system_prompt(ctx)
print("OK: CURRENT INVENTORY lists ids, tags and remaining uses; the PLAYER line lists labels")

# --- an empty inventory still says "nothing": a bound engine with empty state is
# present, and "carrying nothing" is a fact the narrator needs. P-2 is about absent
# modules, not empty ones. ---
ctx["state"]["protagonist"]["inventory"] = []
assert "| Inventory: nothing" in se.build_system_prompt(ctx)
assert "CURRENT INVENTORY (cite these ids in used): empty" in observation_prompt(ctx)
print("OK: an empty inventory renders 'nothing' rather than vanishing")

# --- absent engine: neither prompt mentions inventory at all, and a stray field is
# ignored rather than starting to track items anyway ---
none_ctx = se.state_store.load_state("taggeditemstest2", se.state_store.DEFAULT_STORY_SLUG)
with_story(none_ctx, lambda s: s["mechanics"].pop("inventory", None))
prompt = observation_prompt(none_ctx, {
    **EMPTY_DIFF, "inventory": {"gained": [{"label": "a smuggled rope"}], "used": []}})
assert '"inventory"' not in prompt
assert "CURRENT INVENTORY" not in prompt
assert none_ctx["state"]["protagonist"]["inventory"] == [], \
    "a story that never declared the mechanic must not start accumulating items"
assert "Inventory:" not in se.build_system_prompt(none_ctx)
print("OK: an absent engine leaks nothing into either prompt and ignores a stray field")

# --- declare-to-bind: a block with no `engine` key binds nothing --------------------
undeclared_ctx = se.state_store.load_state("taggeditemstest3", se.state_store.DEFAULT_STORY_SLUG)
with_story(undeclared_ctx, lambda s: s["mechanics"]["inventory"].pop("engine"))
assert mechanics.bound_for(se.state_store.thaw(undeclared_ctx["story"]), "inventory") is None
assert '"inventory"' not in observation_prompt(undeclared_ctx)
print("OK: an inventory block without an `engine` key binds nothing (declare-to-bind)")

# --- seeding starting_inventory without declaring the engine is an authoring smell,
# and mechanics.validate says so. A warning rather than a raise, same as the stats case:
# the items are inert, which is a mistake, but a vestigial block is not broken content. ---
import io  # noqa: E402
import contextlib  # noqa: E402

smelly = {"meta": {"title": "Smelly"}, "world": {"rules": []},
          "protagonist": {"starting_inventory": ["a rope"]},
          "plot": {"main_thread": {"title": "t", "description": "d", "acts": []},
                   "opening_scene": {"narration": "x"}}}
captured = io.StringIO()
with contextlib.redirect_stdout(captured):
    mechanics.validate(smelly)
assert "starting_inventory" in captured.getvalue(), captured.getvalue()
assert "tagged_items" in captured.getvalue()
captured = io.StringIO()
with contextlib.redirect_stdout(captured):
    mechanics.validate({**smelly, "mechanics": {"inventory": {"engine": "tagged_items"}}})
assert captured.getvalue() == "", captured.getvalue()
print("OK: seeding starting_inventory with no declared engine warns; declaring it silences the warning")

# --- end to end through a real turn: a gain and an expenditure in one diff ----------
turn_ctx = se.state_store.load_state("taggeditemstest4", se.state_store.DEFAULT_STORY_SLUG)
turn_ctx["state"]["protagonist"]["inventory"] = []
se.call_llm_json = CannedResponses([
    {**EMPTY_DIFF, "inventory": {"gained": [{"label": "a ledger", "tags": ["document"]}], "used": []}},
    {**EMPTY_DIFF, "inventory": {"gained": [], "used": ["itm_001"]}},
])
se.update_progress_from_turn(turn_ctx, "take the ledger", "narration text")
assert [r["label"] for r in ENGINE.records(turn_ctx)] == ["a ledger"]
se.update_progress_from_turn(turn_ctx, "hand the ledger over", "narration text")
assert ENGINE.records(turn_ctx) == []
events = [e for e in turn_ctx["state"].get("events", []) if e["type"].startswith("item_")]
assert [e["type"] for e in events] == ["item_gained", "item_used"], events
assert events[0]["label"] == "a ledger" and events[1]["item"] == "itm_001"
print("OK: a full turn gains and expends through the pipeline, and the event log records both")

print("\nALL CHECKS PASSED: test_tagged_items")

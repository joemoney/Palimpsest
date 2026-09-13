"""The `revelations` / `triggered_reveal` engine - engine v2 phase 4, port 3.

test_revealed_memories.py still owns the end-to-end behaviour this module inherited (the
REVEALED MEMORIES block, the cap, the never-both-halves rule) and test_pacing_loop.py owns
the §12 placement queue. This file covers what the port *added*, which is the part with no
v2 ancestor to regress against:

  - **the cadence** (§5.2) - the first engine allowed to ask nothing at all;
  - **`after`** - ordering constraints, so a clue chain cannot fire out of sequence;
  - **two fields becoming one**, and an id appearing in both halves resolving deterministically.

Mostly pure: `live()`, `resolve()` and `events()` need no stubs (§9).

Run directly: python3 test/test_triggered_reveal.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import CannedResponses, RecordingLLM, load_story_engine  # noqa: E402

se = load_story_engine()
mechanics = se.mechanics
ENGINE = mechanics.reveal.ENGINE

EMPTY_DIFF = {
    "subplot_beats": {}, "flags_set": {}, "revelations": {"revealed": [], "eligible": []},
    "inventory": {"gained": [], "used": []}, "social": [], "new_characters": [],
}

CHAIN = {
    "engine": "triggered_reveal",
    "entries": [
        {"id": "rev_a", "trigger": "the first contradiction", "content": "content A"},
        {"id": "rev_b", "trigger": "the second contradiction", "content": "content B",
         "after": ["rev_a"]},
        {"id": "rev_c", "trigger": "the confession", "content": "content C",
         "after": ["rev_b"]},
    ],
}


def fake_ctx(cfg, revealed=None, queue=None, pacing_loop=False, turn=0):
    mech = {"revelations": cfg}
    if pacing_loop:
        mech["pacing_loop"] = {"beats": {}}
    return {
        "story": {"mechanics": mech},
        "state": {"plot": {"revelations_revealed": dict(revealed or {})},
                  "pacing": {"turn_count": turn, "reveal_queue": list(queue or [])}},
    }


def with_story(ctx, mutate):
    story_dict = se.state_store.thaw(ctx["story"])
    mutate(story_dict)
    ctx["story"] = se.state_store.freeze(story_dict)


# =====================================================================================
# 1. Pure engine rules - no stubs
# =====================================================================================

# --- `after` gates a chain: only the head is live until it fires ---------------------
ctx = fake_ctx(CHAIN)
assert [e["id"] for e in ENGINE.live(CHAIN, ctx)] == ["rev_a"]
ctx = fake_ctx(CHAIN, revealed={"rev_a": {"turn": 3}})
assert [e["id"] for e in ENGINE.live(CHAIN, ctx)] == ["rev_b"]
ctx = fake_ctx(CHAIN, revealed={"rev_a": {"turn": 3}, "rev_b": {"turn": 5}})
assert [e["id"] for e in ENGINE.live(CHAIN, ctx)] == ["rev_c"]
print("OK: `after` keeps a clue chain in order - one live entry at a time, in sequence")

# --- an `after` naming an id the story does not have degrades to "no constraint" -----
typo = {"engine": "triggered_reveal", "entries": [
    {"id": "rev_a", "trigger": "t", "content": "c", "after": ["rev_typo"]}]}
assert [e["id"] for e in ENGINE.live(typo, fake_ctx(typo))] == ["rev_a"], \
    "a typo in `after` must degrade to no constraint, never to a reveal nothing can unlock"
print("OK: an unknown id in `after` is ignored rather than blocking its entry forever")

# --- the cadence (§5.2): nothing live, nothing asked ---------------------------------
exhausted = fake_ctx(CHAIN, revealed={i: {"turn": 1} for i in ("rev_a", "rev_b", "rev_c")})
assert ENGINE.observations(CHAIN, exhausted) is None
assert ENGINE.live(CHAIN, exhausted) == []
fresh = fake_ctx(CHAIN)
assert ENGINE.observations(CHAIN, fresh) is not None
print("OK: an exhausted chain asks nothing at all; a live one asks exactly one field")

# --- only LIVE triggers reach the observation prompt - never a blocked or fired one --
field = ENGINE.observations(CHAIN, fake_ctx(CHAIN, revealed={"rev_a": {"turn": 1}}))[0]
assert "the second contradiction" in field.context
assert "the first contradiction" not in field.context, "a fired trigger is not still asked about"
assert "the confession" not in field.context, "a blocked trigger is not asked about early"
assert "content A" not in field.context and "content B" not in field.context, \
    "CR-03: the observation pass must never see a fragment's content"
print("OK: only live triggers reach the observation pass, and never any fragment's content")

# --- the `eligible` half exists only where something can consume it ------------------
assert '"eligible"' not in ENGINE.observations(CHAIN, fake_ctx(CHAIN))[0].schema
assert '"eligible"' in ENGINE.observations(CHAIN, fake_ctx(CHAIN, pacing_loop=True))[0].schema
print("OK: the `eligible` half is offered only to a story whose pacing directive can place it")

# --- events(): ids outside the live set are dropped; `revealed` beats `eligible` -----
ctx = fake_ctx(CHAIN, pacing_loop=True)
assert ENGINE.events(CHAIN, ctx, {"revelations": "not a dict"}) == []
assert ENGINE.events(CHAIN, ctx, {"revelations": {"revealed": ["rev_c"], "eligible": ["rev_b"]}}) == [], \
    "rev_b and rev_c are both blocked behind rev_a, so neither can fire"
both = ENGINE.events(CHAIN, ctx, {"revelations": {"revealed": ["rev_a"], "eligible": ["rev_a"]}})
assert both == [{"type": "revelation_revealed", "id": "rev_a"}], both
print("OK: non-live ids are dropped, and an id in both halves counts as revealed, not queued")

# --- no pacing_loop means no queueing even if the model answers with `eligible` ------
no_placing = fake_ctx(CHAIN)
assert ENGINE.events(CHAIN, no_placing, {"revelations": {"revealed": [], "eligible": ["rev_a"]}}) == []
print("OK: without a pacing directive to place it, an eligible reveal is not queued at all")

# --- resolve(): a reveal is recorded at the current turn, and clears the queue -------
ctx = fake_ctx(CHAIN, queue=["rev_a"], pacing_loop=True, turn=11)
effects = ENGINE.resolve(CHAIN, ctx, [{"type": "revelation_revealed", "id": "rev_a"}], [])
mechanics.apply_effects(ctx, effects)
assert ctx["state"]["plot"]["revelations_revealed"]["rev_a"] == {"turn": 11}
assert ctx["state"]["pacing"]["reveal_queue"] == [], \
    "a reveal that actually landed must leave the queue, or the directive keeps asking for it"
print("OK: a reveal records its turn and drops itself from the placement queue")

# --- a queued id already revealed by other means never surfaces as the queued reveal -
ctx = fake_ctx(CHAIN, revealed={"rev_a": {"turn": 2}}, queue=["rev_a"], pacing_loop=True)
assert ENGINE.queued_content(CHAIN, ctx) is None
ctx = fake_ctx(CHAIN, queue=["rev_a"], pacing_loop=True)
assert ENGINE.queued_content(CHAIN, ctx) == "content A"
print("OK: a stale queue entry never asks the narrator for something already read")

# --- declaring the engine with no entries raises rather than going inert -------------
try:
    ENGINE.live({"engine": "triggered_reveal"}, fake_ctx({}))
    raise AssertionError("a triggered_reveal with no entries must not silently do nothing")
except ValueError as exc:
    assert "entries" in str(exc), exc
print("OK: declaring triggered_reveal without `entries` raises rather than going inert")


# =====================================================================================
# 2. Through the real observation pass
# =====================================================================================

def observation_prompt(ctx, diff=None):
    recorder = RecordingLLM(lambda p: dict(diff or EMPTY_DIFF))
    se.call_llm_json = recorder
    se.update_progress_from_turn(ctx, "look around", "narration text")
    return recorder.prompts[-1]


# --- the default story authors none: nothing leaks into either prompt ---------------
plain = se.state_store.load_state("triggeredrevealtest", se.state_store.DEFAULT_STORY_SLUG)
assert not plain["story"]["mechanics"].get("revelations")
prompt = observation_prompt(plain, {**EMPTY_DIFF,
                                    "revelations": {"revealed": ["rev_a"], "eligible": []}})
assert "LIVE TRIGGERS" not in prompt and '"revelations"' not in prompt
assert plain["state"]["plot"]["revelations_revealed"] == {}, \
    "a story that never declared the mechanic must not start recording reveals"
assert "REVEALED MEMORIES" not in se.build_system_prompt(plain)
print("OK: an absent engine leaks nothing into either prompt and ignores a stray field")

# --- declare-to-bind: entries with no `engine` key bind nothing ---------------------
undeclared = se.state_store.load_state("triggeredrevealtest2", se.state_store.DEFAULT_STORY_SLUG)
with_story(undeclared, lambda s: s["mechanics"].__setitem__(
    "revelations", {"entries": CHAIN["entries"]}))
assert mechanics.bound_for(se.state_store.thaw(undeclared["story"]), "revelations") is None
assert "LIVE TRIGGERS" not in observation_prompt(undeclared)
print("OK: a revelations block without an `engine` key binds nothing (declare-to-bind)")

# --- end to end: the chain advances one link per turn, and the cadence closes it ----
chained = se.state_store.load_state("triggeredrevealtest3", se.state_store.DEFAULT_STORY_SLUG)
with_story(chained, lambda s: s["mechanics"].__setitem__("revelations", CHAIN))
se.call_llm_json = CannedResponses([
    {**EMPTY_DIFF, "revelations": {"revealed": ["rev_b"], "eligible": []}},
    {**EMPTY_DIFF, "revelations": {"revealed": ["rev_a"], "eligible": []}},
    {**EMPTY_DIFF, "revelations": {"revealed": ["rev_b"], "eligible": []}},
    {**EMPTY_DIFF, "revelations": {"revealed": ["rev_c"], "eligible": []}},
])
se.update_progress_from_turn(chained, "jump ahead", "narration text")
assert chained["state"]["plot"]["revelations_revealed"] == {}, \
    "rev_b is blocked behind rev_a, so claiming it out of order fires nothing"
for _ in range(3):
    se.update_progress_from_turn(chained, "go on", "narration text")
assert sorted(chained["state"]["plot"]["revelations_revealed"]) == ["rev_a", "rev_b", "rev_c"]

narration = se.build_system_prompt(chained)
assert "content A" in narration and "content C" in narration
assert "the confession" not in narration, "a trigger's wording never reaches the narrator"

recorder = RecordingLLM(lambda p: dict(EMPTY_DIFF))
se.call_llm_json = recorder
se.update_progress_from_turn(chained, "look around", "narration text")
assert "LIVE TRIGGERS" not in recorder.prompts[-1], \
    "with the chain exhausted the engine asks nothing - the cadence, end to end"
print("OK: the chain advances in order, refuses to skip, and stops asking once exhausted")

print("\nALL CHECKS PASSED: test_triggered_reveal")

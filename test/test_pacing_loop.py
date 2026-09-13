"""Regression test for Phase 6 steps 3-5 (docs/PHASE_6_HANDOFF.md §3/§4):

- Step 3: beat_type/intensity and leverage_gained extend update_progress_from_turn's
  schema, conditional on the story authoring mechanics.pacing_loop / mechanics.progression
  - exactly mirroring how mechanics.stats gates stat_changes.
- Step 4: counter arithmetic (feeds/resets) and arming, applied alongside that same diff.
- Step 5: eligibility, the deferral ceiling, and the _section_pacing_directive SECTIONS
  builder that actually injects a fired directive into the narration prompt.

Run directly: python3 test/test_pacing_loop.py
"""
import contextlib
import copy
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import RecordingLLM, CannedResponses, load_story_engine  # noqa: E402

se = load_story_engine()

BASE_DIFF = {
    "subplot_progress": {}, "flags_set": {}, "revelations": {"revealed": [], "eligible": []},
    "inventory": {"gained": [], "used": []}, "social": [], "new_characters": [],
}


def with_story(ctx, mutate):
    story_dict = se.state_store.thaw(ctx["story"])
    mutate(story_dict)
    ctx["story"] = se.state_store.freeze(story_dict)


# stories/example already authors both mechanics.pacing_loop (beats: disquiet/comfort) and
# mechanics.progression (label: footing) - see CLAUDE.md's note that DEFAULT_STORY_SLUG has
# changed before, so derive expectations from the template rather than hardcoding them.
ctx = se.state_store.load_state("pacingtest", se.state_store.DEFAULT_STORY_SLUG)
pacing_cfg = ctx["story"]["mechanics"]["pacing_loop"]
beat_names = list(pacing_cfg["beats"].keys())
assert len(beat_names) >= 2

# --- a story without the module gets no beat fields, no leverage field, and applying a
# turn creates neither pacing.last_beat nor protagonist.leverage ---
def _strip_pacing_module(s):
    s["mechanics"].pop("pacing_loop", None)
    s["mechanics"].pop("progression", None)


with_story(ctx, _strip_pacing_module)
assert "pacing_loop" not in ctx["story"]["mechanics"]
assert "progression" not in ctx["story"]["mechanics"]
recorder = RecordingLLM(lambda p: dict(BASE_DIFF))
se.call_llm_json = recorder
se.update_progress_from_turn(ctx, "look around", "narration text")
assert '"beat_type"' not in recorder.prompts[-1]
assert '"intensity"' not in recorder.prompts[-1]
assert '"leverage_gained"' not in recorder.prompts[-1]
assert "BEAT TYPES" not in recorder.prompts[-1]
assert "last_beat" not in ctx["state"]["pacing"]
assert "leverage" not in ctx["state"]["protagonist"]
print("OK: a story without mechanics.pacing_loop/progression gets no beat/leverage schema "
      "fields, and takes a turn without creating pacing.last_beat or protagonist.leverage")

# --- restore the module (fresh ctx) and confirm the schema/prompt now carries it ---
ctx = se.state_store.load_state("pacingtest2", se.state_store.DEFAULT_STORY_SLUG)
recorder = RecordingLLM(lambda p: dict(BASE_DIFF))
se.call_llm_json = recorder
se.update_progress_from_turn(ctx, "look around", "narration text")
prompt = recorder.prompts[-1]
assert '"beat_type"' in prompt and all(name in prompt for name in beat_names)
assert '"intensity"' in prompt
assert '"leverage_gained"' in prompt
assert "BEAT TYPES" in prompt
assert "CURRENT FOOTING" in prompt  # mechanics.progression.label == "footing"
print("OK: a story with the module adds beat_type/intensity/leverage_gained to the schema, "
      "plus BEAT TYPES definitions and a CURRENT <label> line to the prompt")

# --- an old save predating the module has neither key; taking a turn without the model
# reporting anything pacing-related still doesn't raise or create the keys ---
old_ctx = se.state_store.load_state("pacingtest3", se.state_store.DEFAULT_STORY_SLUG)
assert "last_beat" not in old_ctx["state"]["pacing"]
assert "leverage" not in old_ctx["state"]["protagonist"]
se.call_llm_json = CannedResponses([dict(BASE_DIFF)])
se.update_progress_from_turn(old_ctx, "do nothing pacing-related", "narration text")
print("OK: an old save with no pacing.last_beat/protagonist.leverage key takes a turn "
      "without raising")

# --- a reported beat_type/intensity is stored verbatim (clamped to 1-3) ---
ctx = se.state_store.load_state("pacingtest4", se.state_store.DEFAULT_STORY_SLUG)
beat = beat_names[0]
se.call_llm_json = CannedResponses([
    dict(BASE_DIFF, beat_type=beat, intensity=7),
])
se.update_progress_from_turn(ctx, "press the issue", "narration text")
assert ctx["state"]["pacing"]["last_beat"] == {"type": beat, "intensity": 3}, \
    "intensity should clamp to the 1-3 range"
print("OK: beat_type/intensity land in pacing.last_beat, with intensity clamped to 1-3")

# --- an unrecognized beat_type is ignored rather than stored ---
ctx = se.state_store.load_state("pacingtest5", se.state_store.DEFAULT_STORY_SLUG)
se.call_llm_json = CannedResponses([
    dict(BASE_DIFF, beat_type="not_a_real_beat", intensity=2),
])
se.update_progress_from_turn(ctx, "do something", "narration text")
assert "last_beat" not in ctx["state"]["pacing"]
print("OK: a beat_type outside the template's configured beats is ignored")

# --- a leverage_gained entry is appended with a minted id, spent=False, and the turn it
# was acquired on; a second gain in a later turn continues the numbering ---
ctx = se.state_store.load_state("pacingtest6", se.state_store.DEFAULT_STORY_SLUG)
progression_cfg = ctx["story"]["mechanics"]["progression"]
kind = progression_cfg["kinds"][0]
se.call_llm_json = CannedResponses([
    dict(BASE_DIFF, leverage_gained=[{"kind": kind, "label": "A name she let slip"}]),
])
se.update_progress_from_turn(ctx, "press for a name", "narration text")
leverage = ctx["state"]["protagonist"]["leverage"]
assert len(leverage) == 1
entry = leverage[0]
assert entry["id"] == "lev_001"
assert entry["kind"] == kind
assert entry["label"] == "A name she let slip"
assert entry["acquired_turn"] == ctx["state"]["pacing"]["turn_count"]
assert entry["spent"] is False

se.call_llm_json = CannedResponses([
    dict(BASE_DIFF, leverage_gained=[{"kind": kind, "label": "A second thing"}]),
])
se.update_progress_from_turn(ctx, "press further", "narration text")
assert [e["id"] for e in ctx["state"]["protagonist"]["leverage"]] == ["lev_001", "lev_002"]
print("OK: leverage_gained entries append with sequential lev_NNN ids, spent=False, and "
      "the acquiring turn number")

# --- an unknown kind or a missing label is dropped rather than stored ---
ctx = se.state_store.load_state("pacingtest7", se.state_store.DEFAULT_STORY_SLUG)
se.call_llm_json = CannedResponses([
    dict(BASE_DIFF, leverage_gained=[
        {"kind": "not_a_real_kind", "label": "should be dropped"},
        {"kind": ctx["story"]["mechanics"]["progression"]["kinds"][0], "label": ""},
    ]),
])
se.update_progress_from_turn(ctx, "do something", "narration text")
assert ctx["state"]["protagonist"].get("leverage", []) == []
print("OK: a leverage_gained entry with an unrecognized kind or empty label is dropped")

# ===========================================================================
# Phase 6 step 4 (docs/PHASE_6_HANDOFF.md §4): counter arithmetic and arming.
# example's one authored rule (force_complication) watches "stasis", fed by the "comfort"
# beat and reset by "disquiet" - threshold 5, max_deferrals 3, suppress_when ["just_fired"].
# ===========================================================================
def report_beat(ctx, beat_type, intensity):
    se.call_llm_json = CannedResponses([dict(BASE_DIFF, beat_type=beat_type, intensity=intensity)])
    se.update_progress_from_turn(ctx, "do something", "narration text")


ctx = se.state_store.load_state("pacingtest8", se.state_store.DEFAULT_STORY_SLUG)
report_beat(ctx, "comfort", 3)
assert ctx["state"]["pacing"]["counters"] == {"tension": 0, "stasis": 3}
report_beat(ctx, "disquiet", 2)
assert ctx["state"]["pacing"]["counters"] == {"tension": 2, "stasis": 0}, \
    "disquiet feeds tension and resets stasis to 0"
assert "armed" not in ctx["state"]["pacing"] or ctx["state"]["pacing"]["armed"] == {}
print("OK: a beat's feeds counter accumulates intensity and its resets zero the other counter")

# --- crossing the watched counter's threshold arms the rule; falling short doesn't ---
ctx = se.state_store.load_state("pacingtest9", se.state_store.DEFAULT_STORY_SLUG)
report_beat(ctx, "comfort", 3)
assert ctx["state"]["pacing"].get("armed", {}) == {}, "3 < threshold 5 - not armed yet"
report_beat(ctx, "comfort", 2)
assert ctx["state"]["pacing"]["armed"] == {"force_complication": {"deferrals": 0}}
print("OK: a rule arms once its watched counter reaches (not before) its threshold")

# --- resetting the watched counter clears the armed entry ---
report_beat(ctx, "disquiet", 1)
assert ctx["state"]["pacing"]["counters"]["stasis"] == 0
assert "force_complication" not in ctx["state"]["pacing"]["armed"]
print("OK: resetting an armed rule's watched counter clears its armed entry")

# ===========================================================================
# Phase 6 step 5 (docs/PHASE_6_HANDOFF.md §4): eligibility, deferral ceiling, and the
# SECTIONS directive builder (_section_pacing_directive).
# ===========================================================================

# --- an armed, eligible rule injects its full directive into the narration prompt, with
# {counter_value} and {unspent_leverage} interpolated ---
ctx = se.state_store.load_state("pacingtest10", se.state_store.DEFAULT_STORY_SLUG)
report_beat(ctx, "comfort", 3)
report_beat(ctx, "comfort", 2)
assert ctx["state"]["pacing"]["armed"] == {"force_complication": {"deferrals": 0}}
kind = ctx["story"]["mechanics"]["progression"]["kinds"][0]
se.call_llm_json = CannedResponses([
    dict(BASE_DIFF, leverage_gained=[{"kind": kind, "label": "The odd calendar date"}]),
])
se.update_progress_from_turn(ctx, "note the calendar", "narration text")
prompt = se.build_system_prompt(ctx)
assert "PACING DIRECTIVE - THE SURFACE MUST CRACK" in prompt
assert "pleasant for 5 accumulated scenes" in prompt
assert "The odd calendar date" in prompt
assert ctx["state"]["pacing"]["last_fired_rule"] == "force_complication"
assert ctx["state"]["pacing"]["armed"]["force_complication"]["deferrals"] == 0
print("OK: an armed, eligible rule injects its full directive, with counter_value and "
      "unspent_leverage interpolated")

# --- just_fired suppresses the very next turn (deferring, not un-arming), then expires ---
prompt2 = se.build_system_prompt(ctx)
assert "PACING DIRECTIVE" not in prompt2, "just_fired should suppress the turn right after firing"
assert ctx["state"]["pacing"]["armed"]["force_complication"]["deferrals"] == 1
assert "last_fired_rule" not in ctx["state"]["pacing"]
prompt3 = se.build_system_prompt(ctx)
assert "PACING DIRECTIVE - THE SURFACE MUST CRACK" in prompt3, \
    "just_fired only suppresses for one turn - it should fire again now"
assert ctx["state"]["pacing"]["armed"]["force_complication"]["deferrals"] == 0
print("OK: just_fired suppresses exactly the one turn immediately after firing, not longer")

# ===========================================================================
# Deferral ceiling: use a suppression predicate that doesn't self-expire (threat_present)
# to verify deferrals accumulate to max_deferrals and then the reduced directive fires.
# ===========================================================================
ctx = se.state_store.load_state("pacingtest11", se.state_store.DEFAULT_STORY_SLUG)
with_story(ctx, lambda s: s["mechanics"]["pacing_loop"]["rules"][0].update(
    suppress_when=["threat_present"], max_deferrals=3,
))
report_beat(ctx, "comfort", 3)
report_beat(ctx, "comfort", 2)
ctx["state"]["scene"]["threat_present"] = True
assert ctx["state"]["pacing"]["armed"] == {"force_complication": {"deferrals": 0}}

for expected_deferrals in (1, 2, 3):
    prompt = se.build_system_prompt(ctx)
    assert "PACING DIRECTIVE" not in prompt, f"should still be suppressed at deferral {expected_deferrals}"
    assert ctx["state"]["pacing"]["armed"]["force_complication"]["deferrals"] == expected_deferrals

ceiling_prompt = se.build_system_prompt(ctx)
assert "PACING DIRECTIVE - ONE DETAIL OUT OF PLACE" in ceiling_prompt, \
    "deferrals reaching max_deferrals should fire the reduced directive despite suppression"
assert "deferred 3 times" in ceiling_prompt
assert ctx["state"]["pacing"]["armed"]["force_complication"]["deferrals"] == 0, \
    "firing (even reduced) resets the deferral count - the guaranteed-floor cycle restarts"
print("OK: sustained suppression accumulates deferrals to the ceiling, then the reduced "
      "directive fires despite still being suppressed, and the cycle resets")

# ===========================================================================
# Act-scaled thresholds (spec §13): exact act number overrides the base threshold; "finale"
# (when the current act is a generated finale) disables the rule entirely.
# ===========================================================================
ctx = se.state_store.load_state("pacingtest12", se.state_store.DEFAULT_STORY_SLUG)
assert ctx["state"]["plot"]["current_act"] == 1
with_story(ctx, lambda s: s["mechanics"]["pacing_loop"]["rules"][0].update(
    threshold_by_act={"1": 1, "finale": None},
))
report_beat(ctx, "comfort", 1)
assert ctx["state"]["pacing"]["armed"] == {"force_complication": {"deferrals": 0}}, \
    "act 1's threshold_by_act override (1) should arm at counter value 1, not the base 5"
print("OK: threshold_by_act's exact-act-number override takes priority over the base threshold")

ctx["state"]["plot"]["generated_acts"].append({
    "act_number": 99, "title": "Finale", "description": "", "completion_signals": [],
    "completed": False, "optional": False, "is_finale": True,
})
ctx["state"]["plot"]["current_act"] = 99
prompt = se.build_system_prompt(ctx)
assert "PACING DIRECTIVE" not in prompt, "\"finale\": null should disable the rule in the finale act"
print("OK: threshold_by_act's \"finale\": null disables the rule once the current act is a finale")

# ===========================================================================
# v1 scope: a template declaring more than one rule logs a warning and uses only the first.
# ===========================================================================
ctx = se.state_store.load_state("pacingtest13", se.state_store.DEFAULT_STORY_SLUG)


def add_second_rule(s):
    rules = s["mechanics"]["pacing_loop"]["rules"]
    second = copy.deepcopy(rules[0])
    second["id"] = "second_rule"
    rules.append(second)


with_story(ctx, add_second_rule)
stderr = io.StringIO()
with contextlib.redirect_stderr(stderr):
    rule = se._pacing_rule(ctx["story"]["mechanics"]["pacing_loop"])
assert rule["id"] == "force_complication"
assert "WARNING" in stderr.getvalue() and "2 rules" in stderr.getvalue()
print("OK: a template declaring more than one rule logs a warning and uses only the first")

# ===========================================================================
# Gap 1 (docs/PHASE_6_HANDOFF.md §1, spec §7): leverage is *spent*, not just gained, and
# the ledger is bounded by LEVERAGE_LIMIT with spent entries evicted oldest-first.
# ===========================================================================
ctx = se.state_store.load_state("pacingtest14", se.state_store.DEFAULT_STORY_SLUG)
kind = ctx["story"]["mechanics"]["progression"]["kinds"][0]
se.call_llm_json = CannedResponses([
    dict(BASE_DIFF, leverage_gained=[
        {"kind": kind, "label": "A name she let slip"},
        {"kind": kind, "label": "The back-door key"},
    ]),
])
se.update_progress_from_turn(ctx, "press for a name", "narration text")
recorder = RecordingLLM(lambda p: dict(BASE_DIFF, leverage_spent=["The back-door key"]))
se.call_llm_json = recorder
se.update_progress_from_turn(ctx, "use the key", "narration text")
assert '"leverage_spent"' in recorder.prompts[-1]
assert "The back-door key" in recorder.prompts[-1], \
    "the CURRENT <LABEL> line is what leverage_spent matches against, like items_lost"
leverage = ctx["state"]["protagonist"]["leverage"]
assert [(e["label"], e["spent"]) for e in leverage] == [
    ("A name she let slip", False), ("The back-door key", True),
], "a spent entry is marked, not removed"
assert leverage[1]["spent_turn"] == ctx["state"]["pacing"]["turn_count"]
print("OK: leverage_spent marks a matching unspent entry spent (retained, not pruned) and "
      "records the turn it was spent on")

# --- a spent entry stops being offered to the directive and to leverage_gained's dedup ---
ctx["state"]["pacing"]["armed"] = {"force_complication": {"deferrals": 0}}
ctx["state"]["pacing"]["counters"] = {"tension": 0, "stasis": 5}
prompt = se.build_system_prompt(ctx)
assert "A name she let slip" in prompt
assert "The back-door key" not in prompt, \
    "{unspent_leverage} must not point the directive at something already cashed in"
print("OK: a spent entry drops out of {unspent_leverage} while the unspent one remains")

# --- a label that doesn't match, or matches only an already-spent entry, is a no-op ---
se.call_llm_json = CannedResponses([
    dict(BASE_DIFF, leverage_spent=["The back-door key", "something never gained"]),
])
se.update_progress_from_turn(ctx, "try again", "narration text")
assert [e["spent"] for e in ctx["state"]["protagonist"]["leverage"]] == [False, True]
print("OK: a leverage_spent label matching nothing unspent is ignored rather than raising")

# --- LEVERAGE_LIMIT evicts spent entries oldest-first, and never an unspent one ---
ctx = se.state_store.load_state("pacingtest15", se.state_store.DEFAULT_STORY_SLUG)
limit = se.LEVERAGE_LIMIT
ledger = ctx["state"]["protagonist"]["leverage"] = [
    {"id": f"lev_{i:03d}", "kind": kind, "label": f"entry {i}", "acquired_turn": i,
     "spent": i % 2 == 0}
    for i in range(1, limit + 4)
]
spent_before = [e["id"] for e in ledger if e["spent"]]
se.call_llm_json = CannedResponses([dict(BASE_DIFF)])
se.update_progress_from_turn(ctx, "do something", "narration text")
ledger = ctx["state"]["protagonist"]["leverage"]
assert len(ledger) == limit
surviving = {e["id"] for e in ledger}
assert all(e["id"] in surviving for e in ledger if not e["spent"])
assert [i for i in spent_before if i not in surviving] == spent_before[:3], \
    "the three oldest SPENT entries are the ones evicted"
print(f"OK: over LEVERAGE_LIMIT ({limit}) the oldest spent entries are evicted first")

# --- unspent entries alone over the limit are allowed to overflow rather than be dropped ---
ctx = se.state_store.load_state("pacingtest16", se.state_store.DEFAULT_STORY_SLUG)
ctx["state"]["protagonist"]["leverage"] = [
    {"id": f"lev_{i:03d}", "kind": kind, "label": f"live {i}", "acquired_turn": i,
     "spent": False}
    for i in range(1, limit + 3)
]
se.call_llm_json = CannedResponses([dict(BASE_DIFF)])
se.update_progress_from_turn(ctx, "do something", "narration text")
assert len(ctx["state"]["protagonist"]["leverage"]) == limit + 2, \
    "an all-unspent ledger overflows rather than losing a live asset (spec §7)"
print("OK: a ledger of only unspent entries is allowed to exceed the limit rather than "
      "dropping a live asset")

# ===========================================================================
# Gap 2 (docs/PHASE_6_HANDOFF.md §1, spec §12): reveal placement. `example` authors no
# mechanics.revelations, so this uses a story patched to carry one - which also checks the
# field is gated on the story actually having unrevealed fragments.
# ===========================================================================
ctx = se.state_store.load_state("pacingtest17", se.state_store.DEFAULT_STORY_SLUG)
assert not ctx["story"]["mechanics"].get("revelations")
recorder = RecordingLLM(lambda p: dict(BASE_DIFF))
se.call_llm_json = recorder
se.update_progress_from_turn(ctx, "look around", "narration text")
assert '"eligible"' not in recorder.prompts[-1], \
    "a story with no unrevealed fragments is never asked to queue one"
assert "reveal_queue" not in ctx["state"]["pacing"] or ctx["state"]["pacing"]["reveal_queue"] == []
print("OK: a story authoring no revelations is never asked to queue a reveal")


def add_revelation(s):
    # Phase 4 wrapped the bare list so the block can carry `engine` (ENGINE_V2_SPEC §8.1);
    # the placement mechanics below are the triggered_reveal engine's now, but everything
    # this file asserts about them is unchanged.
    s["mechanics"]["revelations"] = {
        "engine": "triggered_reveal",
        "entries": [
            {"id": "frag_0001", "trigger": "The player reads the ledger.",
             "content": "The ledger's last entry is dated a year after the fire."},
        ],
    }


ctx = se.state_store.load_state("pacingtest18", se.state_store.DEFAULT_STORY_SLUG)
with_story(ctx, add_revelation)
recorder = RecordingLLM(lambda p: dict(
    BASE_DIFF, revelations={"revealed": [], "eligible": ["frag_0001"]}))
se.call_llm_json = recorder
se.update_progress_from_turn(ctx, "read the ledger", "narration text")
assert '"eligible"' in recorder.prompts[-1]
assert ctx["state"]["pacing"]["reveal_queue"] == ["frag_0001"]
assert "frag_0001" not in ctx["state"]["plot"]["revelations_revealed"], \
    "queueing is not revealing - the fragment stays unrevealed until the narration writes it"
print("OK: an eligible-but-unwritten reveal appends to pacing.reveal_queue without "
      "marking the fragment revealed")

# --- re-reporting the same id doesn't duplicate it; an unknown id is dropped ---
se.call_llm_json = CannedResponses([
    dict(BASE_DIFF, revelations={"revealed": [], "eligible": ["frag_0001", "frag_9999"]}),
])
se.update_progress_from_turn(ctx, "read it again", "narration text")
assert ctx["state"]["pacing"]["reveal_queue"] == ["frag_0001"]
print("OK: the reveal queue de-duplicates and ignores ids the template doesn't define")

# --- the queued reveal's CONTENT (never its id or trigger) reaches the directive ---
with_story(ctx, lambda s: s["mechanics"]["pacing_loop"]["rules"][0].update(
    directive="PACING DIRECTIVE - PLACE IT\nQueued: {queued_reveal}",
))
ctx["state"]["pacing"]["armed"] = {"force_complication": {"deferrals": 0}}
ctx["state"]["pacing"]["counters"] = {"tension": 0, "stasis": 5}
prompt = se.build_system_prompt(ctx)
assert "The ledger's last entry is dated a year after the fire." in prompt
assert "frag_0001" not in prompt and "The player reads the ledger." not in prompt, \
    "only the reveal's content is interpolated - never its id or authored trigger"
print("OK: a fired directive interpolates the queued reveal's content, not its bookkeeping")

# --- consumption is on CONFIRMED reveal, not on firing: still queued after the directive
# fired, and dropped only once the observation pass reports it as revealed ---
assert ctx["state"]["pacing"]["reveal_queue"] == ["frag_0001"], \
    "firing the directive is an instruction, not a guarantee - the entry stays queued"
se.call_llm_json = CannedResponses([
    dict(BASE_DIFF, revelations={"revealed": ["frag_0001"], "eligible": []}),
])
se.update_progress_from_turn(ctx, "let it land", "narration text")
assert "frag_0001" in ctx["state"]["plot"]["revelations_revealed"]
assert ctx["state"]["pacing"]["reveal_queue"] == []
print("OK: a queued reveal survives a firing the narration ignored, and clears only once "
      "the state pass confirms it was actually revealed")

# --- an already-revealed id stranded in the queue never reaches the directive ---
ctx["state"]["pacing"]["reveal_queue"] = ["frag_0001"]
ctx["state"]["pacing"]["armed"] = {"force_complication": {"deferrals": 0}}
ctx["state"]["pacing"]["counters"] = {"tension": 0, "stasis": 5}
ctx["state"]["pacing"].pop("last_fired_rule", None)  # clear just_fired from the firing above
prompt = se.build_system_prompt(ctx)
assert "PACING DIRECTIVE - PLACE IT" in prompt
assert "Queued: none queued" in prompt
print("OK: a stale queue entry for an already-revealed fragment renders as \"none queued\"")

print("\nALL CHECKS PASSED: test_pacing_loop")

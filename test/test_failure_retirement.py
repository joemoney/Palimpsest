"""D5 / D6: `failure_conditions` (the `triggered_ending` engine) and the player's end-story command
are retired. A failure is a `kind: "terminal"` entry under `mechanics.endings`; a story ends only
through `mechanics.endings` (a committed destination, a forced commit or a confirmed terminal).

Covers: the loader refuses a template still carrying the old block, loudly and with the way out;
the engine is gone from the registry; a failure that is an *event* (the old prose `trigger`) is
migrated as a declared flag whose `detect` is that text, and fires through the real endings path;
the finale prompt no longer claims the player asked for it; and there is no way to end a story by
typing a phrase.

Run directly: python3 test/test_failure_retirement.py
"""
import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import RecordingLLM, load_story_engine  # noqa: E402

se = load_story_engine()
import conditions  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


def ctx_for(raw):
    return {"story": se.state_store.freeze(raw), "state": se.state_store.new_save_state(raw, "x")}


# --- the loader refuses the retired block, and says what to do -------------------------------------
for shape in ({"engine": "triggered_ending", "conditions": [{"id": "f", "trigger": "t", "ending_prompt": "p"}]},
              [{"id": "f", "trigger": "t", "ending_prompt": "p"}]):
    story = copy.deepcopy(fixture("courtroom.json"))
    story["mechanics"]["failure_conditions"] = shape
    try:
        se.mechanics.validate(story)
    except ValueError as e:
        assert "retired" in str(e) and "kind" in str(e) and "terminal" in str(e) and "ready_when" in str(e), e
    else:
        raise AssertionError("a template with mechanics.failure_conditions must be refused")
print("OK: a template still carrying mechanics.failure_conditions is refused, naming the replacement (both shapes)")

assert ("failure_conditions", "triggered_ending") not in se.mechanics.registered_engines()
assert "failure_conditions" not in {slot for slot, _ in se.mechanics.registered_engines()}
try:
    import mechanics.failure  # noqa: F401
except ImportError:
    pass
else:
    raise AssertionError("backend/mechanics/failure.py should be deleted")
print("OK: the triggered_ending engine is gone from the registry")

# --- no player command ends the story ------------------------------------------------------------------
for retired in ("END_STORY_PHRASES", "is_end_story_command", "handle_end_story_request", "_apply_failure_effect"):
    assert not hasattr(se, retired), retired
print("OK: END_STORY_PHRASES, handle_end_story_request and the failure effect handler are deleted")

# --- the migrated fixtures: a prose event is a declared flag, and fires through the endings path ----------
courtroom = fixture("courtroom.json")
declared = {f["id"]: f["detect"] for f in courtroom["mechanics"]["flags"]["declared"]}
assert declared["contempt_third"] == "The protagonist defies a direct ruling from the bench for the third time"
terminals = {e["id"]: e for e in courtroom["mechanics"]["endings"]["entries"] if e["kind"] == "terminal"}
assert terminals["fail_contempt"]["ready_when"] == {"flag": "contempt_third"}
assert any(e["kind"] == "destination" and "viable_while" not in e for e in courtroom["mechanics"]["endings"]["entries"]), \
    "a catch-all destination, or the endings block would be refused at load"
se.mechanics.validate(courtroom)  # the migrated fixture passes the loader's own checks

EMPTY = {"subplot_beats": {}, "flags_set": {}, "revelations": {"revealed": [], "eligible": []},
         "inventory": {"gained": [], "used": []}, "new_characters": [],
         "scene_update": {"location": "", "summary": "unchanged", "present_npcs": []}}


def turn(ctx, flags):
    recorder = RecordingLLM(lambda p: {**copy.deepcopy(EMPTY), "flags_set": {f: {"value": True} for f in flags}})
    original, se.call_llm_json = se.call_llm_json, recorder
    try:
        se.update_progress_from_turn(ctx, "I object", "The scene unfolds.")
        return recorder.prompts[-1]
    finally:
        se.call_llm_json = original


ctx = ctx_for(courtroom)
prompt = turn(ctx, [])
assert "contempt_third: The protagonist defies a direct ruling" in prompt, "the old trigger text reaches the update pass as a flag's detect"
judge = RecordingLLM(lambda p: {"confirmed": True})
se.call_llm_json = judge
ctx["state"]["pacing"]["turn_count"] = 5
assert se.check_ending_funnel(ctx) is None, "nothing tripped yet"
turn_ctx = ctx
turn(turn_ctx, ["contempt_third"])
se.call_llm_json = judge
fired = se.check_ending_funnel(ctx)
assert fired and fired["id"] == "fail_contempt", fired
endgame = ctx["state"]["plot"]["endgame"]
assert endgame["requested"] and endgame["cause"] == "terminal" and endgame["final_arc"]["title"] == "Removed From the Record"
print("OK: a prose failure, migrated to a declared flag, fires its terminal and enters the finale (cause: terminal)")

# the compound case: rested without ever impeaching (fail_rest) is a flag AND NOT a flag
ctx = ctx_for(courtroom)
ctx["state"]["pacing"]["turn_count"] = 5
turn(ctx, ["defence_rested", "witness_impeached"])
se.call_llm_json = judge
assert se.check_ending_funnel(ctx) is None, "resting after impeaching a witness is not the failure"
ctx = ctx_for(courtroom)
ctx["state"]["pacing"]["turn_count"] = 5
turn(ctx, ["defence_rested"])
se.call_llm_json = judge
fired = se.check_ending_funnel(ctx)
assert fired and fired["id"] == "fail_rest", fired
print("OK: a compound failure (rested, and never impeached) is a flag and a negated flag")

# a stat failure keeps a judge's confirmation: survival's fail_cold has criteria
survival = fixture("survival.json")
se.mechanics.validate(survival)
ctx = ctx_for(survival)
ctx["state"]["protagonist"]["stats"]["warmth"] = -10
ctx["state"]["pacing"]["turn_count"] = 5
asked = RecordingLLM(lambda p: {"confirmed": False, "confirm": False})
se.call_llm_json = asked
se.check_ending_funnel(ctx)
assert len(asked.prompts) == 1 and "exposure" in asked.prompts[0], "a terminal with criteria asks the judge"
print("OK: a stat failure with criteria still goes to the judge")

# --- the finale prompt no longer says the player asked -------------------------------------------------------
ctx = ctx_for(courtroom)
se._begin_endgame(ctx, {"title": "T", "description": "D"}, cause="forced")
text = se._section_pacing_or_endgame(ctx)
assert "The story has reached its ending." in text and "player has asked" not in text, text
print("OK: the ENDGAME prompt says the story reached its ending, never that the player asked")

print("\nALL CHECKS PASSED: test_failure_retirement")

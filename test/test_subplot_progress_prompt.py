"""The `subplots` / `weighted_threads` engine - engine v2 phase 4, port 5.

This file was CR-08's regression test: the observation prompt used to send active subplots
as bare `{id: title}`, giving the scoring model no sense of where a thread stood - it could
not tell "this beat should finish the thread" from "this nudges it" - so progress and
threshold were added to the line. CR-08's *concern* is still live and still tested here;
its *mechanism* is not, because phase 4 took the arithmetic off the model (ENGINE_V2_SPEC
§2.1). The model now classifies the beat and sees a band rather than a running total, which
answers CR-08 without inviting the model to calculate again.

Run directly: python3 test/test_subplot_progress_prompt.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import CannedResponses, RecordingLLM, load_story_engine  # noqa: E402

se = load_story_engine()
mechanics = se.mechanics
ENGINE = mechanics.threads.ENGINE
W = mechanics.threads.DEFAULT_WEIGHTS

EMPTY_DIFF = {
    "subplot_beats": {}, "flags_set": {}, "revelations": {"revealed": [], "eligible": []},
    "inventory": {"gained": [], "used": []}, "social": [], "new_characters": [],
}
CFG = {"engine": "weighted_threads"}


def fake_ctx(cfg, threads):
    """threads: {id: (progress, threshold, active)}."""
    return {
        "story": {"plot": {"subplots": {}}, "mechanics": {"subplots": cfg}},
        "state": {"plot": {"subplots": {
            sid: {"title": sid, "description": "d", "progress": p,
                  "completion_threshold": t, "active": a}
            for sid, (p, t, a) in threads.items()}},
            "pacing": {"turn_count": 0}},
    }


def beat(sid, kind):
    return {"type": "subplot_beat", "id": sid, "beat": kind}


def apply(cfg, ctx, events):
    mechanics.apply_effects(ctx, ENGINE.resolve(cfg, ctx, events, []))
    return {sid: sp["progress"] for sid, sp in ctx["state"]["plot"]["subplots"].items()}


# =====================================================================================
# 1. Pure engine rules - no stubs
# =====================================================================================

# --- the engine prices the classification; the model never supplies a number ---------
ctx = fake_ctx(CFG, {"s1": (0, 100, True)})
assert apply(CFG, ctx, [beat("s1", "touched")]) == {"s1": W["touched"]}
ctx = fake_ctx(CFG, {"s1": (0, 100, True)})
assert apply(CFG, ctx, [beat("s1", "advanced")]) == {"s1": W["advanced"]}
ctx = fake_ctx(CFG, {"s1": (0, 100, True)})
assert apply(CFG, ctx, [beat("s1", "decisive")]) == {"s1": W["decisive"]}
print("OK: each classification is priced from the engine's ladder, not by the model")

# --- `resolved` completes the thread whatever its threshold and wherever it stood -----
ctx = fake_ctx(CFG, {"s1": (0, 100, True), "s2": (30, 250, True)})
assert apply(CFG, ctx, [beat("s1", "resolved"), beat("s2", "resolved")]) == {"s1": 100, "s2": 250}
print("OK: `resolved` completes a thread at any threshold, from any starting progress")

# --- weights are absolute, so a multi_act thread genuinely takes more beats -----------
single = fake_ctx(CFG, {"s1": (0, 100, True)})
multi = fake_ctx(CFG, {"s1": (0, 250, True)})
assert apply(CFG, single, [beat("s1", "decisive")])["s1"] == W["decisive"]
assert apply(CFG, multi, [beat("s1", "decisive")])["s1"] == W["decisive"], \
    "scaling the weight to the threshold would undo the one lever `span: multi_act` has"
print("OK: a decisive beat is worth the same absolutely, so a multi_act thread takes longer")

# --- progress clamps at the threshold rather than overshooting -----------------------
ctx = fake_ctx(CFG, {"s1": (90, 100, True)})
assert apply(CFG, ctx, [beat("s1", "decisive")]) == {"s1": 100}
print("OK: progress clamps at completion_threshold instead of overshooting")

# --- several beats on one thread in a turn accumulate ---------------------------------
ctx = fake_ctx(CFG, {"s1": (0, 100, True)})
effects = ENGINE.resolve(CFG, ctx, [beat("s1", "advanced"), beat("s1", "advanced")], [])
assert [e.payload["value"] for e in effects] == [W["advanced"], 2 * W["advanced"]]
print("OK: two beats on one thread in a turn accumulate rather than both pricing from 0")

# --- a story can override the ladder --------------------------------------------------
brisk = {"engine": "weighted_threads", "weights": {"advanced": 50}}
ctx = fake_ctx(brisk, {"s1": (0, 100, True)})
assert apply(brisk, ctx, [beat("s1", "advanced")]) == {"s1": 50}
assert ENGINE.weights(brisk)["touched"] == W["touched"], "an override merges, it does not replace"
print("OK: an authored `weights` block overrides one rung and leaves the rest alone")

# --- events(): a bare number is dropped, not honoured ---------------------------------
ctx = fake_ctx(CFG, {"s1": (0, 100, True), "s2": (0, 100, False)})
assert ENGINE.events(CFG, ctx, {"subplot_beats": {"s1": 40}}) == [], \
    "accepting an integer would quietly restore the model's arithmetic"
assert ENGINE.events(CFG, ctx, {"subplot_beats": {"s1": "nudged"}}) == [], "vocabulary is closed"
assert ENGINE.events(CFG, ctx, {"subplot_beats": {"s2": "advanced"}}) == [], "s2 is not active"
assert ENGINE.events(CFG, ctx, {"subplot_beats": {"s_unknown": "advanced"}}) == []
assert ENGINE.events(CFG, ctx, {"subplot_beats": {"s1": "advanced"}}) == [
    {"type": "subplot_beat", "id": "s1", "beat": "advanced"}]
print("OK: a bare number, an invented word, an inactive thread and an unknown id are all dropped")

# --- CR-08's concern, answered without a running total --------------------------------
field = ENGINE.observations(CFG, fake_ctx(CFG, {"s1": (10, 100, True), "s2": (50, 100, True),
                                                "s3": (90, 100, True)}))[0]
assert "(just begun)" in field.context and "(under way)" in field.context
assert "(close to resolution)" in field.context
assert "10/100" not in field.context and "90" not in field.context, \
    "a model that must not add should not be shown a running total to add to"
print("OK: a thread's position reaches the model as a band, never as a running total")

# --- the cadence, which is also the P-2 fix: no active thread, no field ---------------
assert ENGINE.observations(CFG, fake_ctx(CFG, {})) is None
assert ENGINE.observations(CFG, fake_ctx(CFG, {"s1": (0, 100, False)})) is None
assert ENGINE.observations(CFG, fake_ctx(CFG, {"s1": (0, 100, True)})) is not None
print("OK: a story with no active thread is never asked about subplots")


# =====================================================================================
# 2. Through the real observation pass
# =====================================================================================

ctx = se.state_store.load_state("subplotprogresstest", se.state_store.DEFAULT_STORY_SLUG)
ctx["state"]["plot"]["subplots"]["subplot_001"]["progress"] = 40

recorder = RecordingLLM(lambda p: dict(EMPTY_DIFF))
se.call_llm_json = recorder
se.update_progress_from_turn(ctx, "chat with the innkeeper", "narration text")
prompt = recorder.prompts[-1]

assert "subplot_001: Settling In - Get to know the Harborlight Inn" in prompt, prompt
assert "(under way)" in prompt, prompt
assert "[40/100]" not in prompt
assert '"subplot_beats"' in prompt and "touched, advanced, decisive, resolved" in prompt
assert '"subplot_progress"' not in prompt, "the v2 field is gone, not merely unused"
print("OK: an active thread's description and band reach the prompt, and the field is a "
      "classification rather than a number")

# --- end to end: a classification is priced and completion still fires ---------------
se.call_llm_json = CannedResponses([
    {**EMPTY_DIFF, "subplot_beats": {"subplot_001": "advanced"}},
    {**EMPTY_DIFF, "subplot_beats": {"subplot_001": "resolved"}},
    # subplot_001 completing triggers a replacement via generate_new_subplot
    {"title": "Replacement", "description": "d", "priority": "medium", "ties_to_main_plot": "t"},
])
se.update_progress_from_turn(ctx, "make real progress", "narration text")
assert se._subplot_view(ctx, "subplot_001")["progress"] == 40 + W["advanced"]
se.update_progress_from_turn(ctx, "finish the thread", "narration text")
assert se._subplot_view(ctx, "subplot_001")["progress"] == 100
assert se.check_subplot_status(ctx)["completed"] == ["subplot_001"], \
    "completion detection deliberately stayed in story_engine - it must still see the result"
print("OK: a priced beat reaches the save, and check_subplot_status still completes the thread")

# --- a story that authors threads but declares no engine: the warning, and inertness ---
import contextlib  # noqa: E402
import io as _io  # noqa: E402

undeclared = se.state_store.load_state("subplotprogresstest2", se.state_store.DEFAULT_STORY_SLUG)
story_dict = se.state_store.thaw(undeclared["story"])
story_dict["mechanics"].pop("subplots", None)
undeclared["story"] = se.state_store.freeze(story_dict)
recorder = RecordingLLM(lambda p: dict(EMPTY_DIFF, subplot_beats={"subplot_001": "resolved"}))
se.call_llm_json = recorder
se.update_progress_from_turn(undeclared, "look around", "narration text")
assert '"subplot_beats"' not in recorder.prompts[-1]
assert se._subplot_view(undeclared, "subplot_001")["progress"] == 0, \
    "declare-to-bind: no engine, no progress, and a stray field is ignored"
captured = _io.StringIO()
with contextlib.redirect_stdout(captured):
    mechanics.validate(se.state_store.thaw(undeclared["story"]))
assert "weighted_threads" in captured.getvalue(), captured.getvalue()
print("OK: a story authoring threads with no declared engine is inert, and says so")

print("\nALL CHECKS PASSED: test_subplot_progress_prompt")

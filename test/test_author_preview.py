"""S4 Preview tab: `author_preview` builds the narrator and state-update prompts through the real
builders for a story plus a sample state. Covers the S4 gate (an empty-sample preview is byte for
byte the prompt a fresh save assembles, for each genre fixture), the sample reaching the prompt,
engines this build lacks being left out and named, the state-update capture leaving no trace on
`story_engine`, and a story that cannot seed a state reporting an error instead of raising.

Run directly: python3 test/test_author_preview.py
"""
import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import load_story_engine  # noqa: E402

se = load_story_engine()
import author_preview  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


# --- the S4 gate: not a byte differs from a real turn's prompt --------------------------------
for name in ("regency.json", "courtroom.json", "survival.json"):
    raw = fixture(name)
    real = se.build_system_prompt({"story": se.state_store.freeze(raw),
                                   "state": se.state_store.new_save_state(raw, "x")})
    got = author_preview.preview(raw, {})
    assert "error" not in got, (name, got)
    assert got["narrator"]["prompt"] == real, name
    assert got["narrator"]["matches"] is True, name
    assert "\n\n".join(s["text"] for s in got["narrator"]["sections"]) in real, name
    assert got["left_out"] == [], (name, got["left_out"])
print("OK: with no sample state the preview is byte for byte a fresh save's narrator prompt (3 fixtures)")

# --- the sample state reaches the prompt ------------------------------------------------------
survival = fixture("survival.json")
axis = next(iter(survival["mechanics"]["stats"].get("axes") or survival["protagonist"]["stats"]))
base = author_preview.preview(survival, {})
moved = author_preview.preview(survival, {"stats": {axis: 3}, "turn": 9, "flags": ["sample_flag"]})
assert moved["narrator"]["prompt"] != base["narrator"]["prompt"]
assert f'"{axis}": 3' in moved["state_update"]["prompt"] or f"{axis.upper()} 3" in moved["narrator"]["prompt"] \
    or f"{axis}: 3" in moved["narrator"]["prompt"], "the sampled stat value is in a prompt"
assert "sample_flag" in moved["narrator"]["prompt"] + moved["state_update"]["prompt"]
print("OK: the sample state's stats and flags change what is sent")

reg = fixture("regency.json")
frag = reg["mechanics"]["revelations"]["entries"][0]["id"] if isinstance(reg["mechanics"]["revelations"], dict) \
    and reg["mechanics"]["revelations"].get("entries") else None
if frag:
    revealed = author_preview.preview(reg, {"revealed": [frag]})
    assert "REVEALED SO FAR" in revealed["narrator"]["prompt"]
    assert "REVEALED SO FAR" not in author_preview.preview(reg, {})["narrator"]["prompt"]
    print("OK: a revealed fragment reaches the narrator, and only then")

# --- the state-update prompt is the real one, captured with no live call ----------------------
before = se.call_llm_json
su = author_preview.preview(survival, {})["state_update"]
assert se.call_llm_json is before, "the capture must restore story_engine.call_llm_json"
assert '"flags_set"' in su["prompt"] and '"scene_update"' in su["prompt"] and su["tokens"] > 0
assert author_preview.SAMPLE_ACTION in su["prompt"] and author_preview.SAMPLE_NARRATION in su["prompt"]
print("OK: the state-update prompt is captured from update_progress_from_turn, and nothing is left patched")

# --- previewing never changes anything, and is repeatable -------------------------------------
frozen = copy.deepcopy(survival)
first = author_preview.preview(survival, {"turn": 30})
second = author_preview.preview(survival, {"turn": 30})
assert survival == frozen, "the template is not touched"
assert first["narrator"]["prompt"] == second["narrator"]["prompt"], "a section's side effect must not leak between runs"
print("OK: previewing is repeatable and leaves the template alone")

# --- an engine this build lacks is left out and named, not approximated -----------------------
lacking = copy.deepcopy(survival)
lacking["mechanics"]["weather"] = {"engine": "not_yet_built"}
lacking["derived"] = [{"when": {"creation": {"a": "b"}}, "set": {"who": "them"}}]
got = author_preview.preview(lacking, {})
assert ("weather", "not_yet_built") in got["left_out"], got["left_out"]
assert not any("derived" in n for n in got["notes"]), "derived is built; it is no longer reported as left out"
print("OK: an unbuilt engine is named as left out; CR-04 derived values no longer are")

# --- a story that cannot seed a state reports it ----------------------------------------------
broken = copy.deepcopy(survival)
del broken["plot"]["main_thread"]
got = author_preview.preview(broken, {})
assert "error" in got and "Could not build the preview" in got["error"], got
print("OK: a story with no main thread reports an error instead of raising")

# CR-03/§5.4's budget guard: revealing every fragment at once (a live save found this exact
# case, 2026-09-29 - see backend/mechanics/reveal.py) must stay inside the engine's own budget
# by trimming to the most recent, never by raising - volume alone is not a bug.
mc = se.state_store.load_template_raw("the_missing_core")
every = [e["id"] for e in mc["mechanics"]["revelations"]["entries"]]
got = author_preview.preview(mc, {"revealed": every})
assert "error" not in got, got
assert "revelations.revealed" in got["narrator"]["prompt"] or "REVEALED SO FAR" in got["narrator"]["prompt"]
print("OK: revealing every fragment at once still fits the engine's own prompt budget (no crash from volume alone)")

# a single fragment too long to ever fit is a real authoring mistake, and still raises - the
# guard this budget exists for (a §5.4 engine bug, not content volume) still works.
overlong = copy.deepcopy(mc)
overlong["mechanics"]["revelations"]["entries"][0]["content"] = "x" * 3000
got = author_preview.preview(overlong, {"revealed": [overlong["mechanics"]["revelations"]["entries"][0]["id"]]})
assert "error" in got and "budget" in got["error"], got
print("OK: a single fragment too long to ever fit the budget still raises, naming the guard")

print("\nALL CHECKS PASSED: test_author_preview")

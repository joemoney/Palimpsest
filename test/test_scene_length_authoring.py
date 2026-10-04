"""CR-14 scene length by moment, authoring side: `narration.scene_length_by_moment` in the
schema, round-tripping through the board's Forms tab untouched, lint (min above max, a per-beat
range for a beat the pacing loop doesn't define, per-beat ranges with no pacing loop), and a load
warning while no engine reads it.

Run directly: python3 test/test_scene_length_authoring.py
"""
import contextlib
import copy
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _llm_stubs  # noqa: E402,F401
import author_lint  # noqa: E402
import author_model  # noqa: E402
import mechanics  # noqa: E402

RAW = {"schema_version": 3, "meta": {"title": "T"},
       "narration": {"pov": "second-person", "scene_length": {"min": 470, "max": 500},
                     "scene_length_by_moment": {
                         "beats": {"respite": {"min": 250, "max": 350}, "threat": {"min": 450, "max": 550}},
                         "directive": {"min": 450, "max": 550}, "finale": {"min": 500, "max": 650},
                         "inquiry": {"min": 120, "max": 220}}},
       "mechanics": {"pacing_loop": {"engine": "beat_counter",
                                     "beats": {"threat": {"definition": "d"}, "respite": {"definition": "d"}}}}}

narration_errors = lambda raw: [e["message"] for e in author_lint.schema_errors(raw) if "narration" in e["message"]]  # noqa: E731
assert narration_errors(RAW) == [], narration_errors(RAW)
bad = copy.deepcopy(RAW)
bad["narration"]["scene_length_by_moment"]["finale"] = {"min": 500}
bad["narration"]["scene_length_by_moment"]["crisis"] = {"min": 1, "max": 2}
assert len(narration_errors(bad)) == 2, narration_errors(bad)
print("OK: scene_length_by_moment is schema-checked (both ends of a range, known moments only)")

model = author_model.to_board_model(RAW)
assert author_model.from_board_model(RAW, model)["narration"] == RAW["narration"]
print("OK: it round-trips through the board untouched")

assert author_lint.scene_length_issues(RAW) == []
x = copy.deepcopy(RAW)
by = x["narration"]["scene_length_by_moment"]
by["inquiry"] = {"min": 300, "max": 100}
by["beats"]["lull"] = {"min": 200, "max": 300}
x["narration"]["scene_length"] = {"min": 600, "max": 500}
got = [(i["id"], i["severity"], i["message"]) for i in author_lint.scene_length_issues(x)]
assert any("a question runs from 300 to 100" in m for _, _, m in got), got
assert any("every other scene runs from 600 to 500" in m for _, _, m in got), got
assert any(i == "L10" and "lull is not a beat" in m for i, _, m in got), got
y = copy.deepcopy(RAW)
del y["mechanics"]["pacing_loop"]
assert any(s == "warning" and "no pacing loop" in m for _, s, m in
           [(i["id"], i["severity"], i["message"]) for i in author_lint.scene_length_issues(y)])
print("OK: lint catches inverted ranges, unknown beats (L10) and per-beat ranges with no pacing loop")

buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    mechanics.validate(RAW)
assert "scene_length_by_moment" not in buf.getvalue()
print("OK: scene_length_by_moment no longer warns at load (an engine reads it)")

se = _llm_stubs.load_story_engine()


def length(directive=False, finale=False, beat=None, story=RAW):
    ctx = {"story": story, "directive_fired": directive,
           "state": {"plot": {"endgame": {"requested": finale}},
                     "pacing": {"last_beat": {"type": beat} if beat else None}}}
    return se._scene_length(ctx)


assert length(finale=True, directive=True, beat="threat")[:2] == (500, 650)
assert length(directive=True, beat="respite")[:2] == (450, 550)
assert length(beat="respite")[:2] == (250, 350)
assert length(beat="unknown")[:2] == (470, 500) and length()[:2] == (470, 500)
assert "120-220" in length(beat="threat")[2]
assert length(directive=True)[2] == "" and length(finale=True)[2] == ""
plain = copy.deepcopy(RAW)
del plain["narration"]["scene_length_by_moment"]
assert length(beat="respite", story=plain) == (470, 500, "")
print("OK: the range follows the moment (finale > directive > previous beat > default); inquiry never on directive/finale")

print("\nALL CHECKS PASSED: test_scene_length_authoring")

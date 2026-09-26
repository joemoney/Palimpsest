"""CR-13 story clock, authoring side: `plot.pacing.story_clock` is in the schema, round-trips
through the board untouched (the Forms tab and the Ending funnel settings both edit the same
object), and warns at load while no engine reads it (build order: authored first, loudly unread).

Run directly: python3 test/test_story_clock_authoring.py
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
       "plot": {"pacing": {"nudge_frequency": 8, "act_check_frequency": 12,
                           "story_clock": {"free_idle_streak": 3, "push_directive": "Someone knocks."}}}}


def pacing_errors(raw):
    return [e["message"] for e in author_lint.schema_errors(raw) if "pacing" in e["message"]]


assert pacing_errors(RAW) == [], pacing_errors(RAW)
bad = copy.deepcopy(RAW)
bad["plot"]["pacing"]["story_clock"] = {"free_idle_streak": 0, "idle_weight": 0.25}
assert len(pacing_errors(bad)) == 2, pacing_errors(bad)
missing = copy.deepcopy(RAW)
missing["plot"]["pacing"]["story_clock"] = {"push_directive": "x"}
assert pacing_errors(missing), "the free streak is required: there is no engine default"
print("OK: story_clock is schema-checked - a free streak of at least 1 is required, nothing else is allowed")

model = author_model.to_board_model(RAW)
assert model["forms"]["plot.pacing"]["story_clock"] == RAW["plot"]["pacing"]["story_clock"]
assert author_model.from_board_model(RAW, model)["plot"]["pacing"] == RAW["plot"]["pacing"]
m = copy.deepcopy(model)
del m["forms"]["plot.pacing"]["story_clock"]
assert "story_clock" not in author_model.from_board_model(RAW, m)["plot"]["pacing"], "turning it off removes it (P-2)"
print("OK: story_clock round-trips through the board and turning it off removes it")

buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    mechanics.validate(RAW)
assert "story_clock (CR-13)" in buf.getvalue(), buf.getvalue()
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    mechanics.validate({"plot": {"pacing": {"nudge_frequency": 8, "act_check_frequency": 12}}})
assert "story_clock" not in buf.getvalue()
print("OK: an authored story clock warns at load while no engine reads it; an absent one is silent")

print("\nALL CHECKS PASSED: test_story_clock_authoring")

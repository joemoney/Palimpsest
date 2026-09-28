"""A bug found on a live playthrough (2026-09-28): `check_and_advance_act` picked the next act
number as `max(every act number that exists) + 1`, never "the next authored act in sequence". A
story authoring acts 1-3 (README/Agent_Authoring_Manual.md: "author only as many as you need") got
Act 1 right, then the moment it resolved, jumped straight to a *generated* Act 4 - `max(1, 2, 3) +
1` - skipping its own Acts 2 and 3 entirely, discarding their authored title, description and
completion_signals for content the model invented on the spot. Predates this session (git blame:
396af0f); invisible until now because example/new_babel each author only Act 1.

This test authors 3 acts and advances through all of them, checking that each authored one is used
- title, description and completion_signals verbatim, no generated_acts entry, no LLM-invented
content kept - and that generation only kicks in once the authored ones run out.

Run directly: python3 test/test_act_advancement.py
"""
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import CannedResponses, load_story_engine  # noqa: E402

se = load_story_engine()

ACTS = [
    {"act_number": 1, "title": "Dead Weight", "description": "d1"},
    {"act_number": 2, "title": "Better Than Its Papers", "description": "d2"},
    {"act_number": 3, "title": "Tacet", "description": "d3"},
]


def make_ctx(user="actadv"):
    ctx = se.state_store.load_state(user, se.state_store.DEFAULT_STORY_SLUG)
    story = se.state_store.thaw(ctx["story"])
    story["plot"]["main_thread"]["acts"] = copy.deepcopy(ACTS)
    ctx["story"] = se.state_store.freeze(story)
    ctx["state"]["plot"]["act_completion"] = {"1": {"completed": False, "optional": False},
                                               "2": {"completed": False, "optional": False},
                                               "3": {"completed": False, "optional": False}}
    ctx["state"]["plot"]["current_act"] = 1
    ctx["state"]["pacing"]["turns_since_act_check"] = 99  # due on cadence, no need to complete a subplot
    return ctx


def ready(title, description, signals):
    # The director's invented content - must be discarded whenever an authored act is waiting.
    return {"ready": True, "reason": "resolved", "next_act_title": title, "next_act_description": description,
            "completion_signals": signals, "new_character": None}


ctx = make_ctx()
se.call_llm_json = lambda p, **kw: ready("WRONG TITLE", "WRONG DESCRIPTION", ["wrong signal"])
new_act = se.check_and_advance_act(ctx)
assert new_act == 2, new_act
assert ctx["state"]["plot"]["current_act"] == 2
assert ctx["state"]["plot"]["act_completion"]["1"]["completed"] is True
assert ctx["state"]["plot"]["act_completion"]["2"] == {"completed": False, "optional": False}
assert ctx["state"]["plot"]["generated_acts"] == [], "an authored act needs nothing generated for it"
current = se._current_act(ctx)
assert current["title"] == "Better Than Its Papers" and current["description"] == "d2", current
assert "WRONG" not in current["title"] and "WRONG" not in current["description"]
print("OK: Act 1 resolving advances to authored Act 2, using its own title and description - never the director's invented ones")

ctx["state"]["pacing"]["turns_since_act_check"] = 99
new_act = se.check_and_advance_act(ctx)
assert new_act == 3 and ctx["state"]["plot"]["current_act"] == 3
assert ctx["state"]["plot"]["act_completion"]["2"]["completed"] is True
assert ctx["state"]["plot"]["generated_acts"] == []
assert se._current_act(ctx)["title"] == "Tacet"
print("OK: Act 2 resolving advances to authored Act 3, still nothing generated")

ctx["state"]["pacing"]["turns_since_act_check"] = 99
se.call_llm_json = lambda p, **kw: ready("The Shape It Was Keeping", "invented content", ["a real signal"])
new_act = se.check_and_advance_act(ctx)
assert new_act == 4 and ctx["state"]["plot"]["current_act"] == 4
assert ctx["state"]["plot"]["act_completion"]["3"]["completed"] is True
assert len(ctx["state"]["plot"]["generated_acts"]) == 1
generated = ctx["state"]["plot"]["generated_acts"][0]
assert generated["title"] == "The Shape It Was Keeping" and generated["completion_signals"] == ["a real signal"]
assert se._current_act(ctx)["title"] == "The Shape It Was Keeping"
print("OK: Act 3 resolving - the last authored one - falls back to generation, using the director's content")

# a second generated act keeps numbering forward from the generated one, same as before this fix
ctx["state"]["pacing"]["turns_since_act_check"] = 99
se.call_llm_json = lambda p, **kw: ready("Further Out", "more invented content", ["another signal"])
new_act = se.check_and_advance_act(ctx)
assert new_act == 5 and len(ctx["state"]["plot"]["generated_acts"]) == 2
print("OK: a second generated act continues numbering forward, unaffected by this fix")

# act_history records every transition, authored or generated, the same way
history = ctx["state"]["plot"]["act_history"]
assert [h["to_act"] for h in history] == [2, 3, 4, 5]
print("OK: act_history records every transition uniformly")

print("\nALL CHECKS PASSED: test_act_advancement")

"""Regression tests for story_engine.parse_narration_and_options - the split between
displayed narration prose and the 3 clickable choices the web UI renders as buttons. Each
option carries both a short third-person "action" label (shown on the button) and a
first-person "prose" rendition (shown as a preview, and submitted as the actual player
action if picked - so what lands in the novel is the prose, not the menu label)."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from _llm_stubs import load_story_engine

se = load_story_engine()


def test_well_formed_options_block():
    text = (
        "The corridor hums with fluorescent light.\n\n"
        "OPTIONS:\n"
        "1. Follow the technician deeper into the ward. || I follow the technician deeper into the ward.\n"
        "2. Ask about the missing intake logs. || \"What happened to the intake logs?\" I ask.\n"
        "3. Slip out while the guard is distracted. || I slip out while the guard's attention is elsewhere."
    )
    narration, options = se.parse_narration_and_options(text)
    assert narration == "The corridor hums with fluorescent light."
    assert options == [
        {"action": "Follow the technician deeper into the ward.",
         "prose": "I follow the technician deeper into the ward."},
        {"action": "Ask about the missing intake logs.",
         "prose": "\"What happened to the intake logs?\" I ask."},
        {"action": "Slip out while the guard is distracted.",
         "prose": "I slip out while the guard's attention is elsewhere."},
    ]


def test_case_insensitive_heading_and_paren_style():
    text = (
        "Something happens.\n\noptions:\n"
        "1) Do this || I do this.\n"
        "2) Do that || I do that.\n"
        "3) Do the other thing || I do the other thing."
    )
    narration, options = se.parse_narration_and_options(text)
    assert narration == "Something happens."
    assert len(options) == 3
    assert options[0] == {"action": "Do this", "prose": "I do this."}


def test_no_options_heading_falls_back_to_full_text():
    text = "Plain narration with no choices offered."
    narration, options = se.parse_narration_and_options(text)
    assert narration == text
    assert options == []


def test_fewer_than_three_options_falls_back():
    text = "Narration.\n\nOPTIONS:\n1. Only one option here || I do the one thing."
    narration, options = se.parse_narration_and_options(text)
    assert narration == text.strip()
    assert options == []


def test_missing_prose_separator_falls_back():
    # a model that ignores the "action || prose" format entirely - old-style plain options
    text = "Narration.\n\nOPTIONS:\n1. First\n2. Second\n3. Third"
    narration, options = se.parse_narration_and_options(text)
    assert narration == text.strip()
    assert options == []


def test_more_than_three_options_truncates_to_three():
    text = (
        "Narration.\n\nOPTIONS:\n"
        "1. First || I do the first thing.\n"
        "2. Second || I do the second thing.\n"
        "3. Third || I do the third thing.\n"
        "4. Fourth || I do the fourth thing."
    )
    narration, options = se.parse_narration_and_options(text)
    assert narration == "Narration."
    assert [o["action"] for o in options] == ["First", "Second", "Third"]


def run_all():
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"  ok: {t.__name__}")
    print(f"All {len(tests)} tests passed in test_parse_options.py")


if __name__ == "__main__":
    run_all()

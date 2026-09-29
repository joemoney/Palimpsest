"""backend/app.py's `_start_turn_job`: the background thread a turn or regenerate request runs
on, and what happens to an exception raised inside it (2026-09-29 incident).

Before this, `run()`'s `except` only caught `story_engine.ActionRefused` and `LLMUnavailableError`
- anything else (a mechanics engine's own prompt-budget guard, in the incident that found this)
propagated out of the thread uncaught. `write_turn_result` was then never called, so
`GET /api/turn/result` found nothing and, after its own 20 retries, fell through to a generic
"lost track of that turn's result" 503 with no indication anything had actually crashed - the
player was simply unable to generate a new narration, with no error anywhere they could see, for
every turn from then on (the underlying cause recurs on every attempt until fixed).

`run()` now catches any exception, logs it in full (docker logs stay the operator's view: still
findable there) and writes a real turn_result so the player is told plainly that the turn failed
and nothing was saved, rather than left polling forever.

Run directly: python3 test/test_turn_job_errors.py
"""
import io
import os
import sys
import time
from contextlib import redirect_stderr

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import load_state_store  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

try:
    import flask  # noqa: F401
except ImportError:
    print("SKIPPED: flask is not installed in this environment (no pip access) - "
          "this test needs `pip install -r requirements.txt` to run for real.")
    sys.exit(0)

import jsonschema  # noqa: E402
if not hasattr(jsonschema, "Draft202012Validator"):
    print(f"SKIPPED: jsonschema {jsonschema.__version__} has no Draft202012Validator - "
          "needs jsonschema>=4.18 (`pip install -r requirements.txt`) to run for real.")
    sys.exit(0)

import tempfile  # noqa: E402

tmp_dir = tempfile.mkdtemp(prefix="cyoa_turn_job_test_")
os.environ.setdefault("GOOGLE_API_KEY", "test-key")
os.environ.setdefault("FLASK_SECRET_KEY", "test-secret")
ss = load_state_store(tmp_dir)

import app as flask_app_module  # noqa: E402
import story_engine  # noqa: E402

USER, SLUG = "turnjobtest", "the_story"


def wait_for_result(timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = ss.read_and_clear_turn_result(USER, SLUG)
        if result is not None:
            return result
        time.sleep(0.01)
    raise AssertionError("no turn_result was written within the timeout")


# --- an unexpected exception: caught, logged, and a real result is written -------------------
def boom():
    raise ValueError("revelations:revealed is 2965 chars, over its 2600-char budget")


captured_stderr = io.StringIO()
with redirect_stderr(captured_stderr):
    flask_app_module._start_turn_job(boom, USER, SLUG)
    result = wait_for_result()

assert result["ok"] is False, result
assert result["error"] and "Something went wrong" in result["error"], result
# the raw exception text must never reach the player - it's an internal detail, not a message
# written for them (unlike LLMUnavailableError/ActionRefused, whose text is player-facing)
assert "2965" not in result["error"] and "budget" not in result["error"], result
assert result.get("refusal") is None
# but it must still be visible to the operator - docker logs stay the source of truth
assert "ValueError" in captured_stderr.getvalue() and "2965 chars" in captured_stderr.getvalue()
print("OK: an unexpected exception in the turn thread is caught, logged in full, and reported "
      "to the player without exposing the raw error - not silently dropped")
# clearing the status beacon on a real crash is take_turn/regenerate_last_turn's own job (their
# `finally` blocks), not _start_turn_job's here - fn() above is a bare stand-in, not a real turn.

# --- ActionRefused and LLMUnavailableError still work exactly as before ----------------------
def refused():
    raise story_engine.ActionRefused("The door won't budge.", "north_gate")


flask_app_module._start_turn_job(refused, USER, SLUG)
result = wait_for_result()
assert result == {"ok": True, "error": None, "refusal": {"sentence": "The door won't budge.", "gate": "north_gate"}}
print("OK: ActionRefused is unaffected - still reported as a refusal, not an error")


def unavailable():
    raise story_engine.LLMUnavailableError("the model is temporarily unreachable")


flask_app_module._start_turn_job(unavailable, USER, SLUG)
result = wait_for_result()
assert result == {"ok": False, "error": "the model is temporarily unreachable", "refusal": None}
print("OK: LLMUnavailableError is unaffected - still reported with its own message")


# --- a normal success still writes ok=True, as before -----------------------------------------
def succeeds():
    pass


flask_app_module._start_turn_job(succeeds, USER, SLUG)
result = wait_for_result()
assert result == {"ok": True, "error": None, "refusal": None}
print("OK: a successful turn still just writes ok=True")

print("\nALL CHECKS PASSED: test_turn_job_errors")

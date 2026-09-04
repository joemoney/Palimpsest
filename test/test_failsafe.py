"""Regression test for call_llm's Gemini fail-safe (story_engine.py): if the primary
provider/model fails, call_llm retries once against the operator's free-tier Gemini model
(GEMINI_MODEL) via a direct Google API call before giving up, so a freely-swapped
TIER_AB_MODEL/TIER_C_MODEL (e.g. an experimental OpenRouter model being tried out) can't
take the whole app down if it turns out to be unreachable or misconfigured. See
test_openrouter.py for the opposite case (fail-safe also fails, LLMUnavailableError still
propagates) - it exhausts the fail-safe deliberately, this file is about it succeeding.

Sets TESTING_FORCE_GOOGLE=false (like test_openrouter.py) so the primary path for both
tiers is the real OpenRouter one under test (TIER_AB_PROVIDER/TIER_C_PROVIDER already
default to "openrouter") - the fail-safe always targets Gemini regardless of which tier's
primary failed.

Run directly: python3 test/test_failsafe.py
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["TESTING_FORCE_GOOGLE"] = "false"
os.environ.setdefault("OPENROUTER_API_KEY", "test-key")
os.environ.setdefault("GOOGLE_API_KEY", "test-key")

# Installs a google.generativeai stub that SUCCEEDS, recording what model name it's
# constructed with - see test_openrouter.py's preamble comment for why this is stubbed
# directly rather than importing the real (likely not pip-installed) google-api-core
# package, and why it has to happen before _llm_stubs.load_story_engine().
_last_model_name = {}


class _FakeGeminiModel:
    def __init__(self, model_name):
        _last_model_name["value"] = model_name

    def generate_content(self, prompt):
        return types.SimpleNamespace(text="fail-safe response")


_genai_stub = types.ModuleType("google.generativeai")
_genai_stub.configure = lambda **k: None
_genai_stub.GenerativeModel = _FakeGeminiModel
if "google" not in sys.modules:
    sys.modules["google"] = types.ModuleType("google")
sys.modules["google"].generativeai = _genai_stub
sys.modules["google.generativeai"] = _genai_stub

from _llm_stubs import load_story_engine  # noqa: E402

se = load_story_engine()

import requests  # noqa: E402  (a real, already-installed dependency - not stubbed)


def _fake_post_fails(url, headers=None, json=None, timeout=None):
    raise requests.exceptions.ConnectionError("primary provider unreachable (test stub)")


# --- narration's OpenRouter primary fails -> the Gemini fail-safe rescues the call ---
se.requests.post = _fake_post_fails
result = se.call_llm("some prompt")
assert result == "fail-safe response", result
assert _last_model_name["value"] == se.GEMINI_MODEL, _last_model_name["value"]
print("OK: a failed narration call is rescued by the Gemini fail-safe (GEMINI_MODEL)")


# --- state-update's OpenRouter primary fails -> the Gemini fail-safe rescues call_llm_json
# too, and its JSON parsing works against the fail-safe's response same as any other ---
def _fake_generate_content_json(self, prompt):
    return types.SimpleNamespace(text='{"rescued": true}')


_FakeGeminiModel.generate_content = _fake_generate_content_json
se.requests.post = _fake_post_fails
result = se.call_llm_json("some prompt")
assert result == {"rescued": True}, result
print("OK: call_llm_json is rescued by the fail-safe too, and still parses as JSON")


# --- regression guard: under the whole-process TESTING_FORCE_GOOGLE override, a call that
# fails must NOT trigger a second, redundant fail-safe attempt against the exact same model.
# Catches a real bug found during development: comparing the fail-safe's "already tried
# this" check against the wrong variable (the raw model= argument, which is usually NOT
# GEMINI_MODEL even when the actual call WAS to GEMINI_MODEL, since the force-google
# override silently substitutes it in) caused exactly this kind of wasted duplicate retry -
# a call-counting stub catches it directly. google.api_core.exceptions is safe to import
# for real now (not stub it manually like test_openrouter.py does) - _llm_stubs'
# _install_stubs(), already run via load_story_engine() above, installed its own generic
# stub for it, since this file never touched that module itself.
from google.api_core.exceptions import GoogleAPIError  # noqa: E402

_call_count = {"n": 0}


class _FailsEveryTimeGeminiModel:
    def __init__(self, model_name):
        pass

    def generate_content(self, prompt):
        _call_count["n"] += 1
        raise GoogleAPIError("simulated Gemini failure (test stub)")


se.genai.GenerativeModel = _FailsEveryTimeGeminiModel
_original_force_google = se.TESTING_FORCE_GOOGLE
se.TESTING_FORCE_GOOGLE = True
try:
    try:
        se.call_llm("some prompt", provider="google")
        raise AssertionError("expected LLMUnavailableError")
    except se.LLMUnavailableError:
        assert _call_count["n"] == 1, (
            f"expected exactly 1 call (no redundant fail-safe retry), got {_call_count['n']}"
        )
        print("OK: a failure under the TESTING_FORCE_GOOGLE override raises "
              "immediately after exactly one attempt, without a wasted duplicate "
              "fail-safe retry against the same model")
finally:
    se.TESTING_FORCE_GOOGLE = _original_force_google

print("\nALL CHECKS PASSED: test_failsafe")

"""Regression test for story_engine's OpenRouter call path (_call_llm_openrouter, the
default provider for both TIER_AB_PROVIDER and TIER_C_PROVIDER in real use - Google is kept
only for testing/debugging and call_llm's fail-safe, see CLAUDE.md): a successful response
is parsed correctly, model routing defaults to the right tier per call, and network/HTTP/
malformed-response failures are all wrapped as the same LLMUnavailableError the Google path
uses - by way of also exhausting call_llm's Gemini fail-safe (see test_failsafe.py for the
fail-safe actually rescuing a failed call, the opposite case), since a bare OpenRouter
failure alone no longer directly raises.

Sets TESTING_FORCE_GOOGLE=false for this file only - the rest of the suite defaults it to
"true" via _llm_stubs.py (the fully-stubbed, provider-agnostic path), but this file
specifically exercises the real OpenRouter path (both tiers already default to
TIER_AB_PROVIDER=TIER_C_PROVIDER="openrouter", so no provider override is needed beyond
disabling the testing force). Each test file runs in its own subprocess (see run_all.py), so
this doesn't affect other test files.

Run directly: python3 test/test_openrouter.py
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["TESTING_FORCE_GOOGLE"] = "false"
os.environ.setdefault("OPENROUTER_API_KEY", "test-key")
os.environ.setdefault("GOOGLE_API_KEY", "test-key")

# Installs BOTH a google.generativeai stub and a google.api_core.exceptions stub BEFORE
# importing story_engine (matching _llm_stubs._install_stubs()'s own stubbing, which only
# stubs a module if it isn't already present, so these survive) - this file's fail-safe
# calls need to raise the EXACT GoogleAPIError class _call_llm_google's lazy `from
# google.api_core.exceptions import GoogleAPIError` will resolve to, so it's stubbed here
# directly rather than trying to import the real (likely not pip-installed) package. This
# file is specifically about the OpenRouter path, so its fail-safe is made to fail
# deterministically too - every failure-mode test below now exercises "OpenRouter down AND
# the Gemini fail-safe also down" rather than the fail-safe silently rescuing what should
# be a failure assertion (see test_failsafe.py for the fail-safe actually rescuing a call).
_GoogleAPIError = type("GoogleAPIError", (Exception,), {})

if "google" not in sys.modules:
    sys.modules["google"] = types.ModuleType("google")
_google_pkg = sys.modules["google"]

_exceptions_stub = types.ModuleType("google.api_core.exceptions")
_exceptions_stub.GoogleAPIError = _GoogleAPIError
_api_core_stub = types.ModuleType("google.api_core")
_api_core_stub.exceptions = _exceptions_stub
sys.modules["google.api_core"] = _api_core_stub
sys.modules["google.api_core.exceptions"] = _exceptions_stub
_google_pkg.api_core = _api_core_stub


class _AlwaysFailsGeminiModel:
    def __init__(self, model_name):
        pass

    def generate_content(self, prompt):
        raise _GoogleAPIError("gemini fail-safe also unavailable (test stub)")


_genai_stub = types.ModuleType("google.generativeai")
_genai_stub.configure = lambda **k: None
_genai_stub.GenerativeModel = _AlwaysFailsGeminiModel
_google_pkg.generativeai = _genai_stub
sys.modules["google.generativeai"] = _genai_stub

from _llm_stubs import load_story_engine  # noqa: E402

se = load_story_engine()

import requests  # noqa: E402  (a real, already-installed dependency - not stubbed)


class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code} error")

    def json(self):
        return self._payload


# --- a successful call parses choices[0].message.content, using the default (Tier A/B) model ---
def _fake_post_ok(url, headers=None, json=None, timeout=None):
    assert json["model"] == se.TIER_AB_MODEL
    return _FakeResponse(200, {"choices": [{"message": {"content": "narration text"}}]})


se.requests.post = _fake_post_ok
assert se.call_llm("some prompt") == "narration text"
print("OK: a successful OpenRouter response is parsed correctly, defaulting to TIER_AB_MODEL")


# --- call_llm_json defaults to Tier C, not Tier A/B, and requests json_object mode ---
def _fake_post_state_update(url, headers=None, json=None, timeout=None):
    assert json["model"] == se.TIER_C_MODEL
    assert json["response_format"] == {"type": "json_object"}
    return _FakeResponse(200, {"choices": [{"message": {"content": "{}"}}]})


se.requests.post = _fake_post_state_update
assert se.call_llm_json("some prompt") == {}
print("OK: call_llm_json defaults to TIER_C_MODEL and requests json_object mode")


# --- call_llm_json's Tier B override (reasoning=True) sends exclude:false ---
def _fake_post_tier_b(url, headers=None, json=None, timeout=None):
    assert json["model"] == se.TIER_AB_MODEL
    assert json["reasoning"] == {"exclude": False}
    return _FakeResponse(200, {"choices": [{"message": {"content": "{}"}}]})


se.requests.post = _fake_post_tier_b
assert se.call_llm_json(
    "some prompt", model=se.TIER_AB_MODEL, provider=se.TIER_AB_PROVIDER, reasoning=True
) == {}
print("OK: reasoning=True (Tier B) sends reasoning.exclude=false")


# --- an explicit model= override is actually sent to OpenRouter ---
def _fake_post_explicit(url, headers=None, json=None, timeout=None):
    assert json["model"] == "some/other-model"
    return _FakeResponse(200, {"choices": [{"message": {"content": "ok"}}]})


se.requests.post = _fake_post_explicit
assert se.call_llm("some prompt", model="some/other-model") == "ok"
print("OK: an explicit model= override is passed through to the request body")


# --- an HTTP error response is wrapped as LLMUnavailableError ---
def _fake_post_http_error(url, headers=None, json=None, timeout=None):
    return _FakeResponse(429, {})


se.requests.post = _fake_post_http_error
try:
    se.call_llm("some prompt")
    raise AssertionError("expected LLMUnavailableError")
except se.LLMUnavailableError:
    print("OK: an HTTP error response from OpenRouter is wrapped as LLMUnavailableError")


# --- a connection failure is wrapped as LLMUnavailableError too ---
def _fake_post_conn_error(url, headers=None, json=None, timeout=None):
    raise requests.exceptions.ConnectionError("network unreachable")


se.requests.post = _fake_post_conn_error
try:
    se.call_llm("some prompt")
    raise AssertionError("expected LLMUnavailableError")
except se.LLMUnavailableError:
    print("OK: a connection failure is wrapped as LLMUnavailableError")


# --- a malformed response body degrades to LLMUnavailableError, not a raw KeyError ---
def _fake_post_malformed(url, headers=None, json=None, timeout=None):
    return _FakeResponse(200, {"unexpected": "shape"})


se.requests.post = _fake_post_malformed
try:
    se.call_llm("some prompt")
    raise AssertionError("expected LLMUnavailableError")
except se.LLMUnavailableError:
    print("OK: a malformed response body is wrapped as LLMUnavailableError, not a raw KeyError")


# --- a call that hangs past OPENROUTER_TOTAL_TIMEOUT is caught even though it never trips
# requests' own per-read timeout - regression test for a real production incident where a
# slow-trickling OpenRouter response ran past gunicorn's --timeout and hard-crashed the
# worker instead of returning a clean error. Shrinks the deadline rather than actually
# sleeping 150s real seconds.
import threading
import time

se.OPENROUTER_TOTAL_TIMEOUT = 0.2


def _fake_post_hangs(url, headers=None, json=None, timeout=None):
    # Blocks well past the shrunk deadline above - simulates a response that never
    # completes (or trickles too slowly for requests' own gap-based timeout to fire).
    threading.Event().wait(2)
    return _FakeResponse(200, {"choices": [{"message": {"content": "too late"}}]})


se.requests.post = _fake_post_hangs
start = time.monotonic()
try:
    se.call_llm("some prompt")
    raise AssertionError("expected LLMUnavailableError")
except se.LLMUnavailableError:
    elapsed = time.monotonic() - start
    assert elapsed < 1.5, f"took {elapsed}s - should have given up at the shrunk deadline, not waited on the hang"
    print("OK: a call that hangs past OPENROUTER_TOTAL_TIMEOUT is caught by the wall-clock "
          "deadline, not left to hang indefinitely")

print("\nALL CHECKS PASSED: test_openrouter")

"""Regression test for story_engine's OpenRouter call path (_call_llm_openrouter, the
default LLM_PROVIDER in real use - Google is kept only for testing/debugging, see
CLAUDE.md): a successful response is parsed correctly, model routing defaults to the right
tier per call, and network/HTTP/malformed-response failures are all wrapped as the same
LLMUnavailableError the Google path uses.

Forces LLM_PROVIDER=openrouter for this file only - the rest of the suite defaults to
LLM_PROVIDER=google via _llm_stubs.py, since that's the fully-stubbed path. Each test file
runs in its own subprocess (see run_all.py), so this doesn't affect other test files.

Run directly: python3 test/test_openrouter.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["LLM_PROVIDER"] = "openrouter"
os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

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


# --- a successful call parses choices[0].message.content, using the default (narration) model ---
def _fake_post_ok(url, headers=None, json=None, timeout=None):
    assert json["model"] == se.NARRATION_MODEL
    return _FakeResponse(200, {"choices": [{"message": {"content": "narration text"}}]})


se.requests.post = _fake_post_ok
assert se.call_llm("some prompt") == "narration text"
print("OK: a successful OpenRouter response is parsed correctly, defaulting to NARRATION_MODEL")


# --- call_llm_json defaults to the state-update tier, not narration ---
def _fake_post_state_update(url, headers=None, json=None, timeout=None):
    assert json["model"] == se.STATE_UPDATE_MODEL
    return _FakeResponse(200, {"choices": [{"message": {"content": "{}"}}]})


se.requests.post = _fake_post_state_update
assert se.call_llm_json("some prompt") == {}
print("OK: call_llm_json defaults to STATE_UPDATE_MODEL")


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

print("\nALL CHECKS PASSED: test_openrouter")

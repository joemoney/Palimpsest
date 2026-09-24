"""backend/author_assist.py: the v1 AI-assist recipe (Authoring_Tool_Spec.md §7, "Ending ->
waypoints"). Offline - google.generativeai is stubbed (see test/_llm_stubs.py), no network,
no real API key needed.

Run directly: python3 test/test_author_assist.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _llm_stubs  # noqa: E402

_llm_stubs._install_stubs()
os.environ.setdefault("GOOGLE_API_KEY", "test-key")

sys.path.insert(0, _llm_stubs.BACKEND_DIR)
import author_assist  # noqa: E402
from google.api_core.exceptions import GoogleAPIError  # noqa: E402 - the stub type, same one author_assist caught


class FakeResponse:
    def __init__(self, text):
        self.text = text


class FakeGeminiModel:
    """Stands in for genai.GenerativeModel(...) - generate_content() delegates to whatever
    the test wired up as author_assist._next_response, same shape as the real SDK call."""

    def __init__(self, *a, **k):
        pass

    def generate_content(self, prompt, request_options=None):
        return _next_response()


_response_fn = None


def _next_response():
    return _response_fn()


def set_response(fn):
    global _response_fn
    _response_fn = fn


author_assist.genai.GenerativeModel = FakeGeminiModel


RAW = {
    "meta": {"genre": "salvage sci-fi", "tone": "cold and physical"},
    "narration": {"style": ["short paragraphs"]},
    "world": {"setting_summary": "a dying hauler in the belt"},
}
ENDING_NODE = {
    "id": "the_end", "kind": "ending", "ekind": "destination", "title": "Still Flying",
    "arc": {"title": "Still Flying", "description": "An arrangement with the ship."},
    "criteria": "QUORUM >= 40 and turn >= 100",
    "hint": "the licence keeps getting renewed",
    "waypoints": [{"id": "licence", "plant": "the licence is renewed", "detect": "", "done_when": None}],
}

# --- no API key configured: a clear error, no request attempted --------------------------
_real_key = author_assist.GOOGLE_API_KEY
author_assist.GOOGLE_API_KEY = ""
try:
    author_assist.suggest_ending_waypoints(RAW, ENDING_NODE)
    raise AssertionError("expected AssistError without an API key")
except author_assist.AssistError as e:
    assert "GOOGLE_API_KEY" in str(e)
author_assist.GOOGLE_API_KEY = _real_key
print("OK: missing API key raises a clear AssistError before any request")

# --- happy path: valid suggestions, existing id filtered out, list capped at 4 -----------
def response_ok():
    return FakeResponse(json.dumps({"waypoints": [
        {"id": "licence", "plant": "a duplicate of the existing one", "detect": ""},  # dropped: dup id
        {"id": "named_once", "plant": "the System gives its name, once", "detect": "Descant is addressed by name"},
        {"id": "", "plant": "missing id", "detect": ""},  # dropped: no id
        {"id": "no_plant", "plant": "", "detect": ""},  # dropped: no plant
        {"id": "extra_1", "plant": "a", "detect": ""},
        {"id": "extra_2", "plant": "b", "detect": ""},
        {"id": "extra_3", "plant": "c", "detect": ""},  # beyond the cap of 4, ignored
    ]}))


set_response(response_ok)
result = author_assist.suggest_ending_waypoints(RAW, ENDING_NODE)
assert len(result) == 4, result
assert all(w["id"] != "licence" for w in result), "must not repeat an existing waypoint id"
assert result[0]["id"] == "named_once"
print("OK: valid suggestions pass through, duplicates/invalid entries dropped, capped at 4")

# --- upstream API error -------------------------------------------------------------------
def response_error():
    raise GoogleAPIError("500")


set_response(response_error)
try:
    author_assist.suggest_ending_waypoints(RAW, ENDING_NODE)
    raise AssertionError("expected AssistError on upstream failure")
except author_assist.AssistError:
    pass
print("OK: an upstream API error raises AssistError")

# --- unparseable content --------------------------------------------------------------------
def response_bad_json():
    return FakeResponse("not json at all")


set_response(response_bad_json)
try:
    author_assist.suggest_ending_waypoints(RAW, ENDING_NODE)
    raise AssertionError("expected AssistError on unparseable content")
except author_assist.AssistError:
    pass
print("OK: unparseable model output raises AssistError")

# --- empty content --------------------------------------------------------------------------
def response_empty():
    return FakeResponse("")


set_response(response_empty)
try:
    author_assist.suggest_ending_waypoints(RAW, ENDING_NODE)
    raise AssertionError("expected AssistError on empty content")
except author_assist.AssistError:
    pass
print("OK: empty model output raises AssistError")

# --- no waypoints key at all -----------------------------------------------------------------
def response_no_waypoints():
    return FakeResponse(json.dumps({"waypoints": []}))


set_response(response_no_waypoints)
try:
    author_assist.suggest_ending_waypoints(RAW, ENDING_NODE)
    raise AssertionError("expected AssistError on an empty waypoints list")
except author_assist.AssistError:
    pass
print("OK: an empty waypoints list raises AssistError")

print("\nALL CHECKS PASSED: test_author_assist")

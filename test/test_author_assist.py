"""backend/author_assist.py: the v1 AI-assist recipe (Authoring_Tool_Spec.md §7, "Ending ->
waypoints"). Offline - requests.post is monkeypatched, no network, no real API key needed.

Run directly: python3 test/test_author_assist.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))
import author_assist  # noqa: E402


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"{self.status_code}")

    def json(self):
        return self._payload


def _chat_payload(content) -> dict:
    return {"choices": [{"message": {"content": content}}]}


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

os.environ.pop("OPENROUTER_API_KEY_TOOL_ASSIST", None)

# --- no API key configured: a clear error, no request attempted --------------------------
try:
    author_assist.suggest_ending_waypoints(RAW, ENDING_NODE)
    raise AssertionError("expected AssistError without an API key")
except author_assist.AssistError as e:
    assert "OPENROUTER_API_KEY_TOOL_ASSIST" in str(e)
print("OK: missing API key raises a clear AssistError before any request")

os.environ["OPENROUTER_API_KEY_TOOL_ASSIST"] = "test-key"

# --- happy path: valid suggestions, existing id filtered out, list capped at 4 -----------
def fake_post_ok(url, headers, json, timeout):
    content = json_module.dumps({"waypoints": [
        {"id": "licence", "plant": "a duplicate of the existing one", "detect": ""},  # dropped: dup id
        {"id": "named_once", "plant": "the System gives its name, once", "detect": "Descant is addressed by name"},
        {"id": "", "plant": "missing id", "detect": ""},  # dropped: no id
        {"id": "no_plant", "plant": "", "detect": ""},  # dropped: no plant
        {"id": "extra_1", "plant": "a", "detect": ""},
        {"id": "extra_2", "plant": "b", "detect": ""},
        {"id": "extra_3", "plant": "c", "detect": ""},  # beyond the cap of 4, ignored
    ]})
    return FakeResponse(_chat_payload(content))


import json as json_module  # after the fake, so the closure above sees the real module
author_assist.requests.post = fake_post_ok
result = author_assist.suggest_ending_waypoints(RAW, ENDING_NODE)
assert len(result) == 4, result
assert all(w["id"] != "licence" for w in result), "must not repeat an existing waypoint id"
assert result[0]["id"] == "named_once"
print("OK: valid suggestions pass through, duplicates/invalid entries dropped, capped at 4")

# --- upstream HTTP error -------------------------------------------------------------------
def fake_post_error(url, headers, json, timeout):
    return FakeResponse({}, status=500)


author_assist.requests.post = fake_post_error
try:
    author_assist.suggest_ending_waypoints(RAW, ENDING_NODE)
    raise AssertionError("expected AssistError on HTTP failure")
except author_assist.AssistError:
    pass
print("OK: an upstream HTTP error raises AssistError")

# --- unparseable content --------------------------------------------------------------------
def fake_post_bad_json(url, headers, json, timeout):
    return FakeResponse(_chat_payload("not json at all"))


author_assist.requests.post = fake_post_bad_json
try:
    author_assist.suggest_ending_waypoints(RAW, ENDING_NODE)
    raise AssertionError("expected AssistError on unparseable content")
except author_assist.AssistError:
    pass
print("OK: unparseable model output raises AssistError")

# --- no waypoints key at all -----------------------------------------------------------------
def fake_post_empty(url, headers, json, timeout):
    return FakeResponse(_chat_payload(json_module.dumps({"waypoints": []})))


author_assist.requests.post = fake_post_empty
try:
    author_assist.suggest_ending_waypoints(RAW, ENDING_NODE)
    raise AssertionError("expected AssistError on an empty waypoints list")
except author_assist.AssistError:
    pass
print("OK: an empty waypoints list raises AssistError")

del os.environ["OPENROUTER_API_KEY_TOOL_ASSIST"]
print("\nALL CHECKS PASSED: test_author_assist")

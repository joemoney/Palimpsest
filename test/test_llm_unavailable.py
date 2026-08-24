"""Regression test for story_engine.call_llm's error wrapping: a real API failure (rate
limit, quota exhausted, transient outage - anything raised by the google-api-core SDK as a
GoogleAPIError) should come out as story_engine.LLMUnavailableError, a stable type app.py's
global Flask error handler can catch regardless of which underlying SDK exception caused it.

Run directly: python3 test/test_llm_unavailable.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import load_story_engine  # noqa: E402

se = load_story_engine()

from google.api_core.exceptions import GoogleAPIError  # noqa: E402  (the stub installed above)


class _FailingModel:
    def generate_content(self, prompt):
        raise GoogleAPIError("429 quota exceeded, retry in 47s")


se.genai.GenerativeModel = lambda *a, **k: _FailingModel()

try:
    se.call_llm("some prompt")
    raise AssertionError("expected LLMUnavailableError to be raised")
except se.LLMUnavailableError as e:
    assert "quota exceeded" in str(e)
    print("OK: a GoogleAPIError from the SDK is wrapped as LLMUnavailableError")

# --- a normal successful call is unaffected ---
class _WorkingModel:
    def generate_content(self, prompt):
        class Response:
            text = "narration text"
        return Response()


se.genai.GenerativeModel = lambda *a, **k: _WorkingModel()
assert se.call_llm("some prompt") == "narration text"
print("OK: a successful call still returns the response text unchanged")

print("\nALL CHECKS PASSED: test_llm_unavailable")

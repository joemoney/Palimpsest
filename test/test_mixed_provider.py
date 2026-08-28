"""Regression test for per-tier provider selection: narration stays on OpenRouter/DeepSeek
(LLM_PROVIDER) while the state-update tier defaults to calling Google's Gemini API directly
(STATE_UPDATE_PROVIDER=google, the operator's own GOOGLE_API_KEY) - added when
STATE_UPDATE_MODEL was switched from a DeepSeek OpenRouter slug to a real Gemini model
name. Confirms both providers are live simultaneously in this mode, that call_llm_json
actually respects the requested STATE_UPDATE_MODEL (_call_llm_google used to always ignore
its model argument in favor of GEMINI_MODEL - now only true under the whole-process
LLM_PROVIDER=google testing override, not this mixed mode), and that narration is
untouched, still going through OpenRouter.

Run directly: python3 test/test_mixed_provider.py
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["LLM_PROVIDER"] = "openrouter"
os.environ.setdefault("OPENROUTER_API_KEY", "test-key")
os.environ.setdefault("GOOGLE_API_KEY", "test-key")
# STATE_UPDATE_PROVIDER deliberately left unset - this test exercises its real default
# ("google", set in story_engine.py), not an explicit override.

# Installs a smarter google.generativeai stub BEFORE importing story_engine, so
# _llm_stubs._install_stubs() (which only stubs a module if it isn't already present)
# leaves this one in place instead of overwriting it with the generic dumb stub (which
# just returns None and would crash if actually called). This one records what model name
# it's constructed with and returns a controllable response.
_last_model_name = {}


class _FakeGeminiModel:
    def __init__(self, model_name):
        _last_model_name["value"] = model_name

    def generate_content(self, prompt):
        return types.SimpleNamespace(text='{"ok": true}')


_genai_stub = types.ModuleType("google.generativeai")
_genai_stub.configure = lambda **k: None
_genai_stub.GenerativeModel = _FakeGeminiModel
if "google" not in sys.modules:
    sys.modules["google"] = types.ModuleType("google")
sys.modules["google"].generativeai = _genai_stub
sys.modules["google.generativeai"] = _genai_stub

from _llm_stubs import load_story_engine  # noqa: E402

se = load_story_engine()

assert se.LLM_PROVIDER == "openrouter"
assert se.STATE_UPDATE_PROVIDER == "google"
print("OK: LLM_PROVIDER defaults to openrouter, STATE_UPDATE_PROVIDER defaults to google")


# --- narration (call_llm's default) still goes through OpenRouter, untouched ---
class _FakeResponse:
    status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": "narration text"}}]}


def _fake_post(url, headers=None, json=None, timeout=None):
    assert json["model"] == se.NARRATION_MODEL
    return _FakeResponse()


se.requests.post = _fake_post
assert se.call_llm("some prompt") == "narration text"
print("OK: narration (call_llm's default provider) still goes through OpenRouter")


# --- state-update (call_llm_json's default) goes straight to Google, respecting the real
# STATE_UPDATE_MODEL rather than silently swapping in GEMINI_MODEL ---
result = se.call_llm_json("some prompt")
assert result == {"ok": True}
assert _last_model_name["value"] == se.STATE_UPDATE_MODEL, _last_model_name["value"]
assert _last_model_name["value"] != se.GEMINI_MODEL
print("OK: call_llm_json (state-update tier) calls Google directly with STATE_UPDATE_MODEL, not GEMINI_MODEL")

print("\nALL CHECKS PASSED: test_mixed_provider")

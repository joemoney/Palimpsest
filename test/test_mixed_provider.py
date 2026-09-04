"""Regression test for per-tier provider selection: both TIER_AB_PROVIDER and
TIER_C_PROVIDER default to "openrouter" in real use (Google is reserved for testing/
debugging and call_llm's fail-safe - see CLAUDE.md), but an operator can still point Tier C
at Google directly (TIER_C_PROVIDER=google, using their own GOOGLE_API_KEY) if they want
the state-update tier's every-turn call routed that way - e.g. when trying a real Gemini
model there instead of an OpenRouter slug. This file exercises exactly that opt-in mixed
mode: confirms both providers are live simultaneously in one process, that call_llm_json
actually respects the requested TIER_C_MODEL (_call_llm_google used to always ignore its
model argument in favor of GEMINI_MODEL - now only true under the whole-process
TESTING_FORCE_GOOGLE testing override, not this mixed mode), and that narration (Tier A) is
untouched, still going through OpenRouter.

Run directly: python3 test/test_mixed_provider.py
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["TESTING_FORCE_GOOGLE"] = "false"
os.environ["TIER_C_PROVIDER"] = "google"  # the one opt-in override this file exercises
# A real Gemini model name (no "google/" prefix, that's OpenRouter's slug convention) -
# TIER_C_MODEL's own default is an OpenRouter slug, which wouldn't make sense paired with
# TIER_C_PROVIDER=google above.
os.environ["TIER_C_MODEL"] = "gemini-3.5-flash-lite"
os.environ.setdefault("OPENROUTER_API_KEY", "test-key")
os.environ.setdefault("GOOGLE_API_KEY", "test-key")

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

assert se.TIER_AB_PROVIDER == "openrouter"
assert se.TIER_C_PROVIDER == "google"
print("OK: TIER_AB_PROVIDER defaults to openrouter; TIER_C_PROVIDER honors the opt-in google override")


# --- narration (Tier A, call_llm's default) still goes through OpenRouter, untouched ---
class _FakeResponse:
    status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": "narration text"}}]}


def _fake_post(url, headers=None, json=None, timeout=None):
    assert json["model"] == se.TIER_AB_MODEL
    return _FakeResponse()


se.requests.post = _fake_post
assert se.call_llm("some prompt") == "narration text"
print("OK: narration (Tier A/B's default provider) still goes through OpenRouter")


# --- state-update (Tier C, call_llm_json's default) goes straight to Google, respecting the
# real TIER_C_MODEL rather than silently swapping in GEMINI_MODEL ---
result = se.call_llm_json("some prompt")
assert result == {"ok": True}
assert _last_model_name["value"] == se.TIER_C_MODEL, _last_model_name["value"]
assert _last_model_name["value"] != se.GEMINI_MODEL
print("OK: call_llm_json (Tier C) calls Google directly with TIER_C_MODEL, not GEMINI_MODEL")

print("\nALL CHECKS PASSED: test_mixed_provider")

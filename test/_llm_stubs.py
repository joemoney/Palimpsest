"""Shared scaffolding for offline regression tests.

story_engine.py imports `dotenv`, `google.generativeai`, and (transitively, via
state_store.py) `filelock` and `werkzeug.security` at module load time, and raises
if GOOGLE_API_KEY isn't set. None of that is needed to test the pure state machine
(subplot pool, act advancement, endgame lockout, flag archiving, storage layer), so
this module stubs those dependencies out and hands back the real story_engine /
state_store modules with call_llm/call_llm_json ready to be monkeypatched. No
network access and no pip-installed dependencies required.

filelock and werkzeug.security get real-enough stand-ins rather than pure no-ops:
- filelock.FileLock becomes a no-op context manager (safe - these tests are
  single-threaded, so nothing is actually racing).
- werkzeug.security's hash/check functions become a trivial sha256-based pair -
  not secure, never used outside this test harness, but real enough that
  state_store.create_account/verify_login's actual logic (not just a mock) gets
  exercised: wrong passwords really fail, right ones really succeed.
"""
import copy
import hashlib
import os
import sys
import tempfile
import types

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(REPO_ROOT, "backend")


def _stub_module(name, **attrs):
    if name in sys.modules:
        return sys.modules[name]
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


class _NoOpLock:
    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _stub_test_password_hash(password):
    return "stub$" + hashlib.sha256(password.encode()).hexdigest()


def _stub_test_check_password_hash(hash_, password):
    return hash_ == _stub_test_password_hash(password)


def _install_stubs():
    _stub_module("dotenv", load_dotenv=lambda *a, **k: None)

    if "google" not in sys.modules:
        sys.modules["google"] = types.ModuleType("google")
    google_stub = sys.modules["google"]

    if "google.generativeai" not in sys.modules:
        genai_stub = _stub_module(
            "google.generativeai",
            configure=lambda **k: None,
            GenerativeModel=lambda *a, **k: None,
        )
        google_stub.generativeai = genai_stub

    if "google.api_core.exceptions" not in sys.modules:
        # story_engine.call_llm imports GoogleAPIError from here (lazily) to wrap real API
        # failures as LLMUnavailableError - stub just enough to test that wrapping offline.
        exceptions_stub = _stub_module(
            "google.api_core.exceptions", GoogleAPIError=type("GoogleAPIError", (Exception,), {})
        )
        api_core_stub = types.ModuleType("google.api_core")
        api_core_stub.exceptions = exceptions_stub
        sys.modules["google.api_core"] = api_core_stub
        google_stub.api_core = api_core_stub

    _stub_module("filelock", FileLock=_NoOpLock)

    if "werkzeug.security" not in sys.modules:
        werkzeug_stub = types.ModuleType("werkzeug")
        security_stub = _stub_module(
            "werkzeug.security",
            generate_password_hash=_stub_test_password_hash,
            check_password_hash=_stub_test_check_password_hash,
        )
        werkzeug_stub.security = security_stub
        sys.modules["werkzeug"] = werkzeug_stub


def _redirect_data_dir(ss, data_dir):
    """Points every DATA_DIR-derived path (runtime-only: saves/, accounts.db,
    perf_stats.json) at data_dir. Shared by load_story_engine/load_state_store below so the
    two never drift apart - both need every path state_store derives from DATA_DIR
    redirected, not just the ones each test file happens to touch directly. In particular
    PERF_STATS_PATH: story_engine.py's _timed() calls state_store.record_call_duration() on
    every single call unconditionally (unlike the turn-status beacon, which only writes
    when a real take_turn/regenerate_last_turn call set up _status_ctx) - any test that
    exercises story_engine's LLM-call machinery at all would otherwise leak stub/test
    timings into the real data/perf_stats.json on disk, which is exactly what happened
    before this helper existed (load_story_engine() didn't redirect DATA_DIR, only
    load_state_store() did)."""
    ss.DATA_DIR = data_dir
    ss.SAVES_DIR = os.path.join(ss.DATA_DIR, "saves")
    ss.ACCOUNTS_DB_PATH = os.path.join(ss.DATA_DIR, "accounts.db")
    ss.PERF_STATS_PATH = os.path.join(ss.DATA_DIR, "perf_stats.json")


def load_story_engine():
    """Import story_engine (and its state_store dependency) with external deps
    stubbed out. Idempotent - safe to call from multiple test files in the same
    run. Redirects state_store's runtime storage (DATA_DIR and everything derived from
    it) to a fresh tmp dir per call, same as load_state_store() below - but leaves
    STORIES_DIR alone, since tests here deliberately read the real, committed stories/
    content (e.g. DEFAULT_STORY_SLUG's template.json) rather than a fixture. Use
    load_state_store() instead for tests that exercise the storage layer itself and need
    STORIES_DIR redirected too."""
    os.chdir(REPO_ROOT)  # engine modules resolve stories/, data/ relative to cwd
    os.environ.setdefault("GOOGLE_API_KEY", "test-key")
    # story_engine.py defaults LLM_PROVIDER to "openrouter" in real use, but Google is the
    # provider kept around specifically for testing/debugging (see CLAUDE.md) - it's the
    # one with a stubbable SDK (google.generativeai, below), so the offline suite forces it
    # here rather than needing a fake OPENROUTER_API_KEY plus a requests.post stub for every
    # test file that merely imports story_engine.
    os.environ.setdefault("LLM_PROVIDER", "google")
    _install_stubs()

    if BACKEND_DIR not in sys.path:
        sys.path.insert(0, BACKEND_DIR)

    import story_engine as se
    _redirect_data_dir(se.state_store, os.path.join(tempfile.mkdtemp(prefix="cyoa_story_engine_test_"), "data"))
    return se


def load_state_store(tmp_path):
    """Import state_store with external deps stubbed out AND its storage paths
    redirected under tmp_path, so tests never touch the real stories/ or data/
    directories. tmp_path should be a fresh directory per test."""
    os.environ.setdefault("GOOGLE_API_KEY", "test-key")
    os.environ.setdefault("LLM_PROVIDER", "google")  # see load_story_engine() above
    _install_stubs()

    if BACKEND_DIR not in sys.path:
        sys.path.insert(0, BACKEND_DIR)

    import state_store as ss
    ss.STORIES_DIR = os.path.join(tmp_path, "stories")
    _redirect_data_dir(ss, os.path.join(tmp_path, "data"))
    os.makedirs(ss.STORIES_DIR, exist_ok=True)
    return ss


class CannedResponses:
    """A queue of canned call_llm_json responses, popped in call order. Raises if
    a test exercises more (or fewer) LLM calls than expected."""

    def __init__(self, responses):
        self._responses = list(responses)

    def __call__(self, prompt, **kwargs):
        # **kwargs (e.g. call_llm's model=) accepted and ignored - this stands in for
        # either call_llm or call_llm_json depending on the test, and only the former is
        # ever called with a model= kwarg by real story_engine.py code.
        assert self._responses, "ran out of canned responses - more LLM calls happened than expected"
        return copy.deepcopy(self._responses.pop(0))

    def remaining(self):
        return len(self._responses)


class RecordingLLM:
    """Records every prompt it's called with and delegates the response to a
    caller-supplied function - useful for asserting on prompt *content* (e.g.
    that a context-bounding limit is respected) rather than just the outcome."""

    def __init__(self, response_fn):
        self.prompts = []
        self._response_fn = response_fn

    def __call__(self, prompt, **kwargs):
        # **kwargs (e.g. call_llm's model=) accepted and ignored - see CannedResponses above.
        self.prompts.append(prompt)
        return self._response_fn(prompt)

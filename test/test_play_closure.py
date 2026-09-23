"""backend/app.py's `_close_play_during_overhaul` before_request hook: /stories and /play
are closed (503, with a message) while PLAY_ENABLED isn't set to "1", since no story is
reliably playable end to end during the V3 schema migration. /help, /login, /author and
/labels are unaffected - gated separately, or not at all.

Same pattern as test_app_routes.py: Flask's real test client, state storage redirected to a
temp dir, no network. Skips gracefully without flask, same contract as test_app_routes.py.

Run directly: python3 test/test_play_closure.py
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import load_state_store  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

try:
    import flask  # noqa: F401
except ImportError:
    print("SKIPPED: flask is not installed in this environment (no pip access) - "
          "this test needs `pip install -r requirements.txt` to run for real.")
    sys.exit(0)

tmp_dir = tempfile.mkdtemp(prefix="cyoa_play_closure_test_")
try:
    os.environ.pop("PLAY_ENABLED", None)
    os.environ.setdefault("GOOGLE_API_KEY", "test-key")
    os.environ.setdefault("FLASK_SECRET_KEY", "test-secret")

    ss = load_state_store(tmp_dir)

    example_path = os.path.join(REPO_ROOT, "stories", "example", "template.json")
    with open(example_path, encoding="utf-8") as f:
        template = json.load(f)
    story_dir = os.path.join(ss.STORIES_DIR, "example")
    os.makedirs(story_dir, exist_ok=True)
    with open(os.path.join(story_dir, "template.json"), "w", encoding="utf-8") as f:
        json.dump(template, f)

    import app as flask_app_module  # noqa: E402

    app = flask_app_module.app
    app.testing = True
    ss.create_account("alice", "correct-horse")
    client = app.test_client()
    client.post("/login", data={"username": "alice", "password": "correct-horse"})

    # --- closed by default -----------------------------------------------------------------
    resp = client.get("/stories")
    assert resp.status_code == 503, resp.status_code
    assert b"Closed for the V3 overhaul" in resp.data
    print("OK: /stories is closed (503) with an explanatory page by default")

    resp = client.get("/play/example")
    assert resp.status_code == 503, resp.status_code
    print("OK: /play/<slug> is closed by default")

    resp = client.get("/")
    assert resp.status_code == 503, resp.status_code
    print("OK: / is closed by default (redirects to /stories otherwise)")

    # --- unaffected routes ------------------------------------------------------------------
    resp = client.get("/help")
    assert resp.status_code == 200, resp.status_code
    print("OK: /help stays open during the closure")

    # --- an unauthenticated visitor is redirected to login, not shown the closed page -------
    resp = app.test_client().get("/stories", follow_redirects=False)
    assert resp.status_code == 302, resp.status_code
    assert "/login" in resp.headers["Location"]
    print("OK: an unauthenticated visitor is redirected to login rather than shown the notice")

    # --- lifting the closure re-opens play ---------------------------------------------------
    os.environ["PLAY_ENABLED"] = "1"
    resp = client.get("/stories")
    assert resp.status_code == 200, resp.status_code
    print("OK: PLAY_ENABLED=1 re-opens /stories")

    print("\nALL CHECKS PASSED: test_play_closure")
finally:
    os.environ.pop("PLAY_ENABLED", None)
    shutil.rmtree(tmp_dir, ignore_errors=True)

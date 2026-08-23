"""Regression test for app.py's web routes: login (with a backend-provisioned
account), the story picker, and the /play flow (name capture, then a turn),
exercised through Flask's real test client - not a hand-rolled stub, since
faking the routing/session/template-rendering machinery would mostly just be
testing the fake rather than the app. story_engine's LLM calls are still
monkeypatched (no network), and state_store's storage is redirected to a temp
directory (never the real stories/ or data/).

NOTE: unlike the rest of test/, this one needs the real `flask` package (and
its dependencies) installed - `pip install -r requirements.txt` - since it's
specifically verifying the real Flask integration. It cannot run in an
environment without pip access.

Run directly: python3 test/test_app_routes.py
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import CannedResponses, load_state_store  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

try:
    import flask  # noqa: F401
except ImportError:
    print("SKIPPED: flask is not installed in this environment (no pip access) - "
          "this test needs `pip install -r requirements.txt` to run for real.")
    sys.exit(0)

tmp_dir = tempfile.mkdtemp(prefix="cyoa_app_routes_test_")
try:
    os.environ.setdefault("GOOGLE_API_KEY", "test-key")
    os.environ.setdefault("FLASK_SECRET_KEY", "test-secret")

    ss = load_state_store(tmp_dir)

    # Seed the catalog with a copy of the real New Babel template - app.py's
    # routes exercise the full opening-scene/take_turn machinery, which needs
    # the real schema (plot.opening_scene, player.flags_active, etc.), not a
    # minimal stub template.
    real_template_path = os.path.join(REPO_ROOT, "stories", "new_babel", "template.json")
    with open(real_template_path) as f:
        template = json.load(f)
    story_dir = os.path.join(ss.STORIES_DIR, "new_babel")
    os.makedirs(story_dir, exist_ok=True)
    with open(os.path.join(story_dir, "template.json"), "w") as f:
        json.dump(template, f)

    import story_engine as se  # noqa: E402  (picks up the same stubbed state_store)
    import app as flask_app_module  # noqa: E402

    # call_llm handles narration (returns a string); call_llm_json handles the
    # separate state-update pass that follows every turn (returns a dict) - two
    # independent canned-response queues, one per function.
    call_queue = CannedResponses([
        "You step into the corridor. The city hums beyond.\n\n1. Keep walking.\n2. Stop and listen.",
    ])
    json_queue = CannedResponses([
        {"subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [], "entity_interaction": False},
    ])
    se.call_llm = call_queue
    se.call_llm_json = json_queue

    alice_id = ss.create_account("alice", "correct-horse")

    app = flask_app_module.app
    app.testing = True
    client = app.test_client()

    # --- unauthenticated access redirects to login ---
    resp = client.get("/stories", follow_redirects=False)
    assert resp.status_code in (301, 302), resp.status_code
    assert "/login" in resp.headers["Location"]
    print("OK: unauthenticated /stories redirects to /login")

    # --- wrong password fails ---
    resp = client.post("/login", data={"username": "alice", "password": "wrong"})
    assert resp.status_code == 200
    assert b"Incorrect username or password" in resp.data
    print("OK: wrong password is rejected with an error, not a session")

    # --- correct login succeeds and lands us a session ---
    resp = client.post("/login", data={"username": "alice", "password": "correct-horse"}, follow_redirects=True)
    assert resp.status_code == 200
    print("OK: correct login succeeds")

    # --- story picker lists the seeded template ---
    resp = client.get("/stories")
    assert resp.status_code == 200
    assert b"New Babel" in resp.data or template["meta"]["title"].encode() in resp.data
    print("OK: /stories lists the seeded catalog")

    # --- first visit to /play is the name-capture phase ---
    resp = client.get("/play/new_babel")
    assert resp.status_code == 200
    assert b'name="name"' in resp.data
    print("OK: first /play visit shows the name-entry form")

    # --- submitting a name applies it and flips into normal play ---
    resp = client.post("/play/new_babel", data={"name": "Vesper Kade"}, follow_redirects=True)
    assert resp.status_code == 200
    assert b"Vesper Kade" in resp.data or b'name="action"' in resp.data
    state = ss.load_state(alice_id, "new_babel")
    assert state["player"]["name"] == "Vesper Kade"
    assert state["plot"]["opening_scene"]["played"] is True
    print("OK: submitting a name applies it and moves the save into normal play")

    # --- submitting an action calls take_turn and updates the save ---
    resp = client.post("/play/new_babel", data={"action": "look around"}, follow_redirects=True)
    assert resp.status_code == 200
    state = ss.load_state(alice_id, "new_babel")
    assert state["plot"]["pacing"]["turn_count"] == 1
    print("OK: submitting an action advances the turn count via story_engine.take_turn")

    # --- a second user is fully isolated from alice's save ---
    ss.create_account("bob", "hunter2")
    bob_client = app.test_client()
    bob_client.post("/login", data={"username": "bob", "password": "hunter2"})
    resp = bob_client.get("/play/new_babel")
    assert b'name="name"' in resp.data, "bob should see his own fresh opening, not alice's progress"
    print("OK: a second user gets an independent save, isolated from the first")

    print("\nALL CHECKS PASSED: test_app_routes")
finally:
    shutil.rmtree(tmp_dir, ignore_errors=True)

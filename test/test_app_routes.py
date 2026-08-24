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
    # independent canned-response queues, one per function. One entry per
    # take_turn/regenerate_last_turn call made below: the initial action, the
    # regenerate, then two more turns to build up enough history to exercise
    # pagination (4 total turns, one more than INITIAL_TURNS_SHOWN).
    narrations = [
        "You step into the corridor. The city hums beyond.\n\n1. Keep walking.\n2. Stop and listen.",
        "A different corridor unfolds. Something else happens.\n\n1. Go left.\n2. Go right.",
        "Second narration continues onward.\n\n1. Push forward.\n2. Retreat.",
        "Third narration wraps the sequence.\n\n1. Rest.\n2. Move on.",
    ]
    state_update = {"subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [], "entity_interaction": False}
    call_queue = CannedResponses(narrations)
    json_queue = CannedResponses([state_update] * len(narrations))
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

    # --- POST /api/turn calls take_turn and returns a scene+controls htmx fragment ---
    resp = client.post("/play/new_babel/api/turn", data={"action": "look around"})
    assert resp.status_code == 200
    assert b'class="scene-block"' in resp.data
    assert b'id="controls"' in resp.data and b'hx-swap-oob="true"' in resp.data
    state = ss.load_state(alice_id, "new_babel")
    assert state["plot"]["pacing"]["turn_count"] == 1
    print("OK: POST /api/turn advances the turn count and returns a scene+controls fragment")

    # --- POST /api/regenerate replaces the last turn instead of appending ---
    turns_before = len(state["history_log"]["recent_turns"])
    resp = client.post("/play/new_babel/api/regenerate")
    assert resp.status_code == 200
    assert b"different corridor" in resp.data
    state = ss.load_state(alice_id, "new_babel")
    assert len(state["history_log"]["recent_turns"]) == turns_before
    print("OK: POST /api/regenerate re-rolls the last turn without growing recent_turns")

    # --- a blank action is a no-op (matches the textarea's required attribute) ---
    resp = client.post("/play/new_babel/api/turn", data={"action": "   "})
    assert resp.status_code == 200
    assert resp.data == b""
    state = ss.load_state(alice_id, "new_babel")
    assert len(state["history_log"]["recent_turns"]) == turns_before
    print("OK: POST /api/turn with a blank action is a no-op")

    # --- two more turns build up enough history to exercise pagination ---
    resp = client.post("/play/new_babel/api/turn", data={"action": "push forward"})
    assert resp.status_code == 200
    resp = client.post("/play/new_babel/api/turn", data={"action": "rest a moment"})
    assert resp.status_code == 200
    state = ss.load_state(alice_id, "new_babel")
    all_turns = state["history_log"].get("full_transcript", []) + state["history_log"]["recent_turns"]
    total = len(all_turns)
    assert total == 4, total
    oldest_index = max(0, total - 3)  # INITIAL_TURNS_SHOWN in app.py
    assert oldest_index == 1
    print("OK: two more turns build up history for pagination checks")

    # --- initial /play render shows a scroll-sentinel once history exceeds the window ---
    resp = client.get("/play/new_babel")
    assert resp.status_code == 200
    assert b'id="scroll-sentinel"' in resp.data
    print("OK: /play renders a scroll-sentinel once there's older history to page in")

    # --- GET /api/history returns the older batch and omits the sentinel once exhausted ---
    resp = client.get("/play/new_babel/api/history", query_string={"before": oldest_index, "count": 3})
    assert resp.status_code == 200
    assert b'class="scene-block"' in resp.data
    assert b'id="scroll-sentinel"' not in resp.data
    print("OK: GET /api/history returns the older batch and stops the chain once exhausted")

    # --- GET /api/history at before=0 is the chain-termination case: nothing to return ---
    resp = client.get("/play/new_babel/api/history", query_string={"before": 0, "count": 3})
    assert resp.status_code == 200
    assert resp.data == b""
    print("OK: GET /api/history at before=0 returns nothing further to page")

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

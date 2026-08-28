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

    # --- submitting a name applies it and flips into the first character-creation step
    # (new_babel authors a two-step character_creation list: class, then starting_place -
    # this is what exercises that generic mechanism, not an engine default - the example
    # story defines no steps and skips straight to play) ---
    resp = client.post("/play/new_babel", data={"name": "Vesper Kade"}, follow_redirects=True)
    assert resp.status_code == 200
    assert b"choose your approach" in resp.data.lower()
    assert b"The Ghost Runner" in resp.data
    state = ss.load_state(alice_id, "new_babel")
    assert state["player"]["name"] == "Vesper Kade"
    assert state["plot"]["opening_scene"]["played"] is True
    assert state["player"]["creation_choices"] == {}
    print("OK: submitting a name applies it and moves the save into the first creation step")

    # --- an unrecognized option_id re-renders the current step with an error, doesn't
    # crash or silently proceed ---
    resp = client.post("/play/new_babel", data={"option_id": "not-a-real-option"})
    assert resp.status_code == 200
    assert b"choose your approach" in resp.data.lower()
    assert b"Please choose one" in resp.data
    state = ss.load_state(alice_id, "new_babel")
    assert state["player"]["creation_choices"] == {}
    print("OK: an invalid option_id re-renders the current step with an error instead of proceeding")

    # --- picking a real class option seeds player.stats and advances to the next step
    # (starting_place), rather than dropping straight into play ---
    resp = client.post("/play/new_babel", data={"option_id": "ghost_runner"}, follow_redirects=True)
    assert resp.status_code == 200
    assert b"where do you go first" in resp.data.lower()
    state = ss.load_state(alice_id, "new_babel")
    assert state["player"]["creation_choices"] == {"class": "ghost_runner"}
    assert state["player"]["stats"] == {"health": 90, "neural_load": 10, "attention_level": 0}
    print("OK: picking a class seeds player.stats and advances to the next creation step")

    # --- picking the final step's option completes character creation and flips into
    # normal play ---
    resp = client.post("/play/new_babel", data={"option_id": "drowned_quarter"}, follow_redirects=True)
    assert resp.status_code == 200
    assert b'name="action"' in resp.data
    state = ss.load_state(alice_id, "new_babel")
    assert state["player"]["creation_choices"] == {"class": "ghost_runner", "starting_place": "drowned_quarter"}
    print("OK: completing the last creation step moves the save into normal play")

    # --- GET /api/status with no turn in flight reports nothing (no stale beacon lying
    # around from a previous request that never happened) ---
    resp = client.get("/play/new_babel/api/status")
    assert resp.status_code == 200
    assert resp.get_json() == {"label": None}
    print("OK: GET /api/status with no turn in flight reports label: None")

    # --- POST /api/turn calls take_turn and returns a scene+controls htmx fragment ---
    resp = client.post("/play/new_babel/api/turn", data={"action": "look around"})
    assert resp.status_code == 200
    assert b'class="scene-block"' in resp.data
    assert b'id="controls"' in resp.data and b'hx-swap-oob="true"' in resp.data
    state = ss.load_state(alice_id, "new_babel")
    assert state["plot"]["pacing"]["turn_count"] == 1
    print("OK: POST /api/turn advances the turn count and returns a scene+controls fragment")

    # --- the status beacon is cleared once the turn completes (take_turn's finally),
    # not left showing a stale "Reckoning"/"Narrating" from the just-finished turn ---
    resp = client.get("/play/new_babel/api/status")
    assert resp.status_code == 200
    assert resp.get_json() == {"label": None}
    print("OK: GET /api/status is cleared back to None after the turn completes")

    # --- a turn already in flight for this save makes /api/turn and /api/regenerate refuse
    # to start a second one, rather than racing it - the turn-status beacon doubles as a
    # cheap in-flight lock, not just a display hint for the busy indicator. Simulated
    # directly via the beacon rather than a real concurrent request - Flask's test client
    # runs requests synchronously, so two genuinely overlapping requests aren't reachable
    # here; this exercises the same check the routes make either way. ---
    ss.write_turn_status(alice_id, "new_babel", "Narrating")
    resp = client.post("/play/new_babel/api/turn", data={"action": "look around"})
    assert resp.status_code == 409, resp.status_code
    resp = client.post("/play/new_babel/api/regenerate")
    assert resp.status_code == 409, resp.status_code
    ss.clear_turn_status(alice_id, "new_babel")
    print("OK: a turn already in flight makes /api/turn and /api/regenerate return 409 "
          "instead of racing a second take_turn/regenerate_last_turn call")

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

    # --- the status beacon reflects whichever call is actually in progress, not just a
    # generic "in flight" flag - a fresh account so this doesn't disturb alice's canned-
    # response queue above. Each canned call reads its own beacon via a direct state_store
    # call (not another HTTP request - Flask's test client runs requests synchronously, so
    # a real concurrent poll mid-request isn't reachable here) to prove _timed() writes the
    # label *before* running the call it's timing, not after. ---
    carol_id = ss.create_account("carol", "carol-password")
    carol_client = app.test_client()
    carol_client.post("/login", data={"username": "carol", "password": "carol-password"})
    carol_client.post("/play/new_babel", data={"name": "Carol"}, follow_redirects=True)
    carol_client.post("/play/new_babel", data={"option_id": "cordon_asset"}, follow_redirects=True)
    carol_client.post("/play/new_babel", data={"option_id": "spire"}, follow_redirects=True)

    seen_labels = []

    def _narration_checks_status(prompt):
        seen_labels.append(ss.read_turn_status(carol_id, "new_babel"))
        return "Carol's narration.\n\n1. Wait.\n2. Go."

    def _state_update_checks_status(prompt):
        seen_labels.append(ss.read_turn_status(carol_id, "new_babel"))
        return dict(state_update)

    se.call_llm = _narration_checks_status
    se.call_llm_json = _state_update_checks_status
    resp = carol_client.post("/play/new_babel/api/turn", data={"action": "wait"})
    assert resp.status_code == 200
    assert seen_labels == ["Narrating", "Reckoning"], seen_labels
    print("OK: the status beacon shows 'Narrating' during the narration call and "
          "'Reckoning' during the following state-update call")
    se.call_llm = call_queue
    se.call_llm_json = json_queue

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

"""Regression test for app.py's web routes: login (with a backend-provisioned
account), the story picker, and the /play flow (name capture, then a turn),
exercised through Flask's real test client - not a hand-rolled stub, since
faking the routing/session/template-rendering machinery would mostly just be
testing the fake rather than the app. story_engine's LLM calls are still
monkeypatched (no network), and state_store's storage is redirected to a temp
directory (never the real stories/ or data/).

It also needs the private story submodule at stories/private/ checked out, and skips
gracefully when it isn't.

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
import time

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
    # /stories and /play are closed by default during the V3 overhaul (backend/app.py's
    # _close_play_during_overhaul) - this test exercises the real play flow, so it needs
    # the closure lifted, the same way it needs a real flask install.
    os.environ["PLAY_ENABLED"] = "1"

    ss = load_state_store(tmp_dir)

    # Seed the catalog with a copy of the real New Babel template - app.py's
    # routes exercise the full opening-scene/take_turn machinery, which needs
    # the real schema (plot.opening_scene, player.flags_active, etc.), not a
    # minimal stub template.
    # stories/private/ is a git submodule of private story content (see state_store's
    # STORIES_PRIVATE_DIR). Built from REPO_ROOT rather than ss.STORIES_PRIVATE_DIR, which
    # load_state_store has already redirected to this test's tmp dir. A public clone that
    # never ran `git submodule update --init` doesn't have it, so skip rather than fail -
    # same contract as the flask check above, for the same reason.
    real_template_path = os.path.join(REPO_ROOT, "stories", "private", "new_babel",
                                      "template.json")
    if not os.path.isfile(real_template_path):
        print("SKIPPED: stories/private/ (private story submodule) is not checked out - "
              "run `git submodule update --init` to run this test for real.")
        sys.exit(0)
    with open(real_template_path) as f:
        template = json.load(f)
    story_dir = os.path.join(ss.STORIES_DIR, "new_babel")
    os.makedirs(story_dir, exist_ok=True)
    with open(os.path.join(story_dir, "template.json"), "w") as f:
        json.dump(template, f)

    # A second, deliberately lint-failing story (no mechanics.endings at all, so L01/L08
    # fire) - used below to exercise the /stories lint gate for real (backend/app.py's
    # _story_blocked_by_lint). Kept minimal rather than a mutated copy of new_babel: this
    # test only needs *a* story that fails lint, not one that also carries the full opening-
    # scene/turn-taking schema new_babel needs.
    BROKEN_TEMPLATE = {
        "schema_version": 3, "story_version": "2024-01-01.1",
        "meta": {"title": "Broken Test Story"},
        "narration": {"pov": "second-person"},
        "world": {"setting_summary": "A test world.", "rules": ["Be nice."]},
        "protagonist": {"default_name": "Traveller"},
        "plot": {
            "main_thread": {"title": "Main", "description": "The main thread.", "acts": [
                {"act_number": 1, "title": "Act One", "description": "It begins."},
            ]},
            "pacing": {"nudge_frequency": 5, "act_check_frequency": 12},
            "initial_scene": {"location": "start", "summary": "The beginning."},
            "opening_scene": {"narration_before_name": "Before.", "narration_after_name": "After."},
        },
    }
    broken_dir = os.path.join(ss.STORIES_DIR, "broken_test_story")
    os.makedirs(broken_dir, exist_ok=True)
    with open(os.path.join(broken_dir, "template.json"), "w") as f:
        json.dump(BROKEN_TEMPLATE, f)

    import story_engine as se  # noqa: E402  (picks up the same stubbed state_store)
    import app as flask_app_module  # noqa: E402

    # This test otherwise exercises the real turn-taking flow against a real (private)
    # template - the committed new_babel template doesn't yet author mechanics.endings
    # (AUTHORING_TOOL_PHASES.md Phase S1 step 8 isn't done for any real story yet), so it
    # fails lint same as every other real story right now, and would otherwise vanish from
    # /stories and break the "lists the seeded catalog" and /play assertions below for a
    # reason unrelated to what this file is testing. broken_test_story above is deliberately
    # left un-patched, so the real gate (captured here before the override) still gets
    # exercised against it below.
    _real_story_blocked_by_lint = flask_app_module._story_blocked_by_lint
    flask_app_module._story_blocked_by_lint = (
        lambda slug: False if slug == "new_babel" else _real_story_blocked_by_lint(slug)
    )

    # call_llm handles narration (returns a string); call_llm_json handles the
    # separate state-update pass that follows every turn (returns a dict) - two
    # independent canned-response queues, one per function. One entry per
    # take_turn/regenerate_last_turn call made below: the initial action, the
    # regenerate, then two more turns to build up enough history to exercise
    # pagination (4 total turns, one more than INITIAL_TURNS_SHOWN).
    # Real "OPTIONS:"/"||" format (see parse_narration_and_options) - a canned narration
    # missing this would spuriously trigger story_engine.generate_missing_options's
    # follow-up call_llm call, consuming an extra entry from this same canned queue and
    # desyncing every take_turn call after it.
    # narration.option_count isn't set in new_babel's template, so parse_narration_and_options
    # falls back to its default of 3 - each canned narration below needs exactly 3
    # well-formed options to match, or it's treated as malformed too (see comment above).
    narrations = [
        "You step into the corridor. The city hums beyond.\n\n"
        "OPTIONS:\n1. Keep walking. || I keep walking.\n2. Stop and listen. || I stop and listen."
        "\n3. Turn back. || I turn back.",
        "A different corridor unfolds. Something else happens.\n\n"
        "OPTIONS:\n1. Go left. || I go left.\n2. Go right. || I go right."
        "\n3. Stay put. || I stay put.",
        "Second narration continues onward.\n\n"
        "OPTIONS:\n1. Push forward. || I push forward.\n2. Retreat. || I retreat."
        "\n3. Wait. || I wait.",
        "Third narration wraps the sequence.\n\n"
        "OPTIONS:\n1. Rest. || I rest.\n2. Move on. || I move on."
        "\n3. Look around. || I look around.",
    ]
    state_update = {"subplot_beats": {}, "flags_set": {}, "revelations": {"revealed": [], "eligible": []}, "entity_interaction": False}
    call_queue = CannedResponses(narrations)
    json_queue = CannedResponses([state_update] * len(narrations))
    se.call_llm = call_queue
    se.call_llm_json = json_queue

    alice_id = ss.create_account("alice", "correct-horse")

    app = flask_app_module.app
    app.testing = True
    client = app.test_client()

    def wait_for_idle(user_id, story_slug="new_babel", timeout=5.0):
        """/api/turn and /api/regenerate now kick off take_turn/regenerate_last_turn on a
        background thread and return immediately (see app.py's _start_turn_job) rather than
        blocking the request on the whole LLM pipeline - the real client polls GET
        /api/status until it goes idle again before fetching GET /api/turn/result, and a
        test exercising the real HTTP contract has to do the same rather than assuming the
        background thread has already finished by the time the kickoff POST returns."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if ss.read_turn_status(user_id, story_slug) is None:
                return
            time.sleep(0.01)
        raise AssertionError(
            f"turn for {user_id!r}/{story_slug!r} did not finish within {timeout}s"
        )

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

    # --- ...but omits a story that still fails lint (backend/app.py's _story_blocked_by_lint,
    # exercised for real here since new_babel above is the one with the monkeypatched bypass) --
    assert b"Broken Test Story" not in resp.data
    print("OK: /stories omits a story that still fails lint")

    # --- first visit to /play is the name-capture phase ---
    resp = client.get("/play/new_babel")
    assert resp.status_code == 200
    assert b'name="name"' in resp.data
    print("OK: first /play visit shows the name-entry form")

    # --- submitting a name applies it and flips into the first character-creation step
    # (new_babel authors a three-step character_creation list: gender, then class, then
    # starting_place - this is what exercises that generic mechanism, not an engine
    # default - the example story defines no steps and skips straight to play) ---
    resp = client.post("/play/new_babel", data={"name": "Vesper Kade"}, follow_redirects=True)
    assert resp.status_code == 200
    assert b"mandatory field" in resp.data
    assert b"Woman (she/her)" in resp.data
    ctx = ss.load_state(alice_id, "new_babel")
    assert ctx["state"]["protagonist"]["name"] == "Vesper Kade"
    assert ctx["state"]["plot"]["opening_played"] is True
    assert ctx["state"]["protagonist"]["creation_choices"] == {}
    print("OK: submitting a name applies it and moves the save into the first creation step")

    # --- an unrecognized option_id re-renders the current step with an error, doesn't
    # crash or silently proceed ---
    resp = client.post("/play/new_babel", data={"option_id": "not-a-real-option"})
    assert resp.status_code == 200
    assert b"mandatory field" in resp.data
    assert b"Please choose one" in resp.data
    ctx = ss.load_state(alice_id, "new_babel")
    assert ctx["state"]["protagonist"]["creation_choices"] == {}
    print("OK: an invalid option_id re-renders the current step with an error instead of proceeding")

    # --- gender is a flavor-only step (no starting_stats on any option), so it records the
    # pick and advances without seeding player.stats - the same optional-starting_stats
    # path starting_place relies on ---
    resp = client.post("/play/new_babel", data={"option_id": "woman"}, follow_redirects=True)
    assert resp.status_code == 200
    assert b"choose your approach" in resp.data.lower()
    assert b"The Ghost Runner" in resp.data
    ctx = ss.load_state(alice_id, "new_babel")
    assert ctx["state"]["protagonist"]["creation_choices"] == {"gender": "woman"}
    assert ctx["state"]["protagonist"]["stats"] == {}
    print("OK: a step whose options carry no starting_stats advances without seeding stats")

    # --- picking a real class option seeds player.stats and advances to the next step
    # (starting_place), rather than dropping straight into play ---
    resp = client.post("/play/new_babel", data={"option_id": "ghost_runner"}, follow_redirects=True)
    assert resp.status_code == 200
    assert b"where do you go first" in resp.data.lower()
    ctx = ss.load_state(alice_id, "new_babel")
    assert ctx["state"]["protagonist"]["creation_choices"] == {"gender": "woman", "class": "ghost_runner"}
    assert ctx["state"]["protagonist"]["stats"] == {"health": 90, "neural_load": 10, "attention_level": 0}
    print("OK: picking a class seeds player.stats and advances to the next creation step")

    # --- picking the final step's option completes character creation and flips into
    # normal play ---
    resp = client.post("/play/new_babel", data={"option_id": "drowned_quarter"}, follow_redirects=True)
    assert resp.status_code == 200
    assert b'name="action"' in resp.data
    ctx = ss.load_state(alice_id, "new_babel")
    assert ctx["state"]["protagonist"]["creation_choices"] == {
        "gender": "woman", "class": "ghost_runner", "starting_place": "drowned_quarter"}
    print("OK: completing the last creation step moves the save into normal play")

    # --- GET /api/status with no turn in flight reports nothing (no stale beacon lying
    # around from a previous request that never happened) ---
    resp = client.get("/play/new_babel/api/status")
    assert resp.status_code == 200
    assert resp.get_json() == {"label": None, "progress": None}
    print("OK: GET /api/status with no turn in flight reports label: None")

    # --- POST /api/turn kicks off take_turn on a background thread and returns almost
    # immediately (202, no body) rather than blocking on the whole LLM pipeline - the client
    # has to poll /api/status for completion, then fetch the outcome from
    # GET /api/turn/result, which is where the scene+controls htmx fragment now comes from ---
    resp = client.post("/play/new_babel/api/turn", data={"action": "look around"})
    assert resp.status_code == 202, resp.status_code
    wait_for_idle(alice_id)
    resp = client.get("/play/new_babel/api/turn/result")
    assert resp.status_code == 200
    assert b'class="scene-block"' in resp.data
    assert b'id="controls"' in resp.data and b'hx-swap-oob="true"' in resp.data
    ctx = ss.load_state(alice_id, "new_babel")
    assert ctx["state"]["pacing"]["turn_count"] == 1
    print("OK: POST /api/turn (async) advances the turn count and GET /api/turn/result "
          "returns a scene+controls fragment once it's done")

    # --- the status beacon is cleared once the turn completes (take_turn's finally),
    # not left showing a stale "Reckoning"/"Narrating" from the just-finished turn ---
    resp = client.get("/play/new_babel/api/status")
    assert resp.status_code == 200
    assert resp.get_json() == {"label": None, "progress": None}
    print("OK: GET /api/status is cleared back to None after the turn completes")

    # --- §7.4: a refused action returns 200 with OOB swaps only. No scene block, because no
    # turn happened - the result fetch targets #scene-list, so a body here would append the
    # refusal to the transcript as though the story had moved on. ---
    story = ss.thaw(ss.load_state(alice_id, "new_babel")["story"])
    story["mechanics"]["gate"] = {"engine": "precondition", "gates": [
        {"id": "vault", "target": "loc_vault", "requires": {"item_tag": "never_carried"},
         "refusal_hint": "The door does not argue."}]}
    real_load = ss.load_state
    ss.load_state = lambda u, s, *a, **k: dict(real_load(u, s, *a, **k), story=ss.freeze(story))
    turns_before = real_load(alice_id, "new_babel")["state"]["pacing"]["turn_count"]
    se.call_llm_json = CannedResponses([{"blocked": 1, "sentence": "The door does not move."}])

    resp = client.post("/play/new_babel/api/turn", data={"action": "I try the vault door"})
    assert resp.status_code == 202, resp.status_code
    wait_for_idle(alice_id)
    resp = client.get("/play/new_babel/api/turn/result")
    assert resp.status_code == 200, resp.status_code
    assert b'id="refusal-text"' in resp.data and b"The door does not move." in resp.data
    assert b'class="scene-block"' not in resp.data, \
        "a refusal must not append anything to the transcript"
    assert b'id="controls"' in resp.data and b'hx-swap-oob="true"' in resp.data, \
        "the controls have to come back, or the player is left with a disabled form"
    assert real_load(alice_id, "new_babel")["state"]["pacing"]["turn_count"] == turns_before, \
        "a refused action must not consume a turn"
    ss.load_state = real_load
    # Restore the shared queue: the refusal above swapped in a single-response stub, and the
    # tests below still need the normal state-update answer for every turn they take.
    se.call_llm_json = CannedResponses([state_update] * 10)
    print("OK: a refused action returns the refusal fragment, appends no scene, and "
          "consumes no turn")

    # --- a turn already in flight for this save makes /api/turn and /api/regenerate refuse
    # to start a second one, rather than racing it - the turn-status beacon doubles as a
    # cheap in-flight lock, not just a display hint for the busy indicator. Simulated
    # directly via the beacon rather than a real concurrent request - Flask's test client
    # runs requests synchronously, so two genuinely overlapping requests aren't reachable
    # here; this exercises the same check the routes make either way. ---
    ss.write_turn_status(alice_id, "new_babel", "narration")
    resp = client.post("/play/new_babel/api/turn", data={"action": "look around"})
    assert resp.status_code == 409, resp.status_code
    resp = client.post("/play/new_babel/api/regenerate")
    assert resp.status_code == 409, resp.status_code
    resp = client.get("/play/new_babel/api/turn/result")
    assert resp.status_code == 409, resp.status_code
    ss.clear_turn_status(alice_id, "new_babel")
    print("OK: a turn already in flight makes /api/turn, /api/regenerate, and "
          "/api/turn/result all return 409 instead of racing/misreading a second call")

    # --- POST /api/regenerate replaces the last turn instead of appending ---
    turns_before = len(ctx["state"]["history"]["recent_turns"])
    resp = client.post("/play/new_babel/api/regenerate")
    assert resp.status_code == 202, resp.status_code
    wait_for_idle(alice_id)
    resp = client.get("/play/new_babel/api/turn/result")
    assert resp.status_code == 200
    assert b"different corridor" in resp.data
    ctx = ss.load_state(alice_id, "new_babel")
    assert len(ctx["state"]["history"]["recent_turns"]) == turns_before
    print("OK: POST /api/regenerate re-rolls the last turn without growing recent_turns")

    # --- a blank action is a no-op (matches the textarea's required attribute) - still
    # synchronous, since take_turn's own emptiness check runs before _start_turn_job would
    # ever spawn a background thread ---
    resp = client.post("/play/new_babel/api/turn", data={"action": "   "})
    assert resp.status_code == 200
    assert resp.data == b""
    ctx = ss.load_state(alice_id, "new_babel")
    assert len(ctx["state"]["history"]["recent_turns"]) == turns_before
    print("OK: POST /api/turn with a blank action is a synchronous no-op")

    # --- two more turns build up enough history to exercise pagination ---
    resp = client.post("/play/new_babel/api/turn", data={"action": "push forward"})
    assert resp.status_code == 202, resp.status_code
    wait_for_idle(alice_id)
    resp = client.get("/play/new_babel/api/turn/result")
    assert resp.status_code == 200
    resp = client.post("/play/new_babel/api/turn", data={"action": "rest a moment"})
    assert resp.status_code == 202, resp.status_code
    wait_for_idle(alice_id)
    resp = client.get("/play/new_babel/api/turn/result")
    assert resp.status_code == 200
    ctx = ss.load_state(alice_id, "new_babel")
    all_turns = ctx["state"]["history"].get("full_transcript", []) + ctx["state"]["history"]["recent_turns"]
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
        seen_labels.append(ss.read_turn_status(carol_id, "new_babel")["label_key"])
        # Well-formed OPTIONS block (see the comment on `narrations` above) - malformed
        # options here would spuriously trigger generate_missing_options's follow-up
        # call_llm call, adding an extra "options_generation" entry to seen_labels below.
        return ("Carol's narration.\n\nOPTIONS:\n1. Wait. || I wait.\n2. Go. || I go."
                "\n3. Look. || I look.")

    def _state_update_checks_status(prompt):
        seen_labels.append(ss.read_turn_status(carol_id, "new_babel")["label_key"])
        return dict(state_update)

    se.call_llm = _narration_checks_status
    se.call_llm_json = _state_update_checks_status
    resp = carol_client.post("/play/new_babel/api/turn", data={"action": "wait"})
    assert resp.status_code == 202, resp.status_code
    wait_for_idle(carol_id)
    resp = carol_client.get("/play/new_babel/api/turn/result")
    assert resp.status_code == 200
    # `gate_check` leads because new_babel authors a gate whose predicate is unmet for this
    # save - the pre-action detector (§7.4) runs before narration and writes its own beacon.
    # It is in the sequence rather than stripped from it deliberately: the beacon order IS the
    # thing under test, and a story with gates genuinely has three steps, not two.
    assert seen_labels == ["gate_check", "narration", "state_update"], seen_labels
    print("OK: the status beacon shows 'gate_check', then 'narration' during the narration "
          "call, then 'state_update' during the following state-update call")
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

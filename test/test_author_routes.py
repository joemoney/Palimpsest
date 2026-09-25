"""Regression test for the /author routes (AUTHORING_TOOL_PHASES.md Phase S1), exercised
through Flask's real test client - same rationale and pattern as test_app_routes.py: state
storage redirected to a temp dir, no network, no LLM calls (this module makes none).

NOTE: like test_app_routes.py, this needs the real `flask` package installed and skips
gracefully (exit 0) when it isn't.

Run directly: python3 test/test_author_routes.py
"""
import copy
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

# Same contract as above, for jsonschema: L01 needs Draft 2020-12 (jsonschema>=4.18 -
# CLAUDE.md Phase 0), and this repo's own local environment has 3.2.0.
import jsonschema  # noqa: E402
if not hasattr(jsonschema, "Draft202012Validator"):
    print(f"SKIPPED: jsonschema {jsonschema.__version__} has no Draft202012Validator - "
          "needs jsonschema>=4.18 (`pip install -r requirements.txt`) to run for real.")
    sys.exit(0)

# A minimal, schema-and-lint-clean template (one catch-all destination ending, no subplots) -
# used for the "does a save actually succeed and persist" checks, since none of the real
# stories/fixtures author mechanics.endings yet (Phase S1 step 8, not done) and so all of them
# always carry at least the "no catch-all" lint error.
CLEAN_TEMPLATE = {
    "schema_version": 3,
    "story_version": "2024-01-01.1",
    "meta": {"title": "Author Route Test Story"},
    "narration": {"pov": "second-person"},
    "world": {"setting_summary": "A test world.", "rules": ["Be nice."]},
    "protagonist": {"default_name": "Traveller"},
    "mechanics": {
        "endings": {"engine": "ending_funnel", "entries": [
            {"id": "the_end", "kind": "destination", "name": "The End"},
        ]},
    },
    "plot": {
        "main_thread": {"title": "Main", "description": "The main thread.", "acts": [
            {"act_number": 1, "title": "Act One", "description": "It begins."},
        ]},
        "pacing": {"nudge_frequency": 5, "act_check_frequency": 12},
        "initial_scene": {"location": "start", "summary": "The beginning."},
        "opening_scene": {"narration_before_name": "Before.", "narration_after_name": "After."},
    },
}

tmp_dir = tempfile.mkdtemp(prefix="cyoa_author_routes_test_")
try:
    os.environ.setdefault("GOOGLE_API_KEY", "test-key")
    os.environ.setdefault("FLASK_SECRET_KEY", "test-secret")

    ss = load_state_store(tmp_dir)

    example_path = os.path.join(REPO_ROOT, "stories", "example", "template.json")
    with open(example_path, encoding="utf-8") as f:
        example_template = json.load(f)
    example_dir = os.path.join(ss.STORIES_DIR, "example")
    os.makedirs(example_dir, exist_ok=True)
    with open(os.path.join(example_dir, "template.json"), "w", encoding="utf-8") as f:
        json.dump(example_template, f)

    clean_dir = os.path.join(ss.STORIES_DIR, "author_test_story")
    os.makedirs(clean_dir, exist_ok=True)
    with open(os.path.join(clean_dir, "template.json"), "w", encoding="utf-8") as f:
        json.dump(CLEAN_TEMPLATE, f)
    with open(os.path.join(clean_dir, "README.md"), "w", encoding="utf-8") as f:
        f.write("# Author Route Test Story\n\n## Synopsis\n\nStale pitch, not yet synced.\n")

    import author_assist  # noqa: E402
    import author_model  # noqa: E402  (picks up the same stubbed state_store path setup)
    import app as flask_app_module  # noqa: E402

    app = flask_app_module.app
    app.testing = True

    alice_id = ss.create_account("alice", "correct-horse")

    def login(client, username="alice", password="correct-horse"):
        # Not follow_redirects=True: a successful login redirects to /stories, which walks
        # every story in the catalog through load_template() for its save-stats card - and
        # author_test_story below deliberately authors mechanics.endings.engine:
        # "ending_funnel" (D1: the board writes CR-05 content from day one, even though no
        # engine reads it until S5), which makes load_template() raise UnknownEngineError by
        # design. That's a real, separate interaction worth flagging on its own (any
        # author-tool-enabled story authoring CR-05/CR-10 content before S5 ships the engine
        # currently breaks /stories for every user, not just this one) - not something to
        # paper over here by following the redirect.
        resp = client.post("/login", data={"username": username, "password": password})
        assert resp.status_code == 302, resp.status_code
        return client

    # --- gated off entirely when AUTHOR_ENABLED isn't set -------------------------------------
    os.environ.pop("AUTHOR_ENABLED", None)
    os.environ.pop("AUTHOR_USER_IDS", None)
    client = login(app.test_client())
    resp = client.get("/author")
    assert resp.status_code == 404, resp.status_code
    print("OK: /author 404s when AUTHOR_ENABLED is unset")

    # --- gated off for a user not named in AUTHOR_USER_IDS, even with AUTHOR_ENABLED=1 --------
    os.environ["AUTHOR_ENABLED"] = "1"
    os.environ["AUTHOR_USER_IDS"] = "someone-else"
    resp = client.get("/author")
    assert resp.status_code == 404, resp.status_code
    resp = client.get("/author/example/board")
    assert resp.status_code == 404, resp.status_code
    print("OK: /author 404s for a user not listed in AUTHOR_USER_IDS")

    # --- enabled and allow-listed: the real checks -----------------------------------------
    os.environ["AUTHOR_USER_IDS"] = f"someone-else,{alice_id}"

    resp = client.get("/author")
    assert resp.status_code == 200
    assert b"Author Route Test Story" in resp.data or b"author_test_story" in resp.data
    print("OK: /author catalog lists seeded stories")

    resp = client.get("/author/example/board")
    assert resp.status_code == 200
    assert b"board-data" in resp.data
    assert b"getBoardModel" in resp.data
    print("OK: /author/<slug>/board renders with the seeded model")

    resp = client.get("/author/does-not-exist/board")
    assert resp.status_code == 404
    print("OK: /author/<slug>/board 404s for an unknown slug")

    # --- AI assist (§7 "Ending -> waypoints"): offline, genai.GenerativeModel monkeypatched --
    clean_model_for_assist = author_model.to_board_model(ss.load_template_raw("author_test_story"))
    author_assist.GOOGLE_API_KEY = "test-key"

    class _FakeAssistResponse:
        text = json.dumps(
            {"waypoints": [{"id": "new_wp", "plant": "a stranger asks the wrong question",
                            "detect": "someone asks about the operator by name"}]}
        )

    class _FakeAssistModel:
        def __init__(self, *a, **k):
            pass

        def generate_content(self, prompt, request_options=None):
            return _FakeAssistResponse()

    author_assist.genai.GenerativeModel = _FakeAssistModel
    resp = client.post("/author/author_test_story/assist", data={
        "model": json.dumps(clean_model_for_assist), "ending_id": "the_end",
    })
    assert resp.status_code == 200
    assert b"new_wp" in resp.data
    assert b"a stranger asks the wrong question" in resp.data
    assert b"data-accept-waypoint" in resp.data
    print("OK: /author/<slug>/assist returns suggested waypoints for a destination ending")

    resp = client.post("/author/author_test_story/assist", data={
        "model": json.dumps(clean_model_for_assist), "ending_id": "does-not-exist",
    })
    assert resp.status_code == 200
    assert b"No such destination ending" in resp.data
    print("OK: /author/<slug>/assist reports an error for an unknown ending id")

    author_assist.GOOGLE_API_KEY = ""
    resp = client.post("/author/author_test_story/assist", data={
        "model": json.dumps(clean_model_for_assist), "ending_id": "the_end",
    })
    assert resp.status_code == 200
    assert b"GOOGLE_API_KEY" in resp.data
    print("OK: /author/<slug>/assist reports a clear error with no API key configured")

    # --- validate against the real, unmodified example story: known lint errors surface, but
    # Confirm & Save is still offered - Save always writes the full template now, lint errors
    # or not (see backend/app.py's _author_validate_response); what lint errors gate is
    # whether the story is listed to players (_story_blocked_by_lint / /stories), not whether
    # the author can keep saving their edits ------------------------------------------------
    raw = ss.load_template_raw("example")
    model = author_model.to_board_model(raw)
    resp = client.post("/author/example/api/validate", data={"model": json.dumps(model)})
    assert resp.status_code == 200
    assert b"No catch-all ending" in resp.data
    assert b"Confirm" in resp.data
    print("OK: validating the unmodified example story surfaces its known lint errors, without blocking Confirm & Save")

    # --- a clean, schema-and-lint-valid template validates with nothing blocking -------------
    clean_raw = ss.load_template_raw("author_test_story")
    clean_model = author_model.to_board_model(clean_raw)
    resp = client.post("/author/author_test_story/api/validate", data={"model": json.dumps(clean_model)})
    assert resp.status_code == 200
    assert b"No catch-all ending" not in resp.data
    assert b"Confirm" in resp.data
    print("OK: a clean template validates with no blocking errors and offers Confirm & Save")

    # --- save actually writes the file and bumps story_version -------------------------------
    before_version = ss.load_template_raw("author_test_story")["story_version"]
    resp = client.post("/author/author_test_story/api/save", data={"model": json.dumps(clean_model)})
    assert resp.status_code == 200
    assert b"Saved." in resp.data
    after = ss.load_template_raw("author_test_story")
    assert after["story_version"] != before_version, (before_version, after["story_version"])
    assert after["schema_version"] == author_model.TEMPLATE_SCHEMA_VERSION
    print("OK: /api/save writes the file and bumps story_version")

    # --- /api/evaluate: the real conditions.evaluate against an author-typed sample state ----
    ev_raw = ss.load_template_raw("author_test_story")
    ev_model = author_model.to_board_model(ev_raw)
    resp = client.post("/author/author_test_story/api/evaluate",
                       data={"model": json.dumps(ev_model), "sample": json.dumps({"stats": {"x": 1}})})
    assert resp.status_code == 200, resp.status_code
    assert b"Conditions against the sample state" in resp.data
    assert b"ending_funnel" in resp.data, "an unbuilt engine must be named, not swallowed"
    # D4: the stat sidebar's data rides an HX-Trigger payload, not the fragment. This story
    # has no stats block, so the payload is present but empty.
    assert json.loads(resp.headers["HX-Trigger"]) == {"author-stat-tiers": {}}, resp.headers.get("HX-Trigger")
    print("OK: /api/evaluate renders the condition table and names engines this build lacks")

    resp = client.post("/author/author_test_story/api/evaluate", data={"model": "not json", "sample": "{}"})
    assert resp.status_code == 200 and b"Could not read the board state" in resp.data
    resp = client.post("/author/author_test_story/api/evaluate",
                       data={"model": json.dumps(ev_model), "sample": "[[["})
    assert resp.status_code == 200 and b"Could not read the board state" in resp.data
    print("OK: /api/evaluate reports an unreadable model or sample instead of erroring")

    os.environ["AUTHOR_USER_IDS"] = "someone-else"
    resp = client.post("/author/author_test_story/api/evaluate", data={"model": "{}", "sample": "{}"})
    assert resp.status_code == 404, resp.status_code
    os.environ["AUTHOR_USER_IDS"] = alice_id
    print("OK: /api/evaluate 404s for a non-author account")

    # --- a successful save also resyncs the story's README, if it has one --------------------
    with open(os.path.join(clean_dir, "README.md"), encoding="utf-8") as f:
        readme_after = f.read()
    assert "Stale pitch, not yet synced." not in readme_after
    assert after["meta"]["title"] in readme_after or "Author Route Test Story" in readme_after
    print("OK: /api/save resyncs the story's README Synopsis section")

    # --- saving with a blocking error still writes the full edited content, not just layout --
    # (an author mid-edit - adding/removing threads and endings, rewiring connections - needs
    # every Save to land on disk; lint errors only gate whether the story is listed to
    # players, never the author's own ability to keep working. See _author_validate_response.)
    before_version = ss.load_template_raw("example")["story_version"]
    moved_model = json.loads(json.dumps(model))  # deep copy
    moved_model["nodes"][0]["x"] = 999
    moved_model["nodes"][0]["y"] = 888
    resp = client.post("/author/example/api/save", data={"model": json.dumps(moved_model)})
    assert resp.status_code == 200
    assert b"Saved." in resp.data
    assert b"No catch-all ending" in resp.data
    assert b"until" in resp.data  # "not shown in the player list until..." note
    after = ss.load_template_raw("example")
    assert after["story_version"] != before_version
    assert after["_storyboard"]["positions"][moved_model["nodes"][0]["id"]] == {"x": 999, "y": 888}
    print("OK: /api/save writes the full edited template even when content lint errors remain")

    # --- the raw JSON escape hatch: GET shows canonical text, POST round-trips through the
    # same validate pipeline -------------------------------------------------------------------
    resp = client.get("/author/author_test_story/raw")
    assert resp.status_code == 200
    # The textarea is Jinja-autoescaped (correctly - it's the save-back escape hatch, and
    # autoescaping is what stops authored content from breaking out of the tag), so the
    # quotes around the JSON string value are HTML entities here, not literal `"`.
    assert b"Author Route Test Story" in resp.data
    print("OK: GET /author/<slug>/raw shows the canonical JSON")

    clean_after = ss.load_template_raw("author_test_story")
    unchanged_text = json.dumps(clean_after, indent=2, ensure_ascii=False) + "\n"
    before_version = clean_after["story_version"]
    resp = client.post("/author/author_test_story/raw", data={"raw": unchanged_text})
    assert resp.status_code == 200
    assert b"Saved." in resp.data
    reloaded = ss.load_template_raw("author_test_story")
    assert reloaded["story_version"] != before_version
    print("OK: POST /author/<slug>/raw validates and saves an unchanged round trip")

    resp = client.post("/author/author_test_story/raw", data={"raw": "not json at all"})
    assert resp.status_code == 200
    assert b"Not valid JSON" in resp.data
    print("OK: POST /author/<slug>/raw rejects unparseable JSON without crashing")

    # --- a second, non-listed user is refused even while logged in ---------------------------
    ss.create_account("bob", "hunter2")
    bob_client = login(app.test_client(), "bob", "hunter2")
    resp = bob_client.get("/author")
    assert resp.status_code == 404
    print("OK: a logged-in user not in AUTHOR_USER_IDS still gets 404")

    print("\nALL CHECKS PASSED: test_author_routes")
finally:
    os.environ.pop("AUTHOR_ENABLED", None)
    os.environ.pop("AUTHOR_USER_IDS", None)
    shutil.rmtree(tmp_dir, ignore_errors=True)

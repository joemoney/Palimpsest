"""Regression test for state_store.py: the storage layer behind multi-user,
multi-story play, and the schema v2 story/state split (see docs/SCHEMA_V2_SPEC.md).
Runs against a temp directory (never the real stories/ or data/), with
filelock/werkzeug.security stubbed per _llm_stubs (no pip-installed dependencies
required).

Covers the two things this whole architecture exists for:
- multi-user isolation: two different user_ids playing the same story_slug get
  independent save files and independent mutations.
- multi-story scalability: dropping a second template.json into the catalog
  makes list_stories() pick it up with zero code changes.

Plus the accounts path: username uniqueness is enforced, and login only
succeeds with the right username *and* password. Plus the story/state split
itself: ctx["story"] is frozen and re-read fresh every load, ctx["state"] is
the only thing ever persisted.

Run directly: python3 test/test_state_store.py
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import load_state_store  # noqa: E402

MINIMAL_TEMPLATE = {
    "schema_version": 2,
    "story_version": "test.1",
    "meta": {"title": "Story A", "genre": "test genre"},
    "narration": {},
    "world": {"rules": []},
    "protagonist": {},
    "mechanics": {},
    "plot": {
        "main_thread": {"title": "t", "description": "d", "acts": [
            {"act_number": 1, "title": "Act 1", "description": "d", "completion_signals": []}
        ]},
        "subplots": {},
        "pacing": {"nudge_frequency": 8, "act_check_frequency": 12, "max_parallel_subplots": 3},
        "opening_scene": {"narration_before_name": "", "narration_after_name": ""},
        "initial_scene": {"location": "", "summary": ""},
    },
}

tmp_dir = tempfile.mkdtemp(prefix="cyoa_state_store_test_")
try:
    ss = load_state_store(tmp_dir)

    # --- seed a minimal v2 story template for this test (not the real templates) ---
    story_a_dir = os.path.join(ss.STORIES_DIR, "story_a")
    os.makedirs(story_a_dir, exist_ok=True)
    with open(os.path.join(story_a_dir, "template.json"), "w") as f:
        json.dump(MINIMAL_TEMPLATE, f)

    # --- catalog: exactly one story so far ---
    stories = ss.list_stories()
    assert [s["slug"] for s in stories] == ["story_a"], stories
    assert stories[0]["title"] == "Story A"
    print("OK: list_stories() reflects the current template catalog")

    # --- the story/state split: ctx["story"] is frozen, ctx["state"] is mutable ---
    ctx_alice = ss.load_state("alice", "story_a")
    assert set(ctx_alice.keys()) == {"story", "state"}
    try:
        ctx_alice["story"]["meta"]["title"] = "hacked"
        assert False, "expected writing into ctx['story'] to raise"
    except TypeError:
        pass
    ctx_alice["state"]["counter"] = 0  # arbitrary test-only runtime field
    print("OK: ctx['story'] raises on write; ctx['state'] is a plain mutable dict")

    # --- multi-user isolation ---
    ctx_bob = ss.load_state("bob", "story_a")
    assert ctx_bob["state"].get("counter", 0) == 0

    ctx_alice["state"]["counter"] = 42
    ss.save_state(ctx_alice, "alice", "story_a")

    # bob's independently-loaded copy must be untouched by alice's save
    ctx_bob_reloaded = ss.load_state("bob", "story_a")
    assert ctx_bob_reloaded["state"].get("counter", 0) == 0, "bob's save was affected by alice's write"

    # alice's own save persisted correctly
    ctx_alice_reloaded = ss.load_state("alice", "story_a")
    assert ctx_alice_reloaded["state"]["counter"] == 42

    alice_path = os.path.join(ss.SAVES_DIR, "alice", "story_a.json")
    bob_path = os.path.join(ss.SAVES_DIR, "bob", "story_a.json")
    assert os.path.isfile(alice_path) and os.path.isfile(bob_path)
    assert alice_path != bob_path
    print("OK: two users playing the same story get independent save files and mutations")

    # --- ctx["story"] is re-read fresh from the template every load, never cached ---
    with open(os.path.join(story_a_dir, "template.json"), "w") as f:
        json.dump({**MINIMAL_TEMPLATE, "meta": {**MINIMAL_TEMPLATE["meta"], "title": "Story A (revised)"}}, f)
    ctx_alice_after_edit = ss.load_state("alice", "story_a")
    assert ctx_alice_after_edit["story"]["meta"]["title"] == "Story A (revised)"
    assert ctx_alice_after_edit["state"]["counter"] == 42, "the runtime save must be untouched by a template edit"
    print("OK: a template edit reaches an existing save's ctx['story'] without touching ctx['state']")
    # restore for the rest of this test
    with open(os.path.join(story_a_dir, "template.json"), "w") as f:
        json.dump(MINIMAL_TEMPLATE, f)

    # --- multi-story scalability: add a second template, zero code changes ---
    story_b_dir = os.path.join(ss.STORIES_DIR, "story_b")
    os.makedirs(story_b_dir, exist_ok=True)
    with open(os.path.join(story_b_dir, "template.json"), "w") as f:
        json.dump({**MINIMAL_TEMPLATE, "meta": {"title": "Story B", "genre": "another genre"}}, f)

    stories = ss.list_stories()
    assert sorted(s["slug"] for s in stories) == ["story_a", "story_b"], stories
    print("OK: dropping in a new template.json is enough for list_stories() to pick it up")

    # a user can independently play both stories
    ctx_alice_b = ss.load_state("alice", "story_b")
    assert "counter" not in ctx_alice_b["state"]  # story_b's save never had one
    assert ss.load_state("alice", "story_a")["state"]["counter"] == 42  # story_a untouched by story_b access
    print("OK: one user can have independent saves across multiple stories")

    # --- accounts: uniqueness + real credential checking ---
    user_id = ss.create_account("alice", "correct-horse")
    assert user_id
    try:
        ss.create_account("alice", "different-password")
        assert False, "expected a duplicate username to raise"
    except ValueError:
        pass
    print("OK: duplicate usernames are rejected")

    assert ss.verify_login("alice", "correct-horse") == user_id
    assert ss.verify_login("alice", "wrong-password") is None
    assert ss.verify_login("nobody", "correct-horse") is None
    print("OK: verify_login succeeds only with the right username and password")

    print("\nALL CHECKS PASSED: test_state_store")
finally:
    shutil.rmtree(tmp_dir, ignore_errors=True)

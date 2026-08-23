"""Regression test for state_store.py: the storage layer behind multi-user,
multi-story play. Runs against a temp directory (never the real stories/ or
data/), with filelock/werkzeug.security stubbed per _llm_stubs (no pip-installed
dependencies required).

Covers the two things this whole architecture exists for:
- multi-user isolation: two different user_ids playing the same story_slug get
  independent save files and independent mutations.
- multi-story scalability: dropping a second template.json into the catalog
  makes list_stories() pick it up with zero code changes.

Plus the accounts path: username uniqueness is enforced, and login only
succeeds with the right username *and* password.

Run directly: python3 test/test_state_store.py
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import load_state_store  # noqa: E402

tmp_dir = tempfile.mkdtemp(prefix="cyoa_state_store_test_")
try:
    ss = load_state_store(tmp_dir)

    # --- seed a minimal story template for this test (not the real New Babel one) ---
    story_a_dir = os.path.join(ss.STORIES_DIR, "story_a")
    os.makedirs(story_a_dir, exist_ok=True)
    with open(os.path.join(story_a_dir, "template.json"), "w") as f:
        json.dump(
            {
                "meta": {"title": "Story A", "genre": "test genre"},
                "plot": {"pacing": {"turn_count": 0}},
                "counter": 0,
            },
            f,
        )

    # --- catalog: exactly one story so far ---
    stories = ss.list_stories()
    assert [s["slug"] for s in stories] == ["story_a"], stories
    assert stories[0]["title"] == "Story A"
    print("OK: list_stories() reflects the current template catalog")

    # --- multi-user isolation ---
    state_alice = ss.load_state("alice", "story_a")
    state_bob = ss.load_state("bob", "story_a")
    assert state_alice["counter"] == 0 and state_bob["counter"] == 0

    state_alice["counter"] = 42
    ss.save_state(state_alice, "alice", "story_a")

    # bob's independently-loaded copy must be untouched by alice's save
    state_bob_reloaded = ss.load_state("bob", "story_a")
    assert state_bob_reloaded["counter"] == 0, "bob's save was affected by alice's write"

    # alice's own save persisted correctly
    state_alice_reloaded = ss.load_state("alice", "story_a")
    assert state_alice_reloaded["counter"] == 42

    alice_path = os.path.join(ss.SAVES_DIR, "alice", "story_a.json")
    bob_path = os.path.join(ss.SAVES_DIR, "bob", "story_a.json")
    assert os.path.isfile(alice_path) and os.path.isfile(bob_path)
    assert alice_path != bob_path
    print("OK: two users playing the same story get independent save files and mutations")

    # --- multi-story scalability: add a second template, zero code changes ---
    story_b_dir = os.path.join(ss.STORIES_DIR, "story_b")
    os.makedirs(story_b_dir, exist_ok=True)
    with open(os.path.join(story_b_dir, "template.json"), "w") as f:
        json.dump({"meta": {"title": "Story B", "genre": "another genre"}}, f)

    stories = ss.list_stories()
    assert sorted(s["slug"] for s in stories) == ["story_a", "story_b"], stories
    print("OK: dropping in a new template.json is enough for list_stories() to pick it up")

    # a user can independently play both stories
    state_alice_b = ss.load_state("alice", "story_b")
    assert "counter" not in state_alice_b  # story_b's template never had one
    assert ss.load_state("alice", "story_a")["counter"] == 42  # story_a untouched by story_b access
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

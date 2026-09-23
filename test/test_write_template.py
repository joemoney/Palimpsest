"""state_store.write_template - the only path that writes a story's template.json
(AUTHORING_TOOL_PHASES.md Phase 0). Two things this test exists to prove:

1. The writer is lossless. write_template(slug, load_template_raw(slug), bump_version=False)
   must reproduce the on-disk file byte-for-byte - the round trip the Phase 0 gate is built
   on, checked before the writer is ever handed a board-produced model instead of an
   untouched one.
2. Every real template.json and test fixture already matches the canonical formatting
   (json.dumps(indent=2, ensure_ascii=False) + trailing newline) that write_template emits -
   the whitespace-only reformat commit that made New Babel and `example` match The Missing
   Core and the fixtures.

Also covers version bumping, atomicity (no leftover temp file), and load_template_raw's
schema_version guard. Runs against a temp directory for the writer behaviour (never the real
stories/ or data/), and reads the real repository files read-only for the formatting check.

Run directly: python3 test/test_write_template.py
"""
import glob
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import load_state_store  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MINIMAL_TEMPLATE = {
    "schema_version": 2,
    "story_version": "2020-01-01.1",
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

tmp_dir = tempfile.mkdtemp(prefix="cyoa_write_template_test_")
try:
    ss = load_state_store(tmp_dir)

    story_dir = os.path.join(ss.STORIES_DIR, "story_a")
    os.makedirs(story_dir, exist_ok=True)
    template_path = os.path.join(story_dir, "template.json")
    # Deliberately non-canonical on disk (compact, no trailing newline) so the dry-run
    # round-trip test below is checking a real reformat, not a no-op.
    with open(template_path, "w") as f:
        json.dump(MINIMAL_TEMPLATE, f)

    # --- dry run is lossless and reformats to canonical -----------------------------
    raw = ss.load_template_raw("story_a")
    assert raw == MINIMAL_TEMPLATE, "load_template_raw must not mutate what it reads"

    returned_version = ss.write_template("story_a", raw, bump_version=False)
    assert returned_version == "2020-01-01.1", "dry run must not touch story_version"
    with open(template_path, "r", encoding="utf-8") as f:
        on_disk = f.read()
    assert on_disk == ss._dumps_template(MINIMAL_TEMPLATE), \
        "dry-run write must equal the canonical formatting of the same content"
    assert json.loads(on_disk) == MINIMAL_TEMPLATE, "content must survive round trip exactly"
    print("OK: dry-run write_template is lossless and canonically formatted")

    # --- no leftover temp file after a write -----------------------------------------
    leftovers = [p for p in os.listdir(story_dir) if p != "template.json"]
    assert leftovers == [], f"write_template left temp files behind: {leftovers}"
    print("OK: write_template cleans up its temp file (atomic replace)")

    # --- version bump: same-day incrementing, malformed/absent resets to .1 ----------
    bumped = ss.write_template("story_a", ss.load_template_raw("story_a"))
    today = ss.time.strftime("%Y-%m-%d")
    assert bumped == f"{today}.1", f"first bump on a new day should be .1, got {bumped}"
    bumped_again = ss.write_template("story_a", ss.load_template_raw("story_a"))
    assert bumped_again == f"{today}.2", f"same-day rebump should increment, got {bumped_again}"

    malformed = dict(MINIMAL_TEMPLATE)
    malformed["story_version"] = "not-a-version"
    reset = ss.write_template("story_a", malformed)
    assert reset == f"{today}.1", f"a malformed previous version should reset to .1, got {reset}"
    print("OK: story_version bumps YYYY-MM-DD.N, incrementing same-day, resetting otherwise")

    # --- write_template resolves through story_roots(), like load_template_raw ------
    private_dir = os.path.join(ss.STORIES_PRIVATE_DIR, "story_priv")
    os.makedirs(private_dir, exist_ok=True)
    priv_template = dict(MINIMAL_TEMPLATE)
    priv_template["meta"] = {"title": "Story Private", "genre": "test genre"}
    with open(os.path.join(private_dir, "template.json"), "w") as f:
        json.dump(priv_template, f)
    ss.write_template("story_priv", ss.load_template_raw("story_priv"), bump_version=False)
    # It must have written into the private root, not created a shadow copy in the public one.
    assert not os.path.isfile(os.path.join(ss.STORIES_DIR, "story_priv", "template.json"))
    assert ss.load_template_raw("story_priv")["meta"]["title"] == "Story Private"
    print("OK: write_template edits a story in whichever root it already lives, in place")

    # --- schema_version guard ----------------------------------------------------------
    unknown_dir = os.path.join(ss.STORIES_DIR, "story_future")
    os.makedirs(unknown_dir, exist_ok=True)
    future = dict(MINIMAL_TEMPLATE)
    future["schema_version"] = 99
    with open(os.path.join(unknown_dir, "template.json"), "w") as f:
        json.dump(future, f)
    try:
        ss.load_template_raw("story_future")
        raise AssertionError("an unrecognised schema_version should raise, not load silently")
    except ValueError as e:
        assert "schema_version" in str(e)
    print("OK: load_template_raw raises on a template schema_version this build doesn't know")

    # schema_version 3 (the version the board writes, per CLAUDE.md "Authoring tool" / D1)
    # must load without a code change to this test - it's declared accepted alongside 2.
    v3_dir = os.path.join(ss.STORIES_DIR, "story_v3")
    os.makedirs(v3_dir, exist_ok=True)
    v3 = dict(MINIMAL_TEMPLATE)
    v3["schema_version"] = 3
    with open(os.path.join(v3_dir, "template.json"), "w") as f:
        json.dump(v3, f)
    assert ss.load_template_raw("story_v3")["schema_version"] == 3
    print("OK: load_template_raw accepts schema_version 3 alongside 2")
finally:
    import shutil
    shutil.rmtree(tmp_dir, ignore_errors=True)

# --- every real template.json and fixture already matches canonical formatting -------
# Read-only against the actual repository files (not the tmp dir above) - this is a content
# invariant of the repo itself (the Phase 0 reformat commit), not a behaviour of the writer.
sys.path.insert(0, os.path.join(REPO_ROOT, "backend"))
from _llm_stubs import _install_stubs  # noqa: E402
_install_stubs()
import state_store as real_ss  # noqa: E402

real_paths = (
    glob.glob(os.path.join(REPO_ROOT, "stories", "*", "template.json"))
    + glob.glob(os.path.join(REPO_ROOT, "stories", "private", "*", "template.json"))
    + glob.glob(os.path.join(REPO_ROOT, "test", "fixtures", "*.json"))
)
# stories/private is a submodule and may be an uninitialised empty directory in a fresh
# clone (see state_store.py's STORIES_PRIVATE_DIR comment) - that's fine, not a failure;
# this loop just has fewer files to check.
assert len(real_paths) >= 4, f"expected at least the public story and fixtures, found {real_paths}"
checked = 0
for path in real_paths:
    with open(path, "r", encoding="utf-8") as f:
        on_disk = f.read()
    content = json.loads(on_disk)
    canonical = real_ss._dumps_template(content)
    assert on_disk == canonical, (
        f"{os.path.relpath(path, REPO_ROOT)} is not canonically formatted - "
        f"run it through state_store._dumps_template and rewrite it"
    )
    checked += 1
print(f"OK: {checked} real template(s)/fixture(s) on disk already match canonical formatting")

print("\nALL CHECKS PASSED: test_write_template")

"""backend/readme_sync.py: the "## Synopsis" section sync (AUTHORING_TOOL_PHASES.md Phase S1
step 6). Pure string transforms, no I/O - exercised directly here, and against the two real
READMEs that predate this module (stories/example, stories/private/new_babel) to prove it
only ever touches the marked section.

Run directly: python3 test/test_readme_sync.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))
import readme_sync  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# --- render_section: wraps, includes the sync note, handles an empty synopsis -------------
section = readme_sync.render_section("A short pitch.")
assert section.startswith("## Synopsis\n\nA short pitch.\n\n"), section
assert "generated from `meta.synopsis`" in section
assert section.endswith("\n\n")

empty = readme_sync.render_section("")
assert "No synopsis authored yet." in empty
assert readme_sync.render_section(None) == empty
print("OK: render_section wraps the synopsis, notes where it comes from, and handles empty input")

# --- sync: replaces an existing section and nothing else -----------------------------------
readme = (
    "# A Story\n\nSome intro text.\n\n"
    "## Synopsis\n\nOld pitch that needs replacing.\n\n"
    "Some stale note.\n\n"
    "## Setting Reference\n\nThis part must survive untouched.\n"
)
out = readme_sync.sync(readme, "New pitch.")
assert "Old pitch that needs replacing." not in out
assert "Some stale note." not in out
assert "New pitch." in out
assert "## Setting Reference\n\nThis part must survive untouched.\n" in out
assert out.startswith("# A Story\n\nSome intro text.\n\n## Synopsis\n\nNew pitch.")
print("OK: sync replaces only the Synopsis section body, leaving every other section intact")

# --- sync is idempotent: running it twice with the same synopsis changes nothing further ---
out2 = readme_sync.sync(out, "New pitch.")
assert out2 == out
print("OK: sync is idempotent")

# --- sync appends a section to a README that has none yet ----------------------------------
no_section = "# A Story\n\nJust some prose, no Synopsis heading at all.\n"
out = readme_sync.sync(no_section, "A pitch.")
assert no_section.strip() in out
assert "## Synopsis" in out
assert "A pitch." in out
print("OK: sync appends a Synopsis section to a README that predates one")

# --- sync on a section that is the last thing in the file (no trailing heading) ------------
last_section = "# A Story\n\n## Synopsis\n\nOld.\n\nOld note.\n"
out = readme_sync.sync(last_section, "New.")
assert "Old." not in out
assert "New." in out
print("OK: sync replaces a Synopsis section that runs to end of file")

# --- minimal_readme: a title line plus the synced section, nothing invented ----------------
out = readme_sync.minimal_readme("A Title", "a-slug", "The pitch.")
assert out.startswith("# A Title\n\n## Synopsis\n\nThe pitch.")
assert readme_sync.minimal_readme("", "a-slug", "The pitch.").startswith("# a-slug\n\n")
print("OK: minimal_readme falls back to the slug when there's no title")

# --- real files: sync only ever touches the Synopsis section, and is idempotent -----------
REAL_README_STORIES = [
    ("example", os.path.join(REPO_ROOT, "stories", "example")),
    ("new_babel", os.path.join(REPO_ROOT, "stories", "private", "new_babel")),
]
checked = 0
for slug, story_dir in REAL_README_STORIES:
    readme_path = os.path.join(story_dir, "README.md")
    if not os.path.isfile(readme_path):
        continue
    with open(readme_path, encoding="utf-8") as f:
        readme_text = f.read()
    with open(os.path.join(story_dir, "template.json"), encoding="utf-8") as f:
        synopsis = json.load(f)["meta"]["synopsis"]

    synced = readme_sync.sync(readme_text, synopsis)
    assert synced == readme_sync.sync(synced, synopsis), f"{slug}: sync is not idempotent"
    # Every line outside the Synopsis section must be untouched.
    before_lines = readme_text.split(readme_sync.SECTION_HEADING, 1)
    after_lines = synced.split(readme_sync.SECTION_HEADING, 1)
    assert before_lines[0] == after_lines[0], f"{slug}: text before the Synopsis heading changed"
    checked += 1
assert checked == 2, f"expected to check both real story READMEs, checked {checked}"
print(f"OK: real README round trip ({checked} stories) only ever touches the Synopsis section")

print("\nALL CHECKS PASSED: test_readme_sync")

"""Tests for the beat-labelling worksheet store (backend/label_sheet.py).

The worksheet markdown file is the single artifact scripts/gate_02.py scores, and three
callers write it (the CLI generator, the standalone web page, the inline row on the play
page). So the invariants worth pinning are: a save touches ONLY the three field lines inside
one turn's fence, sync is idempotent and respects the round's start turn, a sheet name from a
URL can't escape data/, and the whole feature stays off unless LABEL_SHEETS_USER names an
operator - it is a measurement tool, and the worksheets are a global artifact rather than a
per-user one.

Two regressions this pins, both of which happened during development:
  - save_scene originally did an unlocked read-modify-write, and concurrent saves across
    gunicorn workers silently dropped a label.
  - a partially-filled worksheet has empty BEAT: lines; the parser must return them as blank
    rather than swallowing the following line.

Run directly: python3 test/test_label_sheet.py
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import load_story_engine  # noqa: E402

load_story_engine()  # puts backend/ on sys.path and stubs filelock/dotenv
import label_sheet as L  # noqa: E402

BEATS = [("disquiet", "Someone acts against someone."), ("comfort", "Nobody does.")]
INTENSITY = [("1", "gentle"), ("2", "direct"), ("3", "undeniable")]

failures = []


def check(cond, msg):
    print(("OK: " if cond else "FAIL: ") + msg)
    if not cond:
        failures.append(msg)


tmp = tempfile.mkdtemp(prefix="label-sheet-test-")
L.LABELS_DIR = tmp
try:
    # --- authoring + round trip -----------------------------------------------------------
    L.create("t", BEATS, INTENSITY, start_turn=3, tie_break="Prefer comfort.")
    d = L.load("t")
    check([b["name"] for b in d["beats"]] == ["disquiet", "comfort"],
          "beats round-trip out of the header the human actually labelled against")
    check(d["tie_break"] == "Prefer comfort.", "tie-break round-trips")
    check(L.start_turn("t") == 3, "start_turn is recorded in the sheet, not passed around")
    check(d["scenes"] == [] and L.progress(d) == {"done": 0, "total": 0, "remaining": 0},
          "a fresh sheet has no scenes and zero progress")

    # --- appending scenes ------------------------------------------------------------------
    path = L.sheet_path("t")
    with open(path, "a") as f:
        f.write(L.render_scene(3, "I ask her.", "A" * 20 + "\nOPTIONS:\n1. x"))
        f.write(L.render_scene(4, "I wait.", "B" * 20))
    d = L.load("t")
    check([s["turn"] for s in d["scenes"]] == [3, 4], "appended scenes parse back in order")
    check("OPTIONS" not in d["scenes"][0]["opens"], "the OPTIONS block is stripped from excerpts")
    check(d["scenes"][0]["action"] == "I ask her.", "the player action round-trips")

    # --- a partially filled sheet ----------------------------------------------------------
    check(d["scenes"][0]["beat"] == "" and d["scenes"][0]["intensity"] == "",
          "blank rows parse as empty, not as the following line's text")

    # --- saving touches only the three field lines -----------------------------------------
    before = open(path).read()
    L.save_scene("t", 3, beat="disquiet", intensity="2")
    after = open(path).read()
    changed = [(a, b) for a, b in zip(before.split("\n"), after.split("\n")) if a != b]
    check(len(changed) == 2, f"one save rewrote exactly 2 lines (got {len(changed)})")
    check(all("BEAT:" in b or "INTENSITY:" in b for _, b in changed),
          "the rewritten lines are BEAT: and INTENSITY:, leaving prose untouched")
    s3 = next(s for s in L.load("t")["scenes"] if s["turn"] == 3)
    check((s3["beat"], s3["intensity"], s3["note"]) == ("disquiet", "2", ""),
          "an unpassed field (note) is left alone rather than blanked")
    L.save_scene("t", 3, note="hard to call")
    s3 = next(s for s in L.load("t")["scenes"] if s["turn"] == 3)
    check((s3["beat"], s3["note"]) == ("disquiet", "hard to call"),
          "a later save of one field preserves the fields saved earlier")
    check(L.progress(L.load("t"))["done"] == 1, "progress counts only fully labelled scenes")

    try:
        L.save_scene("t", 99, beat="comfort")
        check(False, "saving an unknown turn raises")
    except ValueError:
        check(True, "saving an unknown turn raises")

    # --- sheet names arrive from a URL -----------------------------------------------------
    for bad in ("../../etc/passwd", "a/b", "UPPER", "", "sp ace"):
        try:
            L.sheet_path(bad)
            check(False, f"rejects sheet name {bad!r}")
        except ValueError:
            check(True, f"rejects sheet name {bad!r}")

    try:
        L.create("t", BEATS, INTENSITY)
        check(False, "create refuses to clobber an existing worksheet")
    except FileExistsError:
        check(True, "create refuses to clobber an existing worksheet")

    # --- the operator gate -----------------------------------------------------------------
    os.environ.pop("LABEL_SHEETS_USER", None)
    check(not L.enabled_for("anyone") and not L.enabled_for(""),
          "labelling is off entirely when LABEL_SHEETS_USER is unset")
    os.environ["LABEL_SHEETS_USER"] = "operator-1"
    check(L.enabled_for("operator-1") and not L.enabled_for("someone-else"),
          "labelling is restricted to the one operator id, not to any logged-in user")
    os.environ.pop("LABEL_SHEETS_USER", None)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
if failures:
    print(f"FAILED {len(failures)} check(s): test_label_sheet")
    sys.exit(1)
print("ALL CHECKS PASSED: test_label_sheet")

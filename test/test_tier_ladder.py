"""Phase S3's gate (AUTHORING_TOOL_PHASES.md): editing QUORUM's tier boundary on the board and
saving changes the tier line in a QUORUM = boundary +/- 1 prompt exactly as a hand edit would.

"Exactly as a hand edit would" is checked twice over: the template the board writes is equal to
the template a hand edit produces, and the narration-prompt sections the real engine assembles
from each (`mechanics.prompt_sections`, through `freeze`/`new_save_state` like
test_genre_conformance.py) are equal at boundary - 1, boundary and boundary + 1 - where the tier
line must also actually move, or the gate would pass for a ladder the prompt never reads.

Also covers the `stat_axes` board key's writer (author_model._apply_stat_tiers) and the
`on_enter` "no reader" warning in mechanics.validate().

Run directly: python3 test/test_tier_ladder.py
"""
import contextlib
import copy
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import load_story_engine  # noqa: E402

se = load_story_engine()
import author_model  # noqa: E402
import mechanics  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MISSING_CORE = os.path.join(REPO_ROOT, "stories", "private", "the_missing_core", "template.json")


def missing_core_gate(raw):
    OLD, NEW = 40, 46  # QUORUM's "surfacing" boundary, and where the author drags it
    quorum = raw["mechanics"]["stats"]["axes"]["quorum"]["tiers"]
    idx = next(i for i, t in enumerate(quorum) if t["at"] == OLD)

    # --- board edit vs hand edit: the same template ----------------------------------------------
    model = author_model.to_board_model(raw)
    axis = next(a for a in model["stat_axes"] if a["axis"] == "quorum")
    axis["tiers"][idx]["at"] = NEW
    board = author_model.from_board_model(raw, model)

    hand = copy.deepcopy(raw)
    hand["mechanics"]["stats"]["axes"]["quorum"]["tiers"][idx]["at"] = NEW
    hand["schema_version"] = author_model.TEMPLATE_SCHEMA_VERSION
    hand["_storyboard"] = board["_storyboard"]  # layout is always rewritten; not the field under test
    assert board == hand, "the board's tier edit wrote something a hand edit would not"
    assert json.dumps(board, indent=2) == json.dumps(hand, indent=2), "same dict, different key order"
    print("OK: dragging QUORUM's boundary writes exactly the template a hand edit does")


    # --- ... and the same prompt, at boundary - 1, boundary, boundary + 1 --------------------------
    def tier_section(template, value):
        projected, _ = author_model.playable_projection(template, set(mechanics.registered_engines()))
        ctx = {"story": se.state_store.freeze(projected), "state": se.state_store.new_save_state(projected, "Tester")}
        ctx["state"]["protagonist"].setdefault("stats", {})["quorum"] = value
        return mechanics.prompt_sections(ctx).get("stats.tiers", "")


    def quorum_line(section):
        return next(line for line in section.splitlines() if line.startswith("- QUORUM"))


    below, at_ = quorum[idx - 1]["label"], quorum[idx]["label"]
    for value, expected_tier in ((NEW - 1, below), (NEW, at_), (NEW + 1, at_)):
        b, h = tier_section(board, value), tier_section(hand, value)
        assert b == h, (value, b, h)
        assert quorum_line(b).startswith(f"- QUORUM ({expected_tier}):"), (value, quorum_line(b))
    # The move is real: under the old boundary, QUORUM = NEW - 1 was already in the upper tier.
    assert quorum_line(tier_section(raw, NEW - 1)).startswith(f"- QUORUM ({at_}):")
    print(f"OK: QUORUM {NEW - 1}/{NEW}/{NEW + 1} render identical tier lines from board and hand "
          f"edits, and {NEW - 1} moved from {at_} to {below}")

    # --- stat_axes shape --------------------------------------------------------------------------
    axes = {a["axis"]: a for a in model["stat_axes"]}
    assert list(axes) == author_model.stat_axis_names(raw)
    assert axes["quorum"]["label"] == "QUORUM" and axes["quorum"]["floor"] == 0 and axes["quorum"]["ceiling"] == 100
    assert axes["sync"]["tiers"] == []
    print("OK: stat_axes lists every seeded axis with its display bounds")


if os.path.exists(MISSING_CORE):
    missing_core_gate(json.load(open(MISSING_CORE, encoding="utf-8")))
else:
    print("SKIPPED (the S3 gate): stories/private/the_missing_core is not checked out")

no_stats = {"meta": {"title": "t"}, "mechanics": {}}
assert "stat_axes" not in author_model.to_board_model(no_stats), "P-2: no stats block, no ladder"
print("OK: stat_axes is absent with no stats block")

# --- writer: add, clear, no-op, None ----------------------------------------------------------
BASE = {"meta": {"title": "t"}, "protagonist": {"stats": {"grit": 5}},
        "mechanics": {"stats": {"engine": "bounded_counter", "floor": 0, "ceiling": 20}}}


def write(template, tiers_by_axis):
    m = author_model.to_board_model(template)
    for a in m["stat_axes"]:
        if a["axis"] in tiers_by_axis:
            a["tiers"] = tiers_by_axis[a["axis"]]
    return author_model.from_board_model(template, m)


added = write(BASE, {"grit": [{"at": 0, "label": "raw"}, {"at": 10, "label": "hard",
                                                         "on_enter": {"once": True, "directive": "d"}}]})
assert added["mechanics"]["stats"]["axes"] == {"grit": {"tiers": [
    {"at": 0, "label": "raw"}, {"at": 10, "label": "hard", "on_enter": {"once": True, "directive": "d"}}]}}
assert "costs" not in added["mechanics"]["stats"]["axes"]["grit"], "tiers must never switch a story to priced stats"

cleared = write(added, {"grit": []})
assert "axes" not in cleared["mechanics"]["stats"], "P-2: emptying the only ladder removes the axes block"

priced = copy.deepcopy(BASE)
priced["mechanics"]["stats"]["axes"] = {"grit": {"costs": {"x": 1}, "tiers": [{"at": 0}]}}
assert write(priced, {"grit": []})["mechanics"]["stats"]["axes"] == {"grit": {"costs": {"x": 1}}}

empty_list = copy.deepcopy(BASE)
empty_list["mechanics"]["stats"]["axes"] = {"grit": {"tiers": []}}
assert write(empty_list, {})["mechanics"]["stats"]["axes"] == {"grit": {"tiers": []}}, \
    "an authored empty tier list must survive an unedited save"

old_client = author_model.to_board_model(added)
del old_client["stat_axes"]
assert author_model.from_board_model(added, old_client)["mechanics"]["stats"] == added["mechanics"]["stats"]
print("OK: the writer adds, clears (P-2) and no-ops correctly, and never touches costs")

# --- mechanics.validate warns about on_enter, which nothing reads yet (D1, CR-01) -------------
bound = copy.deepcopy(added)
bound["mechanics"]["stats"]["engine"] = "bounded_counter"
out = io.StringIO()
with contextlib.redirect_stdout(out):
    mechanics.validate(bound)
assert "on_enter" in out.getvalue() and "grit" in out.getvalue(), out.getvalue()
out = io.StringIO()
with contextlib.redirect_stdout(out):
    mechanics.validate(cleared)
assert "on_enter" not in out.getvalue()
print("OK: validate() names the axes whose tiers author an unread on_enter, and only those")

print("test_tier_ladder.py: all checks passed.")

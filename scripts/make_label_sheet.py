#!/usr/bin/env python3
"""Generates a blank beat-labelling worksheet from a played save, for pacing gate 0.2
(docs/analysis_and_plans/SCHEMA_V2/PHASE_0_GATE_REPORT.md, docs/Narrative_Pacing_Loop_Spec_v4.md §0).

Gate 0.2 measures agreement between a human's labels and the classifier prompt's. That only
means anything if the human labels FIRST and INDEPENDENTLY, so this sheet deliberately ships
blank - no model-generated labels, no suggestions, no pre-filled counts. The gate report's own
§2 table is a model's labels and must not be used as the human side of the comparison; two
models agreeing measures shared bias, not accuracy.

Output goes under data/ (gitignored) rather than docs/, because the excerpts are story prose.
For new_babel that content lives in a private submodule on purpose - see CLAUDE.md's
"Public repo, private story content" - and a worksheet full of it must not land in this
repo's history or anywhere hosted.

Usage:
  scripts/make_label_sheet.py --user <id> --story new_babel --out data/labels_new_babel.md
  scripts/make_label_sheet.py --user <id> --story example --out data/labels_example.md \
      --vocab data/vocab_example_4beat.json

The beats below are New Babel's and are the default only because it was labelled first. Gate
0.3 is the same exercise against a second genre's vocabulary, so pass --vocab with the same
JSON file gate_02.py will score against - the worksheet and the classifier prompt must
interpolate identical definition strings or the agreement number compares two questions.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

import label_sheet  # noqa: E402
import state_store  # noqa: E402
from replay_turn import all_turns, split_turn  # noqa: E402

# Scene-block format and excerpting live in backend/label_sheet.py, because the web
# labelling UI writes worksheets too and a block written by either path has to be readable
# by the other and by gate_02.py. This script keeps its own, wordier header - a sheet filled
# in offline needs the "label independently" preamble that a live sheet has no room for.
strip_options = label_sheet.strip_options
excerpt = label_sheet.excerpt
HEAD_WORDS = label_sheet.HEAD_WORDS
TAIL_WORDS = label_sheet.TAIL_WORDS

# Copied verbatim from pacing spec §5.1 (New Babel) - these are the strings that go into the
# classifier prompt, so the human must judge against exactly the same wording or the agreement
# number compares two different questions. When Phase 6.1 authors these into
# mechanics.pacing_loop.beats, read them from the template instead of here.
BEATS = [
    ("crisis", "Active pursuit or direct threat requiring an immediate decision this scene."),
    ("escalation", "A new complication or threat is introduced; stakes rise, but no immediate "
                   "life-or-death branch yet."),
    ("lull", "The immediate pursuing threat has receded or resolved. Dread and cost may remain. "
             "No new pursuer introduced this scene."),
    ("resolution", "A concrete sub-goal is achieved or a threat conclusively closed off. Rare."),
]

INTENSITY = [
    ("1", "Pressure present, no immediate physical danger"),
    ("2", "Direct confrontation or forced decision in the room"),
    ("3", "Physical danger, active pursuit, body-horror escalation"),
]

def load_vocab(path: str):
    """Swaps in a story's own beats/intensity, so the sheet a human fills in is worded exactly
    like the classifier prompt gate_02.py will run over the same scenes."""
    global BEATS, INTENSITY
    v = json.load(open(path))
    BEATS = [(n, b["definition"]) for n, b in v["beats"].items()]
    INTENSITY = [tuple(x) for x in v.get("intensity", INTENSITY)]


def header(story: str, count: int) -> str:
    beats = "\n".join(f"- **`{name}`** — {defn}" for name, defn in BEATS)
    n_beats = {2: "two", 3: "three", 4: "four", 5: "five"}.get(len(BEATS), str(len(BEATS)))
    levels = "\n".join(f"- **{n}** — {desc}" for n, desc in INTENSITY)
    return f"""# Beat labelling worksheet — `{story}`

{count} scenes. Fill in `BEAT:` and `INTENSITY:` under each. Leave `NOTE:` blank unless the
scene was hard to call.

## Before you start

**Label independently.** Do not read the gate report's §2 table first — it contains a model's
labels for these same scenes, and the whole point of this exercise is an independent human
judgement to measure the classifier against. If you have already read it, say so; the
measurement is weaker but still worth something as a consistency check.

**Don't overthink individual calls.** Gate 0.2 wants ≥70% agreement, not perfection.
Disagreements are data — they show which beat pairs are ambiguous, and the plan's remedy for a
consistently confused pair is to collapse it into one beat. First instinct is usually the right
label.

## The {n_beats} beats

{beats}

Every scene gets exactly one. If two seem to fit, apply the boundary rule below.

## Intensity, 1–3

{levels}

Score every scene, including the quiet ones — counters accumulate intensity rather than scene
count, so a quiet scene still carries a weight.

## The boundary rule

Some scenes open in one mode and turn in their final paragraph — a warm meal that ends with a
threat walking in, a night's sleep that ends on an alarm.

**Classify by the scene's terminal state**: what is true when the scene stops, since that is
what carries into the next turn and what a corrective directive would have to act on.

Both excerpts are given per scene for exactly this reason. Where the tail alone is not enough,
the full text is in the export.

---

"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--user", default=state_store.DEFAULT_USER_ID)
    ap.add_argument("--story", default=state_store.DEFAULT_STORY_SLUG)
    ap.add_argument("--out", required=True)
    ap.add_argument("--vocab", help="JSON vocabulary to label against (same file gate_02.py "
                    "takes); defaults to the New Babel beats above.")
    ap.add_argument("--live", action="store_true",
                    help="Create an EMPTY sheet that fills in as you play, instead of one "
                    "covering the turns already played.")
    ap.add_argument("--start-turn", type=int, default=None,
                    help="With --live: first turn this round covers. Defaults to the next "
                    "unplayed turn, so an earlier round's turns aren't pulled in.")
    args = ap.parse_args()

    if not args.out.startswith("data/"):
        print("refusing to write outside data/ - worksheets contain story prose "
              "(see module docstring)", file=sys.stderr)
        return 2

    if args.vocab:
        load_vocab(args.vocab)

    ctx = state_store.load_state(args.user, args.story)
    turns = all_turns(ctx)[1:]  # index 0 is the opening scene, not a played turn

    if args.live:
        # An empty sheet that the play page appends to as turns are played
        # (label_sheet.sync_from_save), for labelling a round WHILE playing it rather than
        # from an export afterwards. Point label_sheet.LIVE_SHEETS at the resulting name.
        sheet = os.path.basename(args.out)[len("labels_"):-len(".md")]
        path = label_sheet.create(
            sheet, BEATS, INTENSITY, start_turn=args.start_turn or len(turns) + 1,
            tie_break=(json.load(open(args.vocab)).get("tie_break", "") if args.vocab else ""),
        )
        print(f"Created empty live sheet {path}, starting at turn "
              f"{label_sheet.start_turn(sheet)}")
        return 0

    parts = [header(args.story, len(turns))]
    for i, entry in enumerate(turns, start=1):
        action, narration = split_turn(entry)
        parts.append(label_sheet.render_scene(i, action, narration))

    parts.append("## When you're done\n\nSave the file and say so. The classifier prompt gets "
                 "run over the same scenes via `scripts/replay_turn.py`, and the two label "
                 "sets are compared — overall agreement, plus which beat pairs account for the "
                 "disagreements.\n")

    with open(args.out, "w") as f:
        f.write("".join(parts))
    print(f"Wrote {args.out} ({len(turns)} scenes to label)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

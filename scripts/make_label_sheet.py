#!/usr/bin/env python3
"""Generates a blank beat-labelling worksheet from a played save, for pacing gate 0.2
(docs/PHASE_0_GATE_REPORT.md, docs/Narrative_Pacing_Loop_Spec_v4.md §0).

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

Gate 0.3 needs the identical artifact for a `example` playthrough, which is why this is a
script and not a one-off paste.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

import state_store  # noqa: E402
from replay_turn import all_turns, split_turn  # noqa: E402

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

HEAD_WORDS = 60
TAIL_WORDS = 150


def strip_options(narration: str) -> str:
    """Stored turns keep the model's whole reply, OPTIONS block included. Those are the choices
    offered, not part of the scene, and left in they swamp the tail excerpt - which is the half
    the boundary rule actually depends on."""
    head = narration.split("\nOPTIONS:")[0]
    return head.rstrip()


def excerpt(narration: str) -> tuple:
    """Opening and closing of a scene. The closing matters most: the boundary rule below asks
    for the scene's terminal state, and four of the 24 scenes in the first sample turn over in
    their final paragraph."""
    words = strip_options(narration).split()
    if len(words) <= HEAD_WORDS + TAIL_WORDS:
        return " ".join(words), ""
    return " ".join(words[:HEAD_WORDS]), " ".join(words[-TAIL_WORDS:])


def header(story: str, count: int) -> str:
    beats = "\n".join(f"- **`{name}`** — {defn}" for name, defn in BEATS)
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

## The four beats

{beats}

Every scene gets exactly one. If two seem to fit, apply the boundary rule below.

## Intensity, 1–3

{levels}

Score every scene, including `lull` and `resolution` — counters accumulate intensity rather
than scene count, so a quiet scene still carries a weight.

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
    args = ap.parse_args()

    if not args.out.startswith("data/"):
        print("refusing to write outside data/ - worksheets contain story prose "
              "(see module docstring)", file=sys.stderr)
        return 2

    ctx = state_store.load_state(args.user, args.story)
    turns = all_turns(ctx)[1:]  # index 0 is the opening scene, not a played turn

    parts = [header(args.story, len(turns))]
    for i, entry in enumerate(turns, start=1):
        action, narration = split_turn(entry)
        head, tail = excerpt(narration)
        parts.append(f"## Turn {i}\n\n")
        parts.append(f"**Action:** {action}\n\n")
        parts.append(f"**Opens:** {head}…\n\n")
        if tail:
            parts.append(f"**Closes:** …{tail}\n\n")
        parts.append("```\nBEAT:       \nINTENSITY:  \nNOTE:       \n```\n\n---\n\n")

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

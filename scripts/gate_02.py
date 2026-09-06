#!/usr/bin/env python3
"""Pacing gate 0.2: measures agreement between a human's beat labels and the classifier
prompt's, over the same scenes. Gate is >=70% (docs/Narrative_Pacing_Loop_Spec_v4.md §0).

Reads the filled-in worksheet from scripts/make_label_sheet.py, runs the classifier over each
scene, and reports overall agreement plus the confusion pairs. The plan's remedy for a
consistently confused pair is to COLLAPSE it into one beat rather than to keep tuning wording,
so which pairs disagree matters as much as the headline number.

The prompt below is deliberately the worksheet's own text: the same four definitions verbatim,
the same boundary rule, the same intensity scale. Measuring the human against a differently
worded question would measure the wording gap, not the classifier.

Scope caveat: this runs classification as a STANDALONE call. Pacing spec §9 step 3 has
production emitting beat_type alongside everything else update_progress_from_turn already
extracts, in one call. A standalone pass is the cleaner measurement of whether the definitions
can be discriminated at all, which is what the gate asks; if it passes here and then degrades
once embedded in the much larger state-update prompt, that is a separate finding.

Usage:
  scripts/gate_02.py --user <id> --story new_babel --labels data/labels_new_babel.md
"""

import argparse
import collections
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

import state_store  # noqa: E402
import story_engine  # noqa: E402
from replay_turn import all_turns, split_turn  # noqa: E402
from make_label_sheet import BEATS, INTENSITY, strip_options  # noqa: E402

VALID = {name for name, _ in BEATS}
# What a human is allowed to have written in the worksheet. With --vocab this is the vocabulary
# they actually labelled against, which for a collapsed vocabulary is the PRE-collapse names -
# those are not in VALID, and scoring them as unrecognised would silently drop every row.
LABEL_VOCAB = set(VALID)


def parse_labels(path: str) -> dict:
    """Pulls {turn_number: (beat, intensity)} out of the filled worksheet. Tolerant of the
    obvious human variations - case, surrounding whitespace, and the "crises"/"crisis" slip -
    because a transcription nit is not a disagreement and must not be scored as one."""
    text = open(path).read()
    labels = {}
    for block in re.split(r"^## Turn (\d+)$", text, flags=re.M)[1:]:
        if block.strip().isdigit():
            turn = int(block)
            continue
        # [ \t]* not \s*: on an unfilled row \s* crosses the newline and captures the NEXT
        # line, so a half-finished worksheet reported every blank scene as the unrecognised
        # beat "intensity:" instead of simply skipping it.
        beat = re.search(r"^BEAT:[ \t]*(.*)$", block, flags=re.M)
        intensity = re.search(r"^INTENSITY:[ \t]*(.*)$", block, flags=re.M)
        if not beat:
            continue
        raw = beat.group(1).strip().lower()
        if not raw:
            continue  # not labelled yet
        raw = {"crises": "crisis", "resolutions": "resolution", "lulls": "lull"}.get(raw, raw)
        if raw not in LABEL_VOCAB:
            print(f"turn {turn}: unrecognised beat {raw!r}", file=sys.stderr)
            continue
        try:
            score = int((intensity.group(1) if intensity else "").strip())
        except ValueError:
            score = None
        labels[turn] = (raw, score)
    return labels


TIE_BREAK = ""


def classifier_prompt(action: str, narration: str) -> str:
    beats = "\n".join(f"- {name}: {defn}" for name, defn in BEATS)
    # Whatever tie-break the worksheet showed the human has to reach the classifier verbatim
    # too, for the same reason the definitions do: otherwise the two are answering different
    # questions and the agreement number measures the gap between them.
    tie = f"\n\n{TIE_BREAK}" if TIE_BREAK else ""
    levels = "\n".join(f"- {n}: {desc}" for n, desc in INTENSITY)
    return f"""Classify this scene from an interactive story by beat type and intensity.

BEAT TYPES (choose exactly one):
{beats}

If a scene opens in one mode and turns in its final paragraph, classify by the scene's
terminal state - what is true when the scene stops.{tie}

INTENSITY:
{levels}

Score intensity for every scene, including quiet ones.

PLAYER ACTION: {action}

SCENE:
{narration}

Respond with ONLY a JSON object, no other text:
{{"beat_type": "<one of: {', '.join(sorted(VALID))}>", "intensity": <1, 2 or 3>}}"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--user", default=state_store.DEFAULT_USER_ID)
    ap.add_argument("--story", default=state_store.DEFAULT_STORY_SLUG)
    ap.add_argument("--labels", required=True)
    ap.add_argument("--tier", choices=["b", "c"], default="c",
                    help="Which model tier classifies. 'c' (default) is what production would "
                    "actually run every turn; 'b' adds reasoning, for diagnosing whether a "
                    "failure is the vocabulary's fault or the cheap model's.")
    ap.add_argument("--vocab", help="JSON vocabulary to test instead of the spec's four beats: "
                    "{beats:{name:{definition}}, collapse:{old_beat:new_beat}, intensity:[[n,desc]]}. "
                    "Human labels are mapped through `collapse` so an existing worksheet can score "
                    "a revised vocabulary without relabelling.")
    args = ap.parse_args()

    global BEATS, INTENSITY, VALID, LABEL_VOCAB, TIE_BREAK
    collapse = None
    if args.vocab:
        v = json.load(open(args.vocab))
        BEATS = [(n, b["definition"]) for n, b in v["beats"].items()]
        INTENSITY = [tuple(x) for x in v.get("intensity", INTENSITY)]
        VALID = {n for n, _ in BEATS}
        TIE_BREAK = v.get("tie_break", "")
        collapse = v.get("collapse") or None
        LABEL_VOCAB = VALID | set(collapse or {})

    human = parse_labels(args.labels)
    if not human:
        print("no labels parsed - is the worksheet filled in?", file=sys.stderr)
        return 2

    ctx = state_store.load_state(args.user, args.story)
    turns = all_turns(ctx)[1:]

    rows, confusion = [], collections.Counter()
    for turn_no, (h_beat, h_int) in sorted(human.items()):
        if collapse:
            h_beat = collapse.get(h_beat, h_beat)
        action, narration = split_turn(turns[turn_no - 1])
        prompt = classifier_prompt(action, strip_options(narration))
        try:
            if args.tier == "b":
                out = story_engine.call_llm_json(
                    prompt, model=story_engine.TIER_AB_MODEL,
                    provider=story_engine.TIER_AB_PROVIDER, reasoning=True)
            else:
                out = story_engine.call_llm_json(prompt)
        except (story_engine.LLMUnavailableError, json.JSONDecodeError, ValueError) as e:
            print(f"turn {turn_no}: classifier failed ({e})", file=sys.stderr)
            continue
        c_beat = str(out.get("beat_type", "")).strip().lower()
        c_int = out.get("intensity")
        match = c_beat == h_beat
        if not match:
            confusion[tuple(sorted((h_beat, c_beat)))] += 1
        rows.append((turn_no, h_beat, h_int, c_beat, c_int, match))
        print(f"  turn {turn_no:>2}: human {h_beat:<11}{h_int}   classifier {c_beat:<11}{c_int}"
              f"   {'ok' if match else 'MISMATCH'}")

    if not rows:
        return 3

    agree = sum(1 for r in rows if r[5])
    pct = 100.0 * agree / len(rows)
    # Raw agreement alone hides a degenerate classifier: on the example sheet a run that
    # answered the SAME beat for all 30 scenes scored 56.7%, purely because that beat was
    # the human's majority class. Kappa corrects for chance and reports 0.00 there, which is
    # the honest number, so it is printed alongside and the marginals are shown outright.
    labels = sorted({r[1] for r in rows} | {r[3] for r in rows})
    n = len(rows)
    p_e = sum((sum(1 for r in rows if r[1] == k) / n) * (sum(1 for r in rows if r[3] == k) / n)
              for k in labels)
    kappa = (pct / 100.0 - p_e) / (1 - p_e) if p_e < 1 else 0.0
    int_agree = sum(1 for r in rows if r[2] is not None and r[2] == r[4])

    print(f"\n{'=' * 62}")
    print(f"BEAT AGREEMENT: {agree}/{len(rows)} = {pct:.1f}%   (gate: >=70%)")
    print(f"COHEN'S KAPPA:  {kappa:.3f}   (0 = no better than guessing the majority class)")
    print("  human:      " + ", ".join(f"{k}={sum(1 for r in rows if r[1] == k)}" for k in labels))
    print("  classifier: " + ", ".join(f"{k}={sum(1 for r in rows if r[3] == k)}" for k in labels))
    print(f"INTENSITY EXACT: {int_agree}/{len(rows)} = {100.0 * int_agree / len(rows):.1f}%")
    print(f"{'=' * 62}")

    if confusion:
        print("\nConfusion pairs (human label vs classifier label):")
        for (a, b), n in confusion.most_common():
            print(f"  {a} / {b}: {n}")
        top, top_n = confusion.most_common(1)[0]
        if top_n >= 3:
            print(f"\n  '{top[0]}' vs '{top[1]}' accounts for {top_n} of {len(rows) - agree} "
                  f"disagreements.\n  Per the plan, a consistently confused pair is collapsed "
                  f"into one beat, not reworded.")

    if pct >= 70 and kappa < 0.4:
        print("\nFAIL - agreement clears 70% but kappa is near chance: the classifier is "
              "riding a majority class, not discriminating.")
        return 1
    print("\nPASS" if pct >= 70 else "\nFAIL - do not proceed to 6.2 on this vocabulary")
    return 0 if pct >= 70 else 1


if __name__ == "__main__":
    sys.exit(main())

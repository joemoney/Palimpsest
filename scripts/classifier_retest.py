#!/usr/bin/env python3
"""Measures the classifier's agreement with ITSELF over repeated runs on the same scenes.

Every kappa in docs/PHASE_0_GATE_REPORT.md is human-vs-classifier, measured from a single
classification pass. Re-scoring an unchanged set of 17 scenes moved kappa from 0.549 to 0.433
with nothing changed but the LLM call, which means those figures carry run-to-run variance on
top of sampling variance and none of them state it.

Self-agreement is the ceiling: a classifier that agrees with itself at kappa K cannot agree
with any human above roughly K, however good the vocabulary or the rater. Measuring it tells
you whether the remaining gap is worth chasing with more wording changes at all.

Reuses gate_02's prompt builder verbatim, so this measures the same call the gate makes.

Usage:
  scripts/classifier_retest.py --user <id> --story example \
      --labels data/labels_example_v3.md --vocab data/vocab_example_2beat_v3.json --runs 3
"""

import argparse
import collections
import itertools
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import state_store  # noqa: E402
import story_engine  # noqa: E402
import gate_02  # noqa: E402
from replay_turn import all_turns, split_turn  # noqa: E402


def kappa(a: list, b: list) -> float:
    n = len(a)
    labels = sorted(set(a) | set(b))
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    pe = sum((a.count(k) / n) * (b.count(k) / n) for k in labels)
    return (po - pe) / (1 - pe) if pe < 1 else 0.0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--user", default=state_store.DEFAULT_USER_ID)
    ap.add_argument("--story", default=state_store.DEFAULT_STORY_SLUG)
    ap.add_argument("--labels", required=True)
    ap.add_argument("--vocab", required=True)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--out", help="write per-scene results as JSON")
    args = ap.parse_args()

    v = json.load(open(args.vocab))
    gate_02.BEATS = [(n, b["definition"]) for n, b in v["beats"].items()]
    gate_02.INTENSITY = [tuple(x) for x in v.get("intensity", gate_02.INTENSITY)]
    gate_02.VALID = {n for n, _ in gate_02.BEATS}
    gate_02.TIE_BREAK = v.get("tie_break", "")
    gate_02.LABEL_VOCAB = gate_02.VALID | set(v.get("collapse") or {})

    human = gate_02.parse_labels(args.labels)
    # A collapsed vocabulary's worksheet holds PRE-collapse names (new_babel's sheet says
    # crisis/escalation/lull/resolution while the vocabulary is threat/respite). Map them
    # here, or every human label mismatches the classifier's on name alone.
    collapse = v.get("collapse") or {}
    if collapse:
        human = {t: (collapse.get(b, b), i) for t, (b, i) in human.items()}
    ctx = state_store.load_state(args.user, args.story)
    turns = all_turns(ctx)[1:]

    scenes = sorted(human)
    runs = [[] for _ in range(args.runs)]
    for turn_no in scenes:
        action, narration = split_turn(turns[turn_no - 1])
        prompt = gate_02.classifier_prompt(action, gate_02.strip_options(narration))
        answers = []
        for r in range(args.runs):
            try:
                out = story_engine.call_llm_json(prompt)
                answers.append(str(out.get("beat_type", "")).strip().lower())
            except Exception as e:  # noqa: BLE001 - a failed call is data, not a crash
                print(f"turn {turn_no} run {r}: FAILED ({e})", file=sys.stderr)
                answers.append(None)
        for r, a in enumerate(answers):
            runs[r].append(a)
        flag = "" if len(set(answers)) == 1 else "   <- UNSTABLE"
        shown = " ".join(f"{str(a or '?')[:9]:<10}" for a in answers)
        print(f"  turn {turn_no:>3}: {shown}{flag}")

    keep = [i for i in range(len(scenes)) if all(r[i] is not None for r in runs)]
    if len(keep) < 2:
        print("too few complete scenes", file=sys.stderr)
        return 3
    cols = [[r[i] for i in keep] for r in runs]

    print(f"\n{'=' * 66}")
    unstable = [scenes[i] for i in keep if len({r[i] for r in runs}) > 1]
    print(f"SCENES SCORED: {len(keep)}   runs: {args.runs}")
    print(f"UNSTABLE SCENES: {len(unstable)}/{len(keep)} = {100.0*len(unstable)/len(keep):.1f}%  {unstable}")
    pairs = list(itertools.combinations(range(args.runs), 2))
    ks, ags = [], []
    for x, y in pairs:
        ag = sum(1 for p, q in zip(cols[x], cols[y]) if p == q) / len(keep)
        k = kappa(cols[x], cols[y])
        ks.append(k); ags.append(ag)
        print(f"  run{x+1} vs run{y+1}: agreement {ag*100:5.1f}%   kappa {k:+.3f}")
    print(f"\nMEAN SELF-AGREEMENT: {sum(ags)/len(ags)*100:.1f}%   MEAN SELF-KAPPA: {sum(ks)/len(ks):+.3f}")
    print("  This is the ceiling on any human-classifier kappa for this prompt.")
    for r, col in enumerate(cols):
        c = collections.Counter(col)
        print(f"  run{r+1} marginals: " + ", ".join(f"{k}={c[k]}" for k in sorted(c)))
    print("=" * 66)

    if args.out:
        json.dump({"scenes": [scenes[i] for i in keep],
                   "runs": cols,
                   "human": {str(t): human[t][0] for t in human}},
                  open(args.out, "w"), indent=1)
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

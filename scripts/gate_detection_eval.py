#!/usr/bin/env python3
"""Precision/recall for story_engine.detect_gate_refusal - the engine v2 phase 6 detector.

docs/analysis_and_plans/ENGINE_V2/GATE_DETECTION_MEASUREMENT.md records what this produced
and what it means. Re-run it after any change to the detector prompt, and after changing a
story's `refusal_hint` wording - the echo rate below is sensitive to that, which is the whole
finding.

**Why the negatives are real actions and not invented ones.** The failure that matters is a
modal refusing a legitimate action, and invented negatives are written by someone who already
knows what the gates are, so they dodge the gates without meaning to. Every real player action
from the two flagship saves was written against a world with no gates in it at all, which
makes them the only honest negatives available.

**Scene reconstruction.** Each action is evaluated against the narration that actually
preceded it in the transcript, not against the save's final scene - the detector is shown a
scene summary and giving it turn 49's scene for turn 3's action would measure nothing.

Requires a real API key (this is the one measurement here that cannot run offline) and the
private submodule. Usage:

    python3 scripts/gate_detection_eval.py                    # full run
    python3 scripts/gate_detection_eval.py --positives-only   # 15 calls, for prompt tweaks
    python3 scripts/gate_detection_eval.py --hints terse      # the echo-rate comparison
"""
import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))
import state_store  # noqa: E402
import story_engine  # noqa: E402

SAVES = [
    ("new_babel", "data/saves/9a20892e-0bc7-4f5e-8bab-8ca815082532/new_babel.json"),
    ("the_missing_core", "data/saves/9a20892e-0bc7-4f5e-8bab-8ca815082532/the_missing_core.json"),
]
STORY = "new_babel"

# Two gates the story's own worldbuilding already implies: the template describes the Spire's
# "private security checkpoints, and skywalks that let the right ID badge avoid the street
# entirely". Authored here rather than in the template because this measures the detector, not
# a shipped feature - no story declares a gate block yet.
HINTS = {
    "prose": {
        "spire_checkpoint": "The checkpoint does not challenge you. It simply does not read "
                            "you, and the guard's attention has already moved on.",
        "tidewall_seal": "The seal is shut and the water behind it is audible. Nothing here "
                         "is going to open for someone without a reason on file.",
    },
    "terse": {
        "spire_checkpoint": "bureaucratic indifference; the system does not even register them",
        "tidewall_seal": "immovable, and the water audible behind it",
    },
}


def gates(style):
    return {"engine": "precondition", "gates": [
        {"id": "spire_checkpoint", "target": "Spire District",
         "requires": {"item_tag": "credential"}, "refusal_hint": HINTS[style]["spire_checkpoint"]},
        {"id": "tidewall_seal", "target": "The Tidewall",
         "requires": {"flag": "tidewall_access_granted"},
         "refusal_hint": HINTS[style]["tidewall_seal"]},
    ]}


# Written in the voice of the real actions (~130 chars, first person, concrete), 9 reaching for
# the Spire and 6 for the Tidewall, so gate *attribution* is measurable and not just detection.
POSITIVES = [
    ("spire_checkpoint", "I head for the Spire District and try to walk the skywalk through like I belong there."),
    ("spire_checkpoint", "I make for the checkpoint at the Spire, badge-hand out, already guessing how little they'll find of me."),
    ("spire_checkpoint", "I take the lift up toward the arcology towers and see how far into the Spire I get."),
    ("spire_checkpoint", "I follow the skywalk in and try to get past Cordon's security without stopping."),
    ("spire_checkpoint", "I walk up to the Spire checkpoint and ask the guard to let me through."),
    ("spire_checkpoint", "I go looking for a way around the Spire's security, some service entrance nobody watches."),
    ("tidewall_seal", "I push toward the Tidewall and see whether the seal will let me through."),
    ("tidewall_seal", "I make my way down to the Tidewall and try the seal with the shim."),
    ("tidewall_seal", "I ask whoever's nearest how a person gets past the Tidewall, then start walking that way."),
    ("tidewall_seal", "I try the Tidewall seal, leaning on it to see if it gives at all."),
    ("spire_checkpoint", "I head up into the Spire District to find Cordon Dynamics' headquarters myself."),
    ("spire_checkpoint", "I tail her toward the Spire and follow her straight through the checkpoint."),
    ("spire_checkpoint", "I climb toward the skywalks and try to cross into the corporate core."),
    ("tidewall_seal", "I work the seal at the Tidewall, looking for the interface it must have."),
    ("tidewall_seal", "I decide the answer's behind the Tidewall and go to force my way in."),
]
POSITIVE_SCENE = "You are in Lowmarket, beneath the Spire's shadow, among the market stalls."


def real_actions(path):
    """(action, the narration that preceded it) for every real player turn."""
    save = json.load(open(path, encoding="utf-8"))
    turns = save["history"].get("full_transcript", []) + save["history"]["recent_turns"]
    out, previous = [], ""
    for entry in turns:
        if entry.startswith("Player: "):
            split = entry.find("\nNarrator: ")
            out.append((entry[len("Player: "):split], previous))
            previous = entry[split + len("\nNarrator: "):]
        else:
            previous = entry.replace("Narrator: ", "", 1)
    return out


def make_ctx(scene_summary, style):
    """A ctx whose gate predicates are genuinely unmet - otherwise `unmet()` is empty and the
    detector returns without calling the model at all, measuring nothing."""
    story = state_store.thaw(state_store.load_template(STORY))
    story.setdefault("mechanics", {})["gate"] = gates(style)
    save = json.load(open(dict(SAVES)[STORY], encoding="utf-8"))
    save["protagonist"]["inventory"] = [
        i for i in save["protagonist"].get("inventory", []) if not isinstance(i, dict)
    ]
    for bucket in ("active", "archive"):
        save["protagonist"]["flags"].get(bucket, {}).pop("tidewall_access_granted", None)
    save["scene"] = {**save.get("scene", {}), "summary": (scene_summary or "")[:600]}
    return {"story": state_store.freeze(story), "state": save}


def probe(action, scene, style):
    try:
        result = story_engine.detect_gate_refusal(make_ctx(scene, style), action)
    except Exception as exc:                                   # noqa: BLE001 - reported, not raised
        return {"action": action, "gate": "ERROR", "sentence": str(exc)[:120]}
    return {"action": action, "gate": result["gate"] if result else None,
            "sentence": (result or {}).get("sentence", "")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hints", choices=sorted(HINTS), default="prose")
    ap.add_argument("--positives-only", action="store_true")
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--out")
    args = ap.parse_args()

    jobs = [("pos", expected, action, POSITIVE_SCENE) for expected, action in POSITIVES]
    if not args.positives_only:
        for _, path in SAVES:
            jobs += [("neg", None, a, s) for a, s in real_actions(path)]

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(lambda j: {**probe(j[2], j[3], args.hints),
                                           "kind": j[0], "expected": j[1]}, jobs))

    negatives = [r for r in results if r["kind"] == "neg"]
    positives = [r for r in results if r["kind"] == "pos"]
    false_pos = [r for r in negatives if r["gate"] and r["gate"] != "ERROR"]
    detected = [r for r in positives if r["gate"] and r["gate"] != "ERROR"]
    correct = [r for r in detected if r["gate"] == r["expected"]]
    echoed = [r for r in detected if r["sentence"].strip().lower()
              == HINTS[args.hints][r["gate"]].strip().lower()]

    print(f"hints: {args.hints}")
    if negatives:
        print(f"  negatives      {len(negatives):>4}   false positives {len(false_pos)} "
              f"({100 * len(false_pos) / len(negatives):.1f}%)")
    print(f"  positives      {len(positives):>4}   detected {len(detected)}, "
          f"right gate {len(correct)}")
    print(f"  hint echoed    {len(echoed):>4}/{len(detected)}   "
          f"<- refusal_hint acting as refusal_text")
    print(f"  errors         {sum(1 for r in results if r['gate'] == 'ERROR'):>4}")
    for r in false_pos:
        print(f"    FALSE POSITIVE [{r['gate']}] {r['action'][:100]}")
    for r in positives:
        if not r["gate"]:
            print(f"    MISSED {r['action'][:100]}")
    if args.out:
        json.dump(results, open(args.out, "w"), indent=1)
        print(f"  wrote {args.out}")


if __name__ == "__main__":
    main()

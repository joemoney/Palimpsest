#!/usr/bin/env python3
"""§12.5 / open question 5: is Tier C still right for the observation pass after E-3?

The spec says this cannot be settled on paper and asks for the ported pass run on Tier C and on
Tier AB over the same held-out turns, comparing misclassification against the latency and cost
delta. This runs that experiment.

**There is no ground truth, and this deliberately does not invent any.** Hand-labelling what
each turn *should* have reported is subjective and expensive, and the decision does not need it.
Two things that can be measured settle the question instead:

  - **Agreement between tiers.** If the expensive model returns the same classifications as the
    cheap one, Tier C stands and no labels are needed. Disagreement is what would require them,
    and only then, and only on the turns that disagree.
  - **Self-consistency within a tier.** Each config is run twice over the same turn. This is the
    control the experiment is useless without: if Tier C disagrees with *itself* as often as it
    disagrees with Tier A, then the between-tier difference is noise and a tier switch buys
    nothing but latency.

**Only categorical fields are compared.** "Misclassification" is meaningful for a beat type, a
subplot's band, a revelation id, a location out of a closed set - not for a flag name the model
invents or a prose item label, which will never match across runs and whose disagreement means
nothing. Comparing whole diffs would report noise as signal.

**Every turn is scored against an identical prompt across tiers**, built from the save's final
state plus that turn's action and narration. The absolute classifications are therefore not what
production would have produced at that point in the story - but both tiers see the same bytes,
which is all a comparison needs.

Requires a real API key. Usage:

    python3 scripts/tier_observation_probe.py --turns 10 --repeats 2
    python3 scripts/tier_observation_probe.py --configs C,A       # skip the slow reasoning pass
"""
import argparse
import copy
import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))
import state_store  # noqa: E402
import story_engine as se  # noqa: E402

SAVES = [
    ("new_babel", "data/saves/9a20892e-0bc7-4f5e-8bab-8ca815082532/new_babel.json"),
    ("the_missing_core", "data/saves/9a20892e-0bc7-4f5e-8bab-8ca815082532/the_missing_core.json"),
]

# Tier A and B are the same model distinguished only by `reasoning` (CLAUDE.md); Tier C is its
# own. All three are here because §5.5's argument is about latency as much as accuracy, and
# Tier B's reasoning pass is the expensive end of it.
CONFIGS = {
    "C": {"model": se.TIER_C_MODEL, "provider": se.TIER_C_PROVIDER, "reasoning": False},
    "A": {"model": se.TIER_AB_MODEL, "provider": se.TIER_AB_PROVIDER, "reasoning": False},
    "B": {"model": se.TIER_AB_MODEL, "provider": se.TIER_AB_PROVIDER, "reasoning": True},
}


def categorical(diff):
    """The classification-shaped answers, flattened. Free-text fields are deliberately absent -
    see the module docstring."""
    diff = diff or {}
    scene = diff.get("scene_update") or {}
    beat = diff.get("beat") or {}
    revelations = diff.get("revelations") or {}
    inventory = diff.get("inventory") or {}
    out = {
        "location": scene.get("location"),
        "entity_interaction": bool(scene.get("entity_interaction")),
        "threat_present": bool(scene.get("threat_present")),
        "beat.type": beat.get("type"),
        "beat.intensity": beat.get("intensity"),
        "failure_triggered": diff.get("failure_triggered"),
        "revealed": tuple(sorted(revelations.get("revealed") or [])),
        "eligible": tuple(sorted(revelations.get("eligible") or [])),
        "subplot_beats": tuple(sorted((diff.get("subplot_beats") or {}).items())),
        # counts only: the labels are prose and will never match, but "did it report an item
        # at all" is a classification and does.
        "n_items_gained": len(inventory.get("gained") or []),
        "n_social": len(diff.get("social") or []),
        # stat_changes is the last v2-shaped field: a delta map whose NUMBERS the model
        # chooses, which is what P-7 exists to prevent. Compared exactly, because "does the
        # model reproduce its own arithmetic" is the whole question about it.
        "stat_changes": tuple(sorted((diff.get("stat_changes") or {}).items())),
        # and the same question with the magnitudes thrown away: did it at least agree on
        # which axes moved and in which direction?
        "stat_axes_moved": tuple(sorted(
            (k, (v > 0) - (v < 0)) for k, v in (diff.get("stat_changes") or {}).items()
            if isinstance(v, (int, float))
        )),
    }
    return out


def turns_from(path):
    save = json.load(open(path, encoding="utf-8"))
    entries = save["history"].get("full_transcript", []) + save["history"]["recent_turns"]
    out = []
    for entry in entries:
        if entry.startswith("Player: "):
            split = entry.find("\nNarrator: ")
            out.append((entry[len("Player: "):split], entry[split + len("\nNarrator: "):]))
    return out, save


def run_one(slug, save, action, narration, config):
    """One observation pass, on one tier, against a deep copy so nothing leaks between runs."""
    story = state_store.load_template(slug)
    ctx = {"story": story, "state": copy.deepcopy(save)}
    original = se.call_llm_json
    se.call_llm_json = lambda prompt, **kw: original(prompt, **{**kw, **config})
    started = time.time()
    try:
        diff = se.update_progress_from_turn(ctx, action, narration)
    except Exception as exc:                                   # noqa: BLE001
        return {"error": str(exc)[:120], "seconds": time.time() - started}
    finally:
        se.call_llm_json = original
    return {"fields": categorical(diff), "seconds": time.time() - started}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--turns", type=int, default=10, help="turns sampled per save")
    ap.add_argument("--repeats", type=int, default=2, help="runs per config per turn")
    ap.add_argument("--configs", default="C,A,B")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out")
    args = ap.parse_args()
    configs = [c.strip() for c in args.configs.split(",") if c.strip()]

    jobs = []
    for slug, path in SAVES:
        turns, save = turns_from(path)
        if not turns:
            continue
        step = max(1, len(turns) // args.turns)
        for index in list(range(0, len(turns), step))[:args.turns]:
            action, narration = turns[index]
            for name in configs:
                for repeat in range(args.repeats):
                    jobs.append((f"{slug}#{index}", name, repeat, slug, save, action, narration))

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(
            lambda j: {"turn": j[0], "config": j[1], "repeat": j[2],
                       **run_one(j[3], j[4], j[5], j[6], CONFIGS[j[1]])}, jobs))

    by = {}
    for r in results:
        by.setdefault((r["turn"], r["config"]), []).append(r)

    print(f"{len(results)} calls over {len({r['turn'] for r in results})} turns, "
          f"{args.repeats} repeats per config\n")
    print(f"{'config':<8}{'p50 s':>8}{'errors':>8}{'self-agree':>13}")
    for name in configs:
        runs = [r for r in results if r["config"] == name]
        times = sorted(r["seconds"] for r in runs)
        errors = sum(1 for r in runs if "error" in r)
        same = total = 0
        for (turn, cfg), group in by.items():
            if cfg != name or len(group) < 2 or any("error" in g for g in group):
                continue
            total += 1
            same += all(g["fields"] == group[0]["fields"] for g in group[1:])
        rate = f"{100 * same / total:.0f}% ({same}/{total})" if total else "n/a"
        print(f"{name:<8}{statistics.median(times):>8.2f}{errors:>8}{rate:>13}")

    base = configs[0]
    for other in configs[1:]:
        agree = total = 0
        fielded = {}
        for (turn, cfg), group in by.items():
            if cfg != base or "error" in group[0]:
                continue
            rival = by.get((turn, other))
            if not rival or "error" in rival[0]:
                continue
            total += 1
            agree += group[0]["fields"] == rival[0]["fields"]
            for key, value in group[0]["fields"].items():
                if value != rival[0]["fields"][key]:
                    fielded[key] = fielded.get(key, 0) + 1
        if total:
            print(f"\n{base} vs {other}: whole-answer agreement {100 * agree / total:.0f}% "
                  f"({agree}/{total})")
            for key, count in sorted(fielded.items(), key=lambda kv: -kv[1]):
                print(f"    disagreed on {key}: {count}/{total}")
    if args.out:
        json.dump(results, open(args.out, "w"), indent=1, default=str)
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Behavioural fingerprint of the stat/resource mechanic - the engine v2 phase 2 gate.

docs/ENGINE_V2_PHASES.md phase 2. The gate was originally "the three stat test files pass
unmodified", which turned out to be unachievable for any port that moves where
configuration lives: all three author `mechanics.stats` with no engine key, and one of them
asserts the no-block fallback outright. That clause was buying "no silent behaviour
change"; this buys the same thing and more, by exercising behaviour instead of pinning the
shape of a test.

Run it before a port, run it after, diff the two. Identical output means the port changed
no observable stat behaviour on any target - including real saves, which is where this
project's actual bugs have always surfaced (the 70-turn SYNC drift behind P-7, the
2,912-word summary behind the code-side cap).

What it probes, per target:
  - clamping against a scripted delta sequence, including both bounds and an unknown axis
  - whether the observation schema offers stat_changes at all
  - the narration prompt's visibility markers and readout-token instruction
  - the rendered readout line, and readout substitution into narration

Usage:
    python3 scripts/equivalence_probe.py            # fingerprint to stdout
    python3 scripts/equivalence_probe.py --saves    # also probe real saves under data/
"""
import argparse
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "test"))
from _llm_stubs import RecordingLLM, load_story_engine  # noqa: E402

se = load_story_engine()

FIXTURES_DIR = os.path.join(REPO_ROOT, "test", "fixtures")
STORY_ROOTS = [os.path.join(REPO_ROOT, "stories"), os.path.join(REPO_ROOT, "stories", "private")]

# Deltas chosen to cross both bounds and to poke an axis the story never declared. The
# unknown axis matters: "the model can never introduce a new stat axis" is a CLAUDE.md
# invariant, so a port that quietly started accepting one would be a real regression.
DELTA_SEQUENCE = [
    {"__probe_a": +5, "__probe_b": -3},
    {"__probe_a": -999},
    {"__probe_a": +9999},
    {"__probe_b": +4, "__never_declared": +7},
]

BASE_DIFF = {
    "subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [],
    "items_gained": [], "items_lost": [], "new_characters": [],
    "scene_update": {"location": "", "summary": "unchanged", "present_npcs": []},
}

NARRATION_MARKERS = [
    "Stats (",                      # the PLAYER-line stat block, whatever its label
    "may be stated directly",       # the visible-stats footer instruction
    "never state a raw number",     # the opaque-stats footer instruction
    "[[STATS]]",                    # the readout token instruction
]


def _observation_prompt(ctx):
    rec = RecordingLLM(lambda p: dict(BASE_DIFF))
    original = se.call_llm_json
    try:
        se.call_llm_json = rec
        se.update_progress_from_turn(ctx, "a player action", "a paragraph of narration.")
    finally:
        se.call_llm_json = original
    return rec.prompts[-1]


def _apply(ctx, stat_changes):
    """One turn's worth of stat_changes through the real state-update path."""
    rec = RecordingLLM(lambda p: {**BASE_DIFF, "stat_changes": stat_changes})
    original = se.call_llm_json
    try:
        se.call_llm_json = rec
        se.update_progress_from_turn(ctx, "act", "narrate.")
    finally:
        se.call_llm_json = original


def probe(ctx, label):
    out = {"target": label}

    # Rename the story's real axes onto the probe's names so one delta sequence works for
    # every target regardless of what its stats are called. Falls back to seeding two axes
    # for a story that has none, which is how "this story has no stats" gets fingerprinted.
    stats = ctx["state"]["protagonist"].get("stats", {})
    out["declared_axes"] = sorted(stats)
    real = sorted(stats)
    ctx["state"]["protagonist"]["stats"] = {
        "__probe_a": stats[real[0]] if real else 0,
        **({"__probe_b": stats[real[1]]} if len(real) > 1 else {}),
    } if real else {}

    out["stats_offered"] = "stat_changes" in _observation_prompt(ctx)

    trace = []
    for deltas in DELTA_SEQUENCE:
        _apply(ctx, deltas)
        trace.append(dict(sorted(ctx["state"]["protagonist"]["stats"].items())))
    out["clamp_trace"] = trace

    prompt = se.build_system_prompt(ctx)
    out["narration_markers"] = {m: (m in prompt) for m in NARRATION_MARKERS}
    out["readout_line"] = se.render_stat_readout(ctx)
    out["readout_applied"] = se.apply_stat_readouts(ctx, "before [[STATS]] after")
    return out


def ctx_from_template(story, label):
    ctx = {"story": se.state_store.freeze(story), "state": se.state_store.new_save_state(story, label)}
    # Take the first option of every creation step, so a story whose stats arrive that way
    # is probed with its stats actually present.
    while True:
        step = se.next_pending_creation_step(ctx)
        if not step:
            break
        se.apply_creation_choice(ctx, step["key"], step["options"][0]["id"])
    return ctx


def targets():
    seen = set()
    for root in STORY_ROOTS:
        if not os.path.isdir(root):
            continue
        for slug in sorted(os.listdir(root)):
            path = os.path.join(root, slug, "template.json")
            if slug != "private" and slug not in seen and os.path.isfile(path):
                seen.add(slug)
                with open(path) as f:
                    yield slug, json.load(f)
    for name in sorted(os.listdir(FIXTURES_DIR)):
        if name.endswith(".json"):
            with open(os.path.join(FIXTURES_DIR, name)) as f:
                yield name[: -len(".json")], json.load(f)


def save_targets():
    """Real saves under data/saves, probed against their own story's template. Read-only:
    the state is deep-copied out of the file and never written back."""
    saves_dir = os.path.join(REPO_ROOT, "data", "saves")
    if not os.path.isdir(saves_dir):
        return
    for user in sorted(os.listdir(saves_dir)):
        for fname in sorted(os.listdir(os.path.join(saves_dir, user))):
            if not fname.endswith(".json"):
                continue
            slug = fname[: -len(".json")]
            with open(os.path.join(saves_dir, user, fname)) as f:
                state = json.load(f)
            template = None
            for root in STORY_ROOTS:
                path = os.path.join(root, slug, "template.json")
                if os.path.isfile(path):
                    with open(path) as tf:
                        template = json.load(tf)
                    break
            if template is None:
                continue
            yield f"save:{user}/{slug}", {"story": se.state_store.freeze(template), "state": state}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--saves", action="store_true", help="also probe real saves under data/")
    args = ap.parse_args()

    results = [probe(ctx_from_template(story, label), label) for label, story in targets()]
    if args.saves:
        results += [probe(ctx, label) for label, ctx in save_targets()]
    print(json.dumps(results, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()

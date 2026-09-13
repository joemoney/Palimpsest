#!/usr/bin/env python3
"""Engine v2 phase 0: the baseline every later phase is measured against.

docs/ENGINE_V2_PHASES.md phase 0. Two claims in docs/ENGINE_V2_SPEC.md are only
checkable if this runs before anything is ported:

  §5.1  porting a mechanic should *shrink* the observation schema, because an engine
        that owns a mechanic no longer has to ask the model about it. "Measure the field
        count before and after each port" needs a before.
  §12.4 the six-fields-per-shard split point of §5.4 is an admitted guess. Phase 7's
        go/no-go wants a real number instead.

Offline by design, same as test/: reuses test/_llm_stubs so it needs no API key, no
network, and no pip-installed dependencies. The observation prompt is captured by
monkeypatching call_llm_json, exactly as test_genre_conformance.py does - nothing here
calls a model.

Every target is built through freeze/new_save_state rather than load_state, so running
this never creates or touches a real save under data/saves/.

Usage:
    python3 scripts/measure_baseline.py            # human-readable
    python3 scripts/measure_baseline.py --markdown # table for pasting into the phases doc
    python3 scripts/measure_baseline.py --json     # machine-readable, for diffing later
"""
import argparse
import hashlib
import json
import os
import re
import statistics
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "test"))

from _llm_stubs import RecordingLLM, load_story_engine  # noqa: E402

se = load_story_engine()

FIXTURES_DIR = os.path.join(REPO_ROOT, "test", "fixtures")
# Both story roots (see state_store.story_roots): stories/ is public and in-repo,
# stories/private/ is the submodule of private story content. Anyone without the
# submodule checked out measures the public catalog and the fixtures, which is enough for
# every comparison this script exists to support.
STORY_ROOTS = [os.path.join(REPO_ROOT, "stories"),
               os.path.join(REPO_ROOT, "stories", "private")]
PERF_STATS = os.path.join(REPO_ROOT, "data", "perf_stats.json")

# No tokenizer is installed and the offline suite deliberately has no pip dependencies, so
# token counts here are chars/CHARS_PER_TOKEN. It is an estimate and is labelled as one
# everywhere it is printed - the number that actually matters for §5.1 is the field count,
# which is exact. Keep the divisor fixed so successive runs stay comparable to each other
# even though neither is comparable to a real tokenizer.
CHARS_PER_TOKEN = 4

# Every state-update schema field is emitted by update_progress_from_turn as a line
# starting with exactly two spaces and a quoted key (the schema_fields list, joined with
# ",\n"). Nothing else in that prompt is indented that way. The extractor returns the names
# rather than just a count so a stray match is visible in the output instead of silently
# inflating the total.
SCHEMA_FIELD_RE = re.compile(r'^  "([a-z_]+)":', re.MULTILINE)

# A turn's worth of empty diff - enough for update_progress_from_turn to apply cleanly
# without inventing state that would skew a later measurement.
EMPTY_DIFF = {
    "subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [],
    "inventory": {"gained": [], "used": []}, "new_characters": [],
    "scene_update": {"location": "", "summary": "unchanged", "present_npcs": []},
}


def _ctx_from_template(story, label):
    return {"story": se.state_store.freeze(story),
            "state": se.state_store.new_save_state(story, label)}


def _apply_creation(ctx):
    """Take the first option of every character_creation step, in order.

    A story whose stats arrive through character_creation (new_babel, the_missing_core)
    has no stats at all at turn 0, so a turn-0-only measurement would report no
    stat_changes field for it and understate the schema it actually runs with. Measuring
    both states is the honest version. Returns the number of steps applied."""
    applied = 0
    while True:
        step = se.next_pending_creation_step(ctx)
        if not step:
            return applied
        se.apply_creation_choice(ctx, step["key"], step["options"][0]["id"])
        applied += 1


def _observation_prompt(ctx):
    """The state-update prompt as update_progress_from_turn would build it, captured
    without calling a model."""
    recorder = RecordingLLM(lambda p: dict(EMPTY_DIFF))
    original = se.call_llm_json
    try:
        se.call_llm_json = recorder
        se.update_progress_from_turn(ctx, "a representative player action",
                                     "a representative paragraph of narration.")
    finally:
        se.call_llm_json = original
    return recorder.prompts[-1]


def measure(story, label):
    ctx = _ctx_from_template(story, label)
    narration = se.build_system_prompt(ctx)
    observation = _observation_prompt(ctx)
    fields = SCHEMA_FIELD_RE.findall(observation)

    row = {
        "target": label,
        # The phase gates that say "byte-identical" (phase 1) and "behaviour identical"
        # (phase 5) need something to compare, and a hash is the only comparison that
        # cannot be fudged. Note these are only stable because of the sorted() in
        # _existing_character_names - a set-to-list there used to make every prompt
        # containing EXISTING CHARACTERS render in a different order per process.
        "narration_sha": hashlib.sha256(narration.encode()).hexdigest()[:16],
        "observation_sha": hashlib.sha256(observation.encode()).hexdigest()[:16],
        "mechanics": sorted(k for k, v in story.get("mechanics", {}).items() if v),
        "narration_chars": len(narration),
        "narration_tokens_est": len(narration) // CHARS_PER_TOKEN,
        "observation_chars": len(observation),
        "observation_tokens_est": len(observation) // CHARS_PER_TOKEN,
        "observation_fields": len(fields),
        "observation_field_names": fields,
        "after_creation": None,
    }

    # Re-measure post-character-creation where the story has one - see _apply_creation.
    ctx2 = _ctx_from_template(story, label)
    if _apply_creation(ctx2):
        observation2 = _observation_prompt(ctx2)
        fields2 = SCHEMA_FIELD_RE.findall(observation2)
        row["after_creation"] = {
            "narration_chars": len(se.build_system_prompt(ctx2)),
            "observation_chars": len(observation2),
            "observation_fields": len(fields2),
            "observation_field_names": fields2,
        }
    return row


def collect():
    rows = []
    seen = set()
    for root in STORY_ROOTS:
        if not os.path.isdir(root):
            continue
        for slug in sorted(os.listdir(root)):
            path = os.path.join(root, slug, "template.json")
            if slug not in seen and os.path.exists(path):
                seen.add(slug)
                with open(path) as f:
                    rows.append(measure(json.load(f), slug))
    for name in sorted(os.listdir(FIXTURES_DIR)):
        if name.endswith(".json"):
            with open(os.path.join(FIXTURES_DIR, name)) as f:
                rows.append(measure(json.load(f), name[: -len(".json")]))
    return rows


def perf_p50s():
    """Median per _timed label from the real data/perf_stats.json - read directly rather
    than through state_store, whose DATA_DIR the test stubs redirect to a tmp dir."""
    if not os.path.exists(PERF_STATS):
        return {}
    with open(PERF_STATS) as f:
        stats = json.load(f)
    return {label: {"p50": round(statistics.median(samples), 2), "samples": len(samples)}
            for label, samples in sorted(stats.items()) if samples}


def print_human(rows, p50s):
    for row in rows:
        print(f"\n{row['target']}")
        print(f"  mechanics authored : {', '.join(row['mechanics']) or 'none'}")
        print(f"  narration prompt   : {row['narration_chars']:,} chars "
              f"(~{row['narration_tokens_est']:,} tokens est.)")
        print(f"  observation prompt : {row['observation_chars']:,} chars "
              f"(~{row['observation_tokens_est']:,} tokens est.)")
        print(f"  observation fields : {row['observation_fields']}")
        print(f"    {', '.join(row['observation_field_names'])}")
        after = row["after_creation"]
        if after:
            added = [f for f in after["observation_field_names"]
                     if f not in row["observation_field_names"]]
            print(f"  after character_creation: {after['observation_fields']} fields "
                  f"(+{', '.join(added) or 'none'}), "
                  f"observation {after['observation_chars']:,} chars")
    print("\nper-call p50 (data/perf_stats.json)")
    for label, s in p50s.items():
        print(f"  {label:<24} {s['p50']:>6.2f}s  (n={s['samples']})")
    if not p50s:
        print("  none recorded")


def print_markdown(rows, p50s):
    names = [r["target"] for r in rows]
    def line(header, fn):
        return "| " + header + " | " + " | ".join(str(fn(r)) for r in rows) + " |"
    print("| Metric | " + " | ".join(f"`{n}`" for n in names) + " |")
    print("|---" * (len(names) + 1) + "|")
    print(line("Observation fields (turn 0)", lambda r: r["observation_fields"]))
    print(line("Observation fields (post-creation)",
               lambda r: r["after_creation"]["observation_fields"] if r["after_creation"] else "—"))
    print(line("Observation prompt (chars)", lambda r: f"{r['observation_chars']:,}"))
    print(line("System prompt (chars)", lambda r: f"{r['narration_chars']:,}"))
    print(line("System prompt (~tokens)", lambda r: f"{r['narration_tokens_est']:,}"))
    print("\n| `_timed` label | p50 | samples |")
    print("|---|---|---|")
    for label, s in p50s.items():
        print(f"| `{label}` | {s['p50']:.2f}s | {s['samples']} |")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--markdown", action="store_true", help="markdown tables")
    ap.add_argument("--hashes", action="store_true",
                    help="only the prompt hashes, for the byte-identical phase gates; "
                         "diff two runs of this to prove a change touched no prompt")
    args = ap.parse_args()

    rows, p50s = collect(), perf_p50s()
    if args.hashes:
        for row in rows:
            print(f"{row['target']:<20} narration={row['narration_sha']} "
                  f"observation={row['observation_sha']}")
    elif args.json:
        print(json.dumps({"targets": rows, "p50": p50s}, indent=2))
    elif args.markdown:
        print_markdown(rows, p50s)
    else:
        print_human(rows, p50s)


if __name__ == "__main__":
    main()

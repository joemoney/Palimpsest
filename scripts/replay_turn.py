#!/usr/bin/env python3
"""Re-runs a already-played turn's state-update pass against the current prompt code, so a
prompt change can be judged on the exact narration that previously mishandled it instead of
by playing another long session and hoping the situation recurs.

Built for Phase 0's Task 1 (docs/PHASE_0_FIX_PLAN.md): turns 18 and 23 of the 24-turn
new_babel playthrough both plainly satisfied revelation trigger frag_0001 and neither fired
(docs/PHASE_0_GATE_REPORT.md §4). Those two turns are a fixed, known-answer regression
sample - replaying them costs two LLM calls and answers "did the fix work" directly, where
a fresh playthrough costs hours and only answers it if the player happens to attempt a
computation again.

This makes REAL LLM calls - that is the entire point, since the failure being measured is a
model judgement, not code behaviour. The offline suite already proves the prompt contains
the right text; only a live call proves the model acts on it. Needs a working .env, same as
running the app.

READ-ONLY with respect to the save. update_progress_from_turn mutates the ctx it is handed,
so it is handed a copy: ctx["state"] is deep-copied and ctx["story"] is passed through by
reference (it is a FrozenDict and cannot be written to anyway - deep-copying a dict subclass
that raises on __setitem__ is a reconstruction hazard for no benefit). save_state is never
called from here.

Usage:
  scripts/replay_turn.py --user <id> --turn 18 --turn 23 --unreveal --expect frag_0001
  scripts/replay_turn.py --user <id> --list
  scripts/replay_turn.py --user <id> --turn 18 --show-prompt     # no LLM call

Gate 0.2 (classifier agreement) needs the same three moves this makes - pull a stored turn,
rebuild the ctx it happened in, run one prompt against it - so extend this rather than
writing a second copy of that plumbing. The beat-classification prompt does not exist yet
(Phase 6.2), so no classify mode is stubbed here.
"""

import argparse
import copy
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

import state_store  # noqa: E402
import story_engine  # noqa: E402


def all_turns(ctx: dict) -> list:
    """The full played history oldest-first, as app.py's history view assembles it:
    full_transcript holds everything already rolled out of recent_turns, recent_turns holds
    the tail. Index 0 is the opening scene (narration only, no player action), so index N is
    turn N - which is what makes --turn 18 mean the same "turn 18" the gate report cites."""
    history = ctx["state"]["history"]
    return list(history.get("full_transcript", [])) + list(history.get("recent_turns", []))


def split_turn(entry: str) -> tuple:
    """Stored turns are "Player: <action>\\nNarrator: <narration>"; the opening scene has no
    Player half. Splits on the first "\\nNarrator: " rather than by line, since narration is
    multi-paragraph and contains newlines of its own."""
    marker = "\nNarrator: "
    if entry.startswith("Narrator: "):
        return "", entry[len("Narrator: "):]
    if marker not in entry:
        return "", entry
    action, narration = entry.split(marker, 1)
    return action[len("Player: "):] if action.startswith("Player: ") else action, narration


def rewind_flags(state: dict, turn_no: int) -> int:
    """Drops flags set at or after the turn being replayed, using the turn_set each carries in
    flags["meta"].

    Without this the replay is not just imprecise but actively misleading. A save loaded today
    holds the flags as of the LAST turn played, so replaying turn 18 would show the model
    "first_listen_completed: true" - set by turn 18 itself - while asking whether the narration
    satisfies a trigger reading "the FIRST TIME the protagonist attempts...". The model could
    correctly answer no on evidence that did not exist when the turn was really played, and the
    result would look like the prompt fix failing.

    Only active flags can be rewound: flags["archive"] stores bare name -> value with no meta,
    so a flag retired after the replayed turn cannot be dated and is not restored. That leaves
    the replay slightly *under*-informed rather than over-informed, which is the safe direction.
    Scene, subplot progress, inventory and relationship scores are likewise still end-state; they
    carry no per-turn history to rewind and do not bear on trigger evaluation the way flags do.
    """
    flags = state["protagonist"]["flags"]
    meta = flags.get("meta", {})
    stale = [
        name for name in list(flags["active"])
        if isinstance(meta.get(name), dict) and (meta[name].get("turn_set") or 0) >= turn_no
    ]
    for name in stale:
        flags["active"].pop(name, None)
        meta.pop(name, None)
    return len(stale)


def replay_ctx(ctx: dict, unreveal: bool, turn_no: int, rewind: bool) -> tuple:
    """A throwaway ctx so the real save is never touched. --unreveal clears the revealed map,
    which is what puts a fragment back into UNREVEALED MEMORY FRAGMENT TRIGGERS and therefore
    back in front of the model; without it, replaying a turn from after a fragment fired
    would silently test nothing."""
    replay = {"story": ctx["story"], "state": copy.deepcopy(ctx["state"])}
    if unreveal:
        replay["state"]["plot"]["revelations_revealed"] = {}
    dropped = rewind_flags(replay["state"], turn_no) if rewind else 0
    return replay, dropped


def use_tier_b(ctx: dict, action: str, narration: str) -> dict:
    """Runs the state-update pass on Tier B (same flagship model, reasoning ON) instead of its
    normal Tier C. This is the discriminator for *why* a trigger did not fire: if Tier B fires
    it and Tier C does not, the trigger is satisfiable and the cheap model is the limit, which
    is a tier decision. If neither fires it, the model is not the variable - the trigger is
    authored in terms the narration never matches, which is a content fix and no amount of tier
    money solves it. See docs/PHASE_0_GATE_REPORT.md §4.1."""
    real = story_engine.call_llm_json
    story_engine.call_llm_json = lambda prompt, **kw: real(
        prompt,
        model=story_engine.TIER_AB_MODEL,
        provider=story_engine.TIER_AB_PROVIDER,
        reasoning=True,
    )
    try:
        return story_engine.update_progress_from_turn(ctx, action, narration)
    finally:
        story_engine.call_llm_json = real


def capture_prompt(ctx: dict, action: str, narration: str) -> str:
    """Builds the state-update prompt without spending a call, by standing in for
    call_llm_json the way the offline tests do. Returns {} to the caller so the rest of
    update_progress_from_turn runs harmlessly against the throwaway ctx."""
    captured = {}
    real = story_engine.call_llm_json

    def spy(prompt, **kwargs):
        captured["prompt"] = prompt
        return {}

    story_engine.call_llm_json = spy
    try:
        story_engine.update_progress_from_turn(ctx, action, narration)
    finally:
        story_engine.call_llm_json = real
    return captured.get("prompt", "")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--user", default=state_store.DEFAULT_USER_ID)
    ap.add_argument("--story", default=state_store.DEFAULT_STORY_SLUG)
    ap.add_argument("--turn", type=int, action="append", default=[],
                    help="turn number to replay; repeatable")
    ap.add_argument("--list", action="store_true", help="list turns and exit")
    ap.add_argument("--unreveal", action="store_true",
                    help="clear revelations_revealed first, so every trigger is offered")
    ap.add_argument("--show-prompt", action="store_true",
                    help="print the built prompt instead of calling the LLM")
    ap.add_argument("--tier", choices=["b", "c"], default="c",
                    help="c (default, normal Tier C) or b (flagship + reasoning) to test whether "
                         "the model or the authored trigger is the limiting factor")
    ap.add_argument("--no-rewind", action="store_true",
                    help="keep end-state flags instead of dropping those set at/after the turn")
    ap.add_argument("--expect", action="append", default=[],
                    help="fragment id that should fire; exit 1 if it does not. Repeatable")
    args = ap.parse_args()

    ctx = state_store.load_state(args.user, args.story)
    turns = all_turns(ctx)

    if args.list:
        for i, entry in enumerate(turns):
            action, narration = split_turn(entry)
            label = action or "(opening scene)"
            print(f"{i:>3}  {label[:96]}")
        print(f"\n{len(turns) - 1} played turns (index 0 is the opening scene).")
        return 0

    if not args.turn:
        ap.error("give at least one --turn (or --list)")

    fired = set()
    for turn_no in args.turn:
        if not 0 <= turn_no < len(turns):
            print(f"turn {turn_no} out of range (0..{len(turns) - 1})", file=sys.stderr)
            return 2

        action, narration = split_turn(turns[turn_no])
        replay, dropped = replay_ctx(ctx, args.unreveal, turn_no, not args.no_rewind)
        unrevealed = [
            r["id"] for r in replay["story"].get("mechanics", {}).get("revelations", [])
            if r["id"] not in replay["state"]["plot"]["revelations_revealed"]
        ]

        print(f"\n{'=' * 70}\nTURN {turn_no}")
        print(f"{'=' * 70}")
        print(f"ACTION: {action[:200] or '(opening scene)'}")
        print(f"NARRATION: {len(narration.split())} words")
        print(f"UNREVEALED TRIGGERS OFFERED: {unrevealed or 'none'}")
        print(f"TIER: {args.tier.upper()}")
        print(f"FLAGS REWOUND: dropped {dropped} set at/after turn {turn_no}"
              if not args.no_rewind else "FLAGS REWOUND: no (end-state flags, may mislead)")

        if args.show_prompt:
            print(f"\n--- prompt ---\n{capture_prompt(replay, action, narration)}")
            continue

        try:
            if args.tier == "b":
                diff = use_tier_b(replay, action, narration)
            else:
                diff = story_engine.update_progress_from_turn(replay, action, narration)
        except story_engine.LLMUnavailableError as e:
            print(f"\nLLM UNAVAILABLE: {e}", file=sys.stderr)
            return 3

        revealed = diff.get("memory_fragments_revealed", [])
        fired.update(revealed)
        print(f"\nmemory_fragments_revealed: {revealed or '[]  <-- nothing fired'}")
        print(f"full diff: {json.dumps(diff, indent=2)[:1500]}")

    if args.expect:
        missing = [f for f in args.expect if f not in fired]
        print(f"\n{'=' * 70}")
        if missing:
            print(f"FAIL: expected {args.expect}, fired {sorted(fired) or 'nothing'}")
            print("The prompt fix did not change the outcome on a turn that plainly")
            print("satisfies the trigger. Per the fix plan, the next step is a tier")
            print("decision (gate 0.2), not another rewording.")
            return 1
        print(f"PASS: {args.expect} fired.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

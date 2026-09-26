"""CR-04 creation-derived variables (Story_Mechanics_Update.md CR-04).

A top-level `derived` list, evaluated once when the last character-creation step completes:
first rule whose `when` holds wins, and its `set` fixes named values for the whole story.
Any narrator-visible string may then say `{var}` - "Lark Ferris is {lark_is} ({lark_pron})" -
instead of asking the narrator to work out a fact from a long conditional rule every turn
(P-7: determinism belongs to the engine).

This module is the pure half, shared by the authoring tool now and the engine when it lands:
`resolve()` picks the values for one set of creation choices, `combinations()` enumerates every
set a player could make, and `uses()` finds each `{var}` a template writes and where. It reads
conditions through `conditions.evaluate` (D3: no second implementation), never writes, calls
no LLM.

**The engine half is not built.** Nothing substitutes `{var}` in a prompt yet, so
`mechanics.validate()` refuses a template that authors `derived` - loud rather than a narrator
handed `{lark_is}` verbatim (build order: a story may be unplayable, loudly).

**`when` is CLOSED** (D2): an unknown step or option reads false, so a typo falls through to
the next rule instead of fixing the wrong value for the whole story. Lint (L10) catches the typo.
"""
import itertools
import re

import conditions

# `{name}` tokens. Only identifiers: prose braces and JSON-ish text are not placeholders.
TOKEN = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

# Placeholders the engine already fills, by where they may appear. A derived value may not take
# one of these names: which of the two a `{name}` meant would depend on the field it is in.
BUILTIN = {
    "plot.opening_scene": {"player_name"},
    "mechanics.pacing_loop.rules": {"counter_value", "deferrals", "unspent_leverage", "queued_reveal"},
}
RESERVED = set().union(*BUILTIN.values())

# Not scanned: format strings with their own fields (the stat readout), and text no prompt ever
# carries - author notes (`_` keys), canon, a character's role, the synopsis, positions, and the
# rules themselves.
_SKIP_PREFIXES = ("mechanics.stats.readout", "derived", "meta.synopsis", "_storyboard")
_SKIP_KEYS = ("canon", "role")

# Past this many creation combinations the table is not enumerated (lint says so instead).
MAX_COMBINATIONS = 512


def rules(story) -> list:
    return [r for r in (story or {}).get("derived") or [] if isinstance(r, dict)]


def variables(story) -> list:
    """Every name some rule sets, in first-seen order."""
    out = []
    for r in rules(story):
        for name in (r.get("set") or {}):
            if name not in out:
                out.append(name)
    return out


def resolve(story, ctx) -> tuple:
    """`(index, values)`: the first rule whose `when` holds against `ctx` (CLOSED), and a copy
    of its `set`; `(None, {})` when none does. An absent or empty `when` always holds."""
    for i, r in enumerate(rules(story)):
        if conditions.satisfied(r.get("when") or {}, ctx, conditions.CLOSED):
            return i, dict(r.get("set") or {})
    return None, {}


def combinations(story):
    """Every `{step key: option id}` a player can finish character creation with, or None when
    there are more than MAX_COMBINATIONS. A story with no creation has exactly one: `{}`."""
    steps = [(s.get("key"), [o.get("id") for o in s.get("options") or [] if isinstance(o, dict)])
             for s in (story or {}).get("character_creation") or [] if isinstance(s, dict) and s.get("key")]
    steps = [(k, opts) for k, opts in steps if opts]
    total = 1
    for _, opts in steps:
        total *= len(opts)
        if total > MAX_COMBINATIONS:
            return None
    return [dict(zip([k for k, _ in steps], combo)) for combo in itertools.product(*[o for _, o in steps])]


def creation_ctx(story, choices: dict) -> dict:
    """The ctx `when` is evaluated against: the choices, and the stats the save would be seeded
    with (protagonist.stats, then each chosen option's starting_stats) - which is all a save
    holds at the moment creation completes."""
    stats = dict((story.get("protagonist") or {}).get("stats") or {})
    for step in story.get("character_creation") or []:
        for opt in step.get("options") or []:
            if isinstance(opt, dict) and choices.get(step.get("key")) == opt.get("id"):
                stats.update(opt.get("starting_stats") or {})
    return {"story": story, "state": {"protagonist": {"creation_choices": dict(choices), "stats": stats}}}


def table(story):
    """`[{choices, rule, values}]` for every combination, or None when there are too many."""
    combos = combinations(story)
    if combos is None:
        return None
    out = []
    for choices in combos:
        index, values = resolve(story, creation_ctx(story, choices))
        out.append({"choices": choices, "rule": index, "values": values})
    return out


def uses(story) -> list:
    """`[(path, name)]` for every `{name}` in a string the engine would ever send to a model."""
    out = []

    def walk(value, path, key):
        if key in _SKIP_KEYS or (key or "").startswith("_"):
            return
        if any(path == p or path.startswith(p + ".") or path.startswith(p + "[") for p in _SKIP_PREFIXES):
            return
        if isinstance(value, dict):
            for k, v in value.items():
                walk(v, f"{path}.{k}" if path else k, k)
        elif isinstance(value, list):
            for i, v in enumerate(value):
                walk(v, f"{path}[{i}]", key)
        elif isinstance(value, str):
            for name in TOKEN.findall(value):
                out.append((path, name))

    walk(story or {}, "", None)
    return out


def builtin_for(path: str) -> set:
    """The engine-filled placeholders legal at `path`."""
    return set().union(*[names for prefix, names in BUILTIN.items()
                         if path == prefix or path.startswith(prefix + ".") or path.startswith(prefix + "[")])

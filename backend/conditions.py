"""CR-02's shared condition grammar (Authoring Tool decision D2; Story_Mechanics_Update.md CR-02).

One evaluator for every "is X true about the state?" question: gates, act `requires`,
`activate_when`, and the ending funnel's `viable_while` / `ready_when` / `fail_when` /
waypoint `done_when`. Pure - it reads `ctx` and never writes it, calls no LLM, and touches no
disk - so the authoring tool can run it against a hand-built sample state (D3) and get the
answer the engine would.

**Polarity is a required argument, and it is the whole point of this module being separate
from `gate.satisfied()`.** A condition can name something that does not exist: a flag nobody
declared, a stat axis the save lacks, a revelation the story dropped. What that *means* depends
on what the answer is used for, because the two ways of being wrong cost different things:

  - `OPEN` (unknown reads *true*): gates, act `requires`, `activate_when`, `viable_while`. A typo
    should cost a locked door, never a save whose main thread can never advance - and
    `viable_while` reading false would prune an ending, which is permanent.
  - `CLOSED` (unknown reads *false*): `ready_when`, `done_when`, `fail_when`. A typo there would
    commit an ending, mark a waypoint planted, or fail a thread - all permanent.

The caller states which it wants at every call; there is no default, because a default is the
silent choice this design exists to prevent. `viable_while` is not among D2's three named
fail-closed fields; it is OPEN here on the same test (which wrong answer cannot be undone).

**Leaves.** `stat` (+`gte`/`lte`/`between`), `tier`, `tier_reached`, `revealed`, `flag`,
`item_tag`, `relationship` (+`tier_gte`/`tier_lte`/`peak_gte`/`between`), `leverage_kind` /
`leverage_label_matches`, `creation`, `turn_gte`, `act_gte`, `subplot_status`,
`waypoints_done`. Combinators `all` / `any` / `not`, nested at most `MAX_DEPTH` deep. The leaf
list is CR-02's and nothing else: gate.py's original warning about becoming a general
expression language moved here with the code.

`bond` (CR-11) is in the grammar and reads as *unknown* - the engine that owns bond state does
not exist yet (build order: the storyboard leads).

**State this reads that the engine does not write yet**, each with a sound lower bound so a
condition still means something meanwhile: `mechanics.stats.tier_log` (a tier reached and then
left - falls back to "the current value is at or above that tier's threshold"), a
relationship's `peak` (falls back to the current score), and `mechanics.endings.waypoints_done`
(CR-05's ledger of planted waypoints - absent, nothing is planted).

**Legacy forms** (`stat: {axis, at_least}`, `revelation`, a character-object `relationship`)
are still accepted; `normalize()` rewrites them into the canonical spelling and `evaluate()`
calls it, so existing gates need no template edit.

**Proximity** in [0, 1] rides along with the truth value: the fraction of leaves true, with a
numeric leaf that is false scored by how close it is (`1 - distance / span`). `all` averages its
clauses, `any` takes its best, `not` is 1 or 0 - a negation has no meaningful "nearly".
CR-05 ranks destinations by it; the simulator reports it.
"""
from collections import namedtuple

OPEN = "open"
CLOSED = "closed"
_POLARITIES = (OPEN, CLOSED)

MAX_DEPTH = 3
_COMBINATORS = ("all", "any", "not")

# Result of evaluating one condition. `unknown` lists every referent that could not be
# resolved, as short human strings - the simulator shows them, lint doesn't use them.
Result = namedtuple("Result", "satisfied proximity unknown")

# Keys that start a leaf of their own; anything else is a modifier belonging to `stat` or
# `relationship`. `_NUMERIC_MODS` attach to a stat, `_REL_MODS` to a relationship.
_STANDALONE = ("revealed", "flag", "item_tag", "tier", "tier_reached", "creation",
               "turn_gte", "act_gte", "subplot_status", "waypoints_done", "bond",
               "leverage_kind", "leverage_label_matches")
_NUMERIC_MODS = ("gte", "lte", "between")
_REL_MODS = ("tier_gte", "tier_lte", "peak_gte", "gte", "lte", "between")


# --- normalisation -------------------------------------------------------------------------

def normalize(cond):
    """The canonical spelling of `cond`. Non-dicts and anything already canonical pass through
    unchanged; only the three legacy forms are rewritten. Never raises."""
    if isinstance(cond, list):
        return cond
    if not isinstance(cond, dict):
        return cond
    out = {}
    for key, value in cond.items():
        if key in ("all", "any") and isinstance(value, list):
            out[key] = [normalize(c) for c in value]
        elif key == "not":
            out[key] = normalize(value)
        elif key == "revelation":
            out["revealed"] = value
        elif key == "stat" and isinstance(value, dict):
            out["stat"] = value.get("axis")
            if "at_least" in value:
                out["gte"] = value["at_least"]
            elif "gte" in value:
                out["gte"] = value["gte"]
            if "at_most" in value:
                out["lte"] = value["at_most"]
            elif "lte" in value:
                out["lte"] = value["lte"]
        elif key == "relationship" and isinstance(value, dict):
            out["relationship"] = value.get("character")
            for mod in ("peak_gte", "gte", "lte"):
                if mod in value:
                    out[mod] = value[mod]
        else:
            out[key] = value
    return out


# --- leaf decomposition --------------------------------------------------------------------

def _parts(cond):
    """Split a normalised dict into independent parts that are ANDed: `("group", key, value)`,
    `("stat", dict)`, `("relationship", dict)`, `("leaf", key, value)`, or `("bad", why)`.

    A dict of one leaf is one part; a dict with several keys (`{"stat": "x", "gte": 1}` is one
    leaf, `{"flag": "a", "revealed": "b"}` is two) is their conjunction, which is how the
    old gate treated it too."""
    parts = []
    leftovers = set(cond)
    for key in _COMBINATORS:
        if key in cond:
            parts.append(("group", key, cond[key]))
            leftovers.discard(key)
    if "stat" in cond:
        mods = {m: cond[m] for m in _NUMERIC_MODS if m in cond}
        parts.append(("stat", {"axis": cond["stat"], **mods}))
        leftovers -= {"stat", *_NUMERIC_MODS}
    if "relationship" in cond:
        mods = {m: cond[m] for m in _REL_MODS if m in cond}
        parts.append(("relationship", {"name": cond["relationship"], **mods}))
        leftovers -= {"relationship", *_REL_MODS}
    for key in _STANDALONE:
        if key in leftovers:
            parts.append(("leaf", key, cond[key]))
            leftovers.discard(key)
    for key in sorted(leftovers):
        parts.append(("bad", f"unknown condition key {key!r}" if key not in _NUMERIC_MODS + _REL_MODS
                      else f"{key!r} has no stat or relationship to apply to"))
    return parts


# --- evaluation ----------------------------------------------------------------------------

def evaluate(cond, ctx, polarity, ending=None) -> Result:
    """Evaluate `cond` against `ctx`. `polarity` is `OPEN` or `CLOSED` and is required.
    `ending` (an `endings.entries[]` dict) scopes `waypoints_done`; without it that leaf is
    unknown. An absent or empty condition is satisfied: "no requirement" reads as "met"."""
    if polarity not in _POLARITIES:
        raise ValueError(f"polarity must be one of {_POLARITIES}, not {polarity!r}")
    return _eval(normalize(cond), ctx, polarity, ending, 1)


def satisfied(cond, ctx, polarity, ending=None) -> bool:
    return evaluate(cond, ctx, polarity, ending).satisfied


def _unknown(polarity, what):
    """An unresolvable referent: true under OPEN, false under CLOSED."""
    truth = polarity == OPEN
    return Result(truth, 1.0 if truth else 0.0, [what])


def _join(results, mode):
    """Combine child results: all -> every one, any -> at least one."""
    unknown = [u for r in results for u in r.unknown]
    if mode == "all":
        return Result(all(r.satisfied for r in results),
                      sum(r.proximity for r in results) / len(results), unknown)
    return Result(any(r.satisfied for r in results), max(r.proximity for r in results), unknown)


def _eval(cond, ctx, polarity, ending, depth) -> Result:
    if not cond:
        return Result(True, 1.0, [])
    if not isinstance(cond, dict):
        return _unknown(polarity, "malformed condition")
    results = []
    for part in _parts(cond):
        if part[0] == "group":
            results.append(_eval_group(part[1], part[2], ctx, polarity, ending, depth))
        elif part[0] == "stat":
            results.append(_stat(part[1], ctx, polarity))
        elif part[0] == "relationship":
            results.append(_relationship(part[1], ctx, polarity))
        elif part[0] == "leaf":
            results.append(_leaf(part[1], part[2], ctx, polarity, ending))
        else:
            results.append(_unknown(polarity, part[1]))
    return results[0] if len(results) == 1 else _join(results, "all")


def _eval_group(kind, value, ctx, polarity, ending, depth) -> Result:
    if depth > MAX_DEPTH:
        return _unknown(polarity, f"conditions nest more than {MAX_DEPTH} deep")
    if kind == "not":
        inner = _eval(value, ctx, polarity, ending, depth + 1)
        # Negating an unresolved referent must not flip its polarity: `not <typo>` under OPEN
        # would otherwise read false and lock the door. The unknown stays the answer.
        if inner.unknown:
            return _unknown(polarity, inner.unknown[0])
        return Result(not inner.satisfied, 0.0 if inner.satisfied else 1.0, [])
    if not isinstance(value, list):
        return _unknown(polarity, f"{kind!r} needs a list of conditions")
    if not value:
        # An empty group expressed no requirement. Vacuous truth for `any` would be false -
        # and a deadlock - so both read as met, under either polarity.
        return Result(True, 1.0, [])
    return _join([_eval(c, ctx, polarity, ending, depth + 1) for c in value], kind)


# --- state accessors -----------------------------------------------------------------------

def _state(ctx):
    return ctx.get("state") or {}


def _protagonist(ctx):
    return _state(ctx).get("protagonist") or {}


def _engine(ctx, slot):
    """`(engine, cfg)` for a mechanic slot, or None - no engine bound, or the story authors one
    this build cannot bind (the playable projection drops those, but a raw story can reach here
    from the simulator)."""
    import mechanics
    try:
        bound = mechanics.bound_for(ctx.get("story") or {}, slot)
    except (mechanics.UnknownEngineError, ValueError, KeyError):
        return None
    return (bound.engine, bound.cfg) if bound else None


def _flags_declared(ctx):
    """Declared flag ids, or None when the story authors no `mechanics.flags` block - in which
    case flags are the pre-CR-02 free-form names and any name is a legitimate referent."""
    block = ((ctx.get("story") or {}).get("mechanics") or {}).get("flags")
    if not isinstance(block, dict):
        return None
    return {f.get("id") for f in block.get("declared") or [] if isinstance(f, dict)}


def _known_flags(ctx):
    """`active ∪ archive`. See gate.py's module docstring: `archive_stale_flags` retires a flag
    out of `active` on a 10-turn window while `act_check_frequency` defaults to 12, so reading
    `active` alone is false at exactly the moment a completion condition is consulted."""
    flags = _protagonist(ctx).get("flags") or {}
    return set(flags.get("active") or {}) | set(flags.get("archive") or {})


def _revelation_ids(ctx):
    found = _engine(ctx, "revelations")
    if not found:
        return set()
    engine, cfg = found
    return {e.get("id") for e in engine.entries(cfg)}


def _inventory(ctx):
    return [{"label": e, "tags": []} if isinstance(e, str) else e
            for e in _protagonist(ctx).get("inventory") or []]


# --- leaves --------------------------------------------------------------------------------

def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _numeric(value, mods, span):
    """Truth and proximity of a number against `gte` / `lte` / `between`. `span` scales the
    proximity of a miss."""
    checks = []  # (satisfied, distance)
    if _num(mods.get("gte")):
        checks.append((value >= mods["gte"], mods["gte"] - value))
    if _num(mods.get("lte")):
        checks.append((value <= mods["lte"], value - mods["lte"]))
    between = mods.get("between")
    if isinstance(between, list) and len(between) == 2 and all(_num(b) for b in between):
        lo, hi = between
        checks.append((lo <= value <= hi, max(lo - value, value - hi)))
    if not checks:
        return None
    ok = all(s for s, _ in checks)
    worst = max((d for s, d in checks if not s), default=0)
    return ok, 1.0 if ok else max(0.0, 1.0 - worst / span)


def _stat(spec, ctx, polarity):
    axis = spec["axis"]
    stats = _protagonist(ctx).get("stats") or {}
    if not isinstance(axis, str) or axis not in stats:
        return _unknown(polarity, f"stat {axis!r}")
    span = 100.0
    found = _engine(ctx, "stats")
    if found:
        floor, ceiling = found[0].bounds(found[1], axis)
        if _num(floor) and _num(ceiling) and ceiling > floor:
            span = float(ceiling - floor)
    out = _numeric(stats[axis], spec, span)
    if out is None:
        return _unknown(polarity, f"stat {axis!r} has no comparator")
    return Result(out[0], out[1], [])


def _tier_by_label(cfg_tiers, label):
    return next((t for t in cfg_tiers if t.get("label") == label), None)


def _tier_leaf(kind, value, ctx, polarity):
    if not (isinstance(value, list) and len(value) == 2 and all(isinstance(v, str) for v in value)):
        return _unknown(polarity, f"{kind} needs [axis, label]")
    axis, label = value
    stats = _protagonist(ctx).get("stats") or {}
    found = _engine(ctx, "stats")
    if axis not in stats or not found:
        return _unknown(polarity, f"stat {axis!r}")
    engine, cfg = found
    tiers = engine.tiers(cfg, axis)
    target = _tier_by_label(tiers, label)
    if target is None:
        return _unknown(polarity, f"tier {label!r} of {axis!r}")
    current = engine.tier_for(cfg, axis, stats[axis])
    if kind == "tier":
        ok = bool(current) and current.get("label") == label
    else:
        log = (((_state(ctx).get("mechanics") or {}).get("stats") or {}).get("tier_log") or {})
        ok = label in (log.get(axis) or []) or stats[axis] >= target["at"]
    return Result(ok, 1.0 if ok else 0.0, [])


def _relationship(spec, ctx, polarity):
    name = spec["name"]
    state = _state(ctx)
    world_chars = ((ctx.get("story") or {}).get("world") or {}).get("characters") or {}
    met = (state.get("characters") or {})
    if not isinstance(name, str) or (name not in met and name not in world_chars):
        return _unknown(polarity, f"character {name!r}")
    entry = met.get(name) or {}
    score = entry.get("relationship")
    if not _num(score):
        # A character who exists but has no score yet: not unknown, just unmet - the player
        # has not had a scored interaction with them.
        return Result(False, 0.0, [])
    found = _engine(ctx, "relationships")
    tiers = found[0].tiers(found[1]) if found else []
    parts = []
    for mod in ("tier_gte", "tier_lte"):
        if mod in spec:
            tier = _tier_by_label(tiers, spec[mod])
            if tier is None:
                return _unknown(polarity, f"relationship tier {spec[mod]!r}")
            parts.append(_numeric(score, {"gte" if mod == "tier_gte" else "lte": tier["at"]}, 200.0))
    if "peak_gte" in spec:
        peak = entry.get("peak")
        best = max(score, peak) if _num(peak) else score
        parts.append(_numeric(best, {"gte": spec["peak_gte"]}, 200.0))
    plain = {m: spec[m] for m in _NUMERIC_MODS if m in spec}
    if plain:
        parts.append(_numeric(score, plain, 200.0))
    parts = [p for p in parts if p is not None]
    if not parts:
        return _unknown(polarity, f"relationship {name!r} has no comparator")
    ok = all(p[0] for p in parts)
    return Result(ok, sum(p[1] for p in parts) / len(parts), [])


def _subplot_state(ctx, sid):
    return ((_state(ctx).get("plot") or {}).get("subplots") or {}).get(sid)


def _subplot_matches(status, record):
    """`progressed` is CR-10's status for a thread that has moved at all: any progress, or done."""
    if status == "progressed":
        return record.get("status") == "completed" or (record.get("progress") or 0) > 0
    return record.get("status") == status


def _leaf(kind, value, ctx, polarity, ending) -> Result:
    if kind in ("tier", "tier_reached"):
        return _tier_leaf(kind, value, ctx, polarity)
    if kind == "revealed":
        if value in (_state(ctx).get("plot") or {}).get("revelations_revealed", {}):
            return Result(True, 1.0, [])
        if value not in _revelation_ids(ctx):
            return _unknown(polarity, f"revelation {value!r}")
        return Result(False, 0.0, [])
    if kind == "flag":
        declared = _flags_declared(ctx)
        if declared is not None and value not in declared:
            return _unknown(polarity, f"flag {value!r}")
        ok = value in _known_flags(ctx)
        return Result(ok, 1.0 if ok else 0.0, [])
    if kind == "item_tag":
        ok = any(value in (r.get("tags") or []) for r in _inventory(ctx))
        return Result(ok, 1.0 if ok else 0.0, [])
    if kind == "turn_gte":
        turn = (_state(ctx).get("pacing") or {}).get("turn_count", 0)
        ok = _num(value) and turn >= value
        return Result(bool(ok), 1.0 if ok else (max(0.0, turn / value) if _num(value) and value else 0.0), [])
    if kind == "act_gte":
        act = (_state(ctx).get("plot") or {}).get("current_act", 1)
        ok = _num(value) and act >= value
        return Result(bool(ok), 1.0 if ok else 0.0, [])
    if kind == "creation":
        return _creation(value, ctx, polarity)
    if kind in ("leverage_kind", "leverage_label_matches"):
        return _leverage(kind, value, ctx)
    if kind == "subplot_status":
        return _subplot_status(value, ctx, polarity)
    if kind == "waypoints_done":
        return _waypoints_done(value, ctx, polarity, ending)
    return _unknown(polarity, f"{kind} (no engine reads this yet)")


def _creation(value, ctx, polarity):
    if not isinstance(value, dict) or not value:
        return _unknown(polarity, "creation needs {step: option}")
    steps = {s.get("key") for s in (ctx.get("story") or {}).get("character_creation") or []}
    chosen = _protagonist(ctx).get("creation_choices") or {}
    results = []
    for step, option in value.items():
        if step not in steps:
            return _unknown(polarity, f"creation step {step!r}")
        results.append(chosen.get(step) == option)
    ok = all(results)
    return Result(ok, sum(results) / len(results), [])


def _leverage(kind, value, ctx):
    import re
    entries = _protagonist(ctx).get("leverage") or []
    if kind == "leverage_kind":
        ok = any(e.get("kind") == value for e in entries if isinstance(e, dict))
    else:
        try:
            pattern = re.compile(value, re.IGNORECASE)
        except (re.error, TypeError):
            return Result(False, 0.0, [])
        ok = any(pattern.search(str(e.get("label", ""))) for e in entries if isinstance(e, dict))
    return Result(ok, 1.0 if ok else 0.0, [])


def _subplot_status(value, ctx, polarity):
    if not isinstance(value, dict) or not value:
        return _unknown(polarity, "subplot_status needs {subplot: status}")
    results = []
    for sid, status in value.items():
        record = _subplot_state(ctx, sid)
        if record is None:
            return _unknown(polarity, f"subplot {sid!r}")
        results.append(_subplot_matches(status, record))
    ok = all(results)
    return Result(ok, sum(results) / len(results), [])


def _waypoints_done(value, ctx, polarity, ending):
    if not isinstance(ending, dict):
        return _unknown(polarity, "waypoints_done outside an ending")
    ids = [w.get("id") for w in ending.get("waypoints") or [] if isinstance(w, dict)]
    # The ending_funnel engine's ledger, keyed "<ending id>.<waypoint id>" (a waypoint id is
    # only unique within its ending), under the engine's own state bucket.
    ledger = ((_state(ctx).get("mechanics") or {}).get("endings") or {}).get("waypoints_done") or {}
    done = {k.split(".", 1)[1] for k in ledger if k.startswith(f"{ending.get('id')}.")}
    have = [i for i in ids if i in done]
    if value == "all":
        need = len(ids)
    elif isinstance(value, int) and not isinstance(value, bool):
        need = value
    elif isinstance(value, list):
        if any(i not in ids for i in value):
            return _unknown(polarity, "waypoints_done names a waypoint this ending lacks")
        have = [i for i in value if i in done]
        need = len(value)
    else:
        return _unknown(polarity, "waypoints_done needs 'all', a count, or a list of ids")
    ok = len(have) >= need
    return Result(ok, 1.0 if ok or not need else len(have) / need, [])


# --- plain-English rendering ---------------------------------------------------------------

FRAGMENT_LABEL_MAX = 48


def revelation_labels(story) -> dict:
    """`{fragment id: short human label}` for every `mechanics.revelations` entry, so a
    `revealed` leaf reads as what the fragment *is* rather than a bare `frag_0006`. The label is
    the author-only `_title` when there is one, else the start of the trigger (the event that
    reveals it). Never the `content`: that is what the narrator is given once the fragment is
    revealed, and an edge label is not the place to read it early."""
    block = ((story or {}).get("mechanics") or {}).get("revelations")
    entries = block.get("entries") if isinstance(block, dict) else block
    out = {}
    for e in entries or []:
        if not isinstance(e, dict) or not e.get("id"):
            continue
        text = (e.get("_title") or "").strip() or (e.get("trigger") or "").strip()
        if len(text) > FRAGMENT_LABEL_MAX:
            text = text[:FRAGMENT_LABEL_MAX - 1].rstrip() + "\u2026"
        out[e["id"]] = text or e["id"]
    return out


def describe(cond, top=True, names=None) -> str:
    """A short plain-English reading of `cond`, for edge labels and the evaluate panel. Lossy by
    design (never parsed back), total (never raises), and the same text wherever it appears -
    the board's own JS labeller only ever covers an edit the server has not seen yet.

    `names` is optional display data, `{"revealed": revelation_labels(story)}`: with it, a
    fragment leaf reads by its title rather than its id."""
    cond = normalize(cond)
    if not cond:
        return "always"
    if not isinstance(cond, dict):
        return "condition"
    bits = []
    for part in _parts(cond):
        tag = part[0]
        if tag == "group":
            _, kind, value = part
            if kind == "not":
                bits.append("not " + describe(value, top=False, names=names))
            else:
                joined = (" and " if kind == "all" else " or ").join(
                    describe(c, top=False, names=names) for c in value if c) if isinstance(value, list) else ""
                bits.append(joined if top and len(cond) == 1 else f"({joined})")
        elif tag == "stat":
            bits.append(_describe_numeric(str(part[1]["axis"]).upper(), part[1]))
        elif tag == "relationship":
            bits.append(_describe_relationship(part[1]))
        elif tag == "leaf":
            bits.append(_describe_leaf(part[1], part[2], names))
        else:
            bits.append("condition")
    return " and ".join(b for b in bits if b) or "condition"


def _describe_numeric(subject, spec):
    out = []
    if "gte" in spec:
        out.append(f"{subject} >= {spec['gte']}")
    if "lte" in spec:
        out.append(f"{subject} <= {spec['lte']}")
    if isinstance(spec.get("between"), list) and len(spec["between"]) == 2:
        out.append(f"{subject} {spec['between'][0]}-{spec['between'][1]}")
    return ", ".join(out) or subject


def _describe_relationship(spec):
    name = spec["name"]
    out = []
    if "tier_gte" in spec:
        out.append(f"{name} at least {spec['tier_gte']}")
    if "tier_lte" in spec:
        out.append(f"{name} at most {spec['tier_lte']}")
    if "peak_gte" in spec:
        out.append(f"{name} peak >= {spec['peak_gte']}")
    plain = _describe_numeric(f"{name} score", {m: spec[m] for m in _NUMERIC_MODS if m in spec})
    if any(m in spec for m in _NUMERIC_MODS):
        out.append(plain)
    return ", ".join(out) or str(name)


def _describe_leaf(kind, value, names=None):
    if kind == "revealed" and ((names or {}).get("revealed") or {}).get(value):
        return f"revealed: \u201c{names['revealed'][value]}\u201d"
    if kind in ("flag", "revealed", "item_tag"):
        return f"{kind}: {value}"
    if kind == "tier" and isinstance(value, list) and len(value) == 2:
        return f"{str(value[0]).upper()} is {value[1]}"
    if kind == "tier_reached" and isinstance(value, list) and len(value) == 2:
        return f"{str(value[0]).upper()} reached {value[1]}"
    if kind == "subplot_status" and isinstance(value, dict):
        return "thread " + ", ".join(f"{k} {v}" for k, v in value.items())
    if kind in ("turn_gte", "act_gte"):
        return f"{kind.split('_')[0]} >= {value}"
    if kind == "creation" and isinstance(value, dict):
        return ", ".join(f"{k} = {v}" for k, v in value.items())
    if kind == "waypoints_done":
        if value == "all":
            return "all waypoints done"
        if isinstance(value, list):
            return "waypoints " + ", ".join(map(str, value)) + " done"
        return f"{value} waypoints done"
    if kind == "leverage_kind":
        return f"leverage of kind {value}"
    if kind == "leverage_label_matches":
        return f"leverage matching {value!r}"
    return kind


# --- authoring-side checks -----------------------------------------------------------------

def check(cond, story) -> list:
    """Problems with `cond` against the *template* `story`, no save state involved: malformed
    shapes, over-deep nesting, and every referent the story does not define (L10). Returns short
    strings, `[]` when clean. Static counterpart of `evaluate()`'s unknown-referent path, so the
    two cannot disagree about what counts as unknown."""
    problems = []
    _check(normalize(cond), story or {}, 1, problems)
    return problems


def _check(cond, story, depth, problems):
    if not cond:
        return
    if not isinstance(cond, dict):
        problems.append("is not a condition object")
        return
    mech = story.get("mechanics") or {}
    for part in _parts(cond):
        tag = part[0]
        if tag == "group":
            _, kind, value = part
            if depth > MAX_DEPTH:
                problems.append(f"nests groups more than {MAX_DEPTH} deep")
            elif kind == "not":
                _check(value, story, depth + 1, problems)
            elif not isinstance(value, list):
                problems.append(f"{kind!r} needs a list of conditions")
            else:
                for c in value:
                    _check(c, story, depth + 1, problems)
        elif tag == "stat":
            axes = _stat_axes(story)
            axis = part[1]["axis"]
            if axis not in axes:
                problems.append(f"names unknown stat {axis!r}")
            if not any(m in part[1] for m in _NUMERIC_MODS):
                problems.append(f"stat {axis!r} needs gte, lte or between")
        elif tag == "relationship":
            spec = part[1]
            if not _character_known(story, spec["name"]):
                problems.append(f"names unknown character {spec['name']!r}")
            if not any(m in spec for m in _REL_MODS):
                problems.append(f"relationship {spec['name']!r} needs a comparator")
        elif tag == "leaf":
            _check_leaf(part[1], part[2], story, mech, problems)
        else:
            problems.append(part[1])


def _stat_axes(story):
    mech = story.get("mechanics") or {}
    axes = set(((mech.get("stats") or {}).get("axes") or {}))
    axes |= set((story.get("protagonist") or {}).get("stats") or {})
    for step in story.get("character_creation") or []:
        for opt in step.get("options") or []:
            axes |= set(opt.get("starting_stats") or {})
    return axes


def _character_known(story, name):
    return isinstance(name, str) and name in ((story.get("world") or {}).get("characters") or {})


def _check_leaf(kind, value, story, mech, problems):
    if kind == "revealed":
        entries = (mech.get("revelations") or {}).get("entries") if isinstance(mech.get("revelations"), dict) else []
        if value not in {e.get("id") for e in entries or [] if isinstance(e, dict)}:
            problems.append(f"names unknown revelation {value!r}")
    elif kind == "flag":
        block = mech.get("flags")
        declared = {f.get("id") for f in (block or {}).get("declared") or [] if isinstance(f, dict)} \
            if isinstance(block, dict) else set()
        if value not in declared:
            problems.append(f"names flag {value!r}, which mechanics.flags.declared does not declare")
    elif kind in ("tier", "tier_reached"):
        if not (isinstance(value, list) and len(value) == 2):
            problems.append(f"{kind} needs [axis, label]")
            return
        axis, label = value
        tiers = ((mech.get("stats") or {}).get("axes") or {}).get(axis, {}) or {}
        if axis not in _stat_axes(story):
            problems.append(f"names unknown stat {axis!r}")
        elif label not in {t.get("label") for t in tiers.get("tiers") or []}:
            problems.append(f"names unknown tier {label!r} of {axis!r}")
    elif kind == "creation":
        keys = {s.get("key"): {o.get("id") for o in s.get("options") or []}
                for s in story.get("character_creation") or []}
        for step, option in (value or {}).items() if isinstance(value, dict) else []:
            if step not in keys:
                problems.append(f"names unknown creation step {step!r}")
            elif option not in keys[step]:
                problems.append(f"names unknown option {option!r} of creation step {step!r}")
    elif kind == "subplot_status":
        subplots = ((story.get("plot") or {}).get("subplots") or {})
        for sid in (value or {}) if isinstance(value, dict) else []:
            if sid not in subplots:
                problems.append(f"names unknown subplot {sid!r}")
    elif kind == "bond":
        problems.append("uses `bond`, which no engine reads yet (CR-11)")
    # item_tag / turn_gte / act_gte / leverage_* / waypoints_done carry no template referent.


# --- where conditions live in a template ---------------------------------------------------

def iter_conditions(story):
    """Yield `(path, condition, polarity, ending)` for every condition field an author can
    write. This is the single list of call sites and their polarity (D2: "every condition call
    site declares its polarity explicitly"); lint and the simulator both walk it, so a new
    condition field is added here once. `ending` is the entry for fields scoped to one."""
    plot = story.get("plot") or {}
    for i, act in enumerate((plot.get("main_thread") or {}).get("acts") or []):
        if act.get("requires"):
            yield f"plot.main_thread.acts[{i}].requires", act["requires"], OPEN, None
    for sid, sp in (plot.get("subplots") or {}).items():
        if sp.get("activate_when"):
            yield f"plot.subplots.{sid}.activate_when", sp["activate_when"], OPEN, None
        if sp.get("fail_when"):
            yield f"plot.subplots.{sid}.fail_when", sp["fail_when"], CLOSED, None
    mech = story.get("mechanics") or {}
    for i, gate in enumerate((mech.get("gate") or {}).get("gates") or []):
        if gate.get("requires"):
            yield f"mechanics.gate.gates[{i}].requires", gate["requires"], OPEN, None
    # CR-06 lore. Both CLOSED: a lore entry is narrator knowledge, and `unlock` exists to keep
    # *staged* knowledge dormant until earned - an unknown referent reading true would inject it
    # early, which is a leak. Failing closed costs one missing line of lore, never a stuck save.
    lore = mech.get("lore") if isinstance(mech.get("lore"), dict) else {}
    for entry in lore.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        for field in ("also_when", "unlock"):
            if entry.get(field):
                yield f"mechanics.lore.entries[{entry.get('id', '?')}].{field}", entry[field], CLOSED, None
    endings = mech.get("endings") if isinstance(mech.get("endings"), dict) else {}
    for entry in endings.get("entries") or []:
        eid = entry.get("id", "?")
        for field, pol in (("viable_while", OPEN), ("ready_when", CLOSED), ("fail_when", CLOSED)):
            if entry.get(field):
                yield f"mechanics.endings.entries[{eid}].{field}", entry[field], pol, entry
        for wp in entry.get("waypoints") or []:
            if wp.get("done_when"):
                yield (f"mechanics.endings.entries[{eid}].waypoints[{wp.get('id', '?')}].done_when",
                       wp["done_when"], CLOSED, entry)

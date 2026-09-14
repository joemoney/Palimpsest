"""`gate` / `precondition` - engine v2 phase 6.

docs/ENGINE_V2_SPEC.md §7.4 and §2.2, docs/analysis_and_plans/ENGINE_V2/ENGINE_V2_PHASES.md
phase 6. Owns one thing: deciding whether a predicate over engine state is satisfied. Two
callers point it at two different targets - a gated action (§7.4) and an authored act's
`requires` (§2.2) - and it is the same evaluator either way.

**The evaluator is a module function, not a method.** `satisfied()` is reachable without any
bound engine, because §2.2's act `requires` is authored on the *act*, not in `mechanics`: a
story can author act preconditions and no `gate` block at all, and declare-to-bind would
leave it with no engine to ask. The `Precondition` class is only the part that gates
*actions*; the predicate language belongs to neither caller.

**Observes nothing, resolves nothing.** §7.4: pure adjudication. This engine contributes no
observation field and emits no effects - it answers questions, and the turn pipeline decides
what to do with the answer.

**Unknown referents degrade, they never block.** A predicate naming a revelation id or flag
that does not exist reads as *satisfied*, and the clause is dropped. That is the CR-04
`dangling connected_to id skipped silently` precedent, and §2.2 names it as the rule for this
feature specifically: the failure being avoided is a save whose main thread can never advance,
which is strictly worse than an act that advances one beat early. Phase 6's gate says it in so
many words - "no reachable deadlock".

**Flag predicates read `flags.active ∪ flags.archive`, and this is not a preference.**
`archive_stale_flags` retires an unpinned flag out of `active` once its setting turn leaves
`RECENT_TURN_LIMIT` (10), while `act_check_frequency` defaults to 12 - so a predicate reading
`active` alone is consulted on a cadence *longer than the flag's own lifetime there* and is
reliably false at exactly the moment it matters. `archive` is written at two sites and popped
at none, which makes the union monotonic and gives the "did this ever happen" semantics a
completion condition actually wants.

**Four leaf kinds, and the omission is deliberate.** `revelation` and `flag` are what §2.2's
table marks usable for act completion - enumerable when written, and latching. `item_tag` and
`stat` exist for *gates*, which have no latching requirement: a door may re-lock when the key
is lost, and that is correct behaviour rather than a bug. `tier` is **not** implemented even
though §7.4 lists it: it needs a relationship lookup and a tier resolution, it is non-latching
in a way §2.2 flags as needing a high-water mark, and the risk this phase names by name is
this evaluator growing into a general expression language. It is one line to add when a story
wants it.
"""
from . import MechanicEngine, register

# Combinators. `not` takes a single predicate, the other two take lists.
_ALL, _ANY, _NOT = "all", "any", "not"


def satisfied(predicate, ctx) -> bool:
    """Whether `predicate` holds against current state. An empty or absent predicate is
    satisfied - "no requirement" has to read as "met", or an absent `requires` would block
    every act (P-4: the minimal template still runs)."""
    if not predicate:
        return True
    if not isinstance(predicate, dict):
        return True  # not a predicate at all; degrade rather than block

    if _ALL in predicate:
        return all(satisfied(clause, ctx) for clause in predicate[_ALL] or [])
    if _ANY in predicate:
        clauses = predicate[_ANY] or []
        # An empty `any` is vacuously *unsatisfiable* in logic, which here would be a
        # deadlock. Degrade: an author who wrote no alternatives expressed no requirement.
        return any(satisfied(clause, ctx) for clause in clauses) if clauses else True
    if _NOT in predicate:
        return not satisfied(predicate[_NOT], ctx)

    return all(_leaf(kind, value, ctx) for kind, value in predicate.items())


def _leaf(kind, value, ctx) -> bool:
    if kind == "revelation":
        if value in (ctx["state"]["plot"].get("revelations_revealed") or {}):
            return True
        # Unrevealed and unknown are different answers. A fragment the story authors but the
        # player has not reached is genuinely unmet - that is the predicate doing its job. An
        # id no longer in the template (renamed, dropped, or never written) is unreachable,
        # and treating it as unmet is precisely the save whose main thread can never advance.
        return not _revelation_exists(value, ctx)
    if kind == "flag":
        return value in _known_flags(ctx)
    if kind == "item_tag":
        return any(value in (record.get("tags") or []) for record in _inventory(ctx))
    if kind == "stat":
        return _stat_threshold(value, ctx)
    # An unimplemented kind is an authoring error, but blocking forever is the one outcome
    # this feature must never produce - so it degrades like an unknown referent.
    print(f"WARNING: gate predicate names unknown kind {kind!r}; treating it as satisfied")
    return True


def _revelation_exists(rev_id, ctx) -> bool:
    """Whether the story still authors this revelation. Read through the registry rather than
    off the template: `mechanics.revelations` is `{engine, entries}` in v3, and four call
    sites once assumed the v2 bare list and crashed on real saves when it stopped being one.

    No revelations engine bound at all means no id exists, so every revelation predicate
    degrades - which is the right answer for a story that authors act preconditions against
    fragments it later removed."""
    from . import bound_for
    bound = bound_for(ctx["story"], "revelations")
    if bound is None:
        return False
    return any(entry.get("id") == rev_id for entry in bound.engine.entries(bound.cfg))


def _known_flags(ctx) -> set:
    """`active ∪ archive` - see the module docstring on why the union is the whole point."""
    flags = ctx["state"]["protagonist"].get("flags") or {}
    return set(flags.get("active") or {}) | set(flags.get("archive") or {})


def _inventory(ctx) -> list:
    """Item records, tolerating a pre-engine save's bare strings (which carry no tags and so
    satisfy no `item_tag` predicate) without reaching into the items engine's internals."""
    return [
        {"label": entry, "tags": []} if isinstance(entry, str) else entry
        for entry in (ctx["state"]["protagonist"].get("inventory") or [])
    ]


def _stat_threshold(value, ctx) -> bool:
    """`{"stat": {"axis": "sync", "at_least": 40}}`, or `at_most` for a ceiling. An axis the
    save does not carry degrades to satisfied: stats are seeded at save creation and an axis
    that is not there is an authoring error, not a condition the player can ever meet."""
    if not isinstance(value, dict):
        return True
    axis = value.get("axis")
    stats = ctx["state"]["protagonist"].get("stats") or {}
    if axis not in stats:
        return True
    current = stats[axis]
    if "at_least" in value and current < value["at_least"]:
        return False
    if "at_most" in value and current > value["at_most"]:
        return False
    return True


class Precondition(MechanicEngine):
    slot = "gate"
    name = "precondition"
    # Adjudicates rather than resolves, so its order never matters; kept low so that a future
    # engine wanting to read a gate verdict finds it already decided.
    resolve_order = 10
    # §5.4. Contributes no narration section: what the narrator is told about a locked door
    # is the refusal path's job, and that is not assembled here.
    prompt_budget = 0

    def gates(self, cfg):
        """The authored gates. Required for the same reason `triggered_reveal` requires
        `entries`: a declared engine with nothing to adjudicate can never fire, and silent
        inertness is what this architecture exists to remove."""
        gates = cfg.get("gates")
        if not gates:
            raise ValueError(
                "mechanics.gate declares engine 'precondition' but authors no 'gates'."
            )
        return gates

    def unmet(self, cfg, ctx) -> list:
        """Every gate whose predicate is *not* satisfied right now - i.e. the only gates that
        could refuse anything this turn.

        This is the engine half of §7.4's split, and the reason the detector never sees a
        satisfied gate: the engine decides *whether* a refusal is possible, and only then is
        a model asked whether this particular action reaches for one."""
        return [g for g in self.gates(cfg) if not satisfied(g.get("requires"), ctx)]


ENGINE = register(Precondition())

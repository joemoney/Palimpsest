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

**The evaluator lives in `backend/conditions.py` now (decision D2).** `satisfied()` below is a
thin caller with `OPEN` polarity - the unknown-referent rule above - so that the ending funnel
can evaluate the same grammar with unknowns reading the other way. The leaf list, the
latching table and the warning about this growing into a general expression language all moved
with it; `LATCHING` and `non_latching_referents` stay here because §2.2's act-`requires` smell
check is a gate concern, not a grammar one.
"""
from . import MechanicEngine, register

# §2.2's table: the referent kinds that are both enumerable when the predicate is written and
# *latching* - once true, true forever. Only these belong in an act's `requires`. `item_tag`
# and `stat` are deliberately absent: both are legitimate on a door, where re-locking when the
# key is spent is correct behaviour, and a trap on an act, where it means advancing and then
# un-advancing. `revelation` is CR-02's legacy spelling of `revealed`.
LATCHING = frozenset({"revealed", "revelation", "flag"})

_COMPARATORS = frozenset({"gte", "lte", "between", "tier_gte", "tier_lte", "peak_gte"})


def satisfied(predicate, ctx) -> bool:
    """Whether `predicate` holds against current state, unknown referents reading *true*.

    A thin caller on `conditions.evaluate` with `OPEN` polarity (decision D2): the evaluator
    moved to `backend/conditions.py` so that the ending funnel can share it and read the same
    unknowns the other way. An empty or absent predicate is satisfied - "no requirement" has to
    read as "met", or an absent `requires` would block every act (P-4)."""
    import conditions
    return conditions.satisfied(predicate, ctx, conditions.OPEN)


def non_latching_referents(predicate) -> list:
    """Referent kinds in `predicate` that §2.2 rules out for act completion, sorted.

    Returns [] for a predicate that is entirely latching, for a combinator whose clauses all
    are, and for anything that is not a predicate at all - this reports an authoring smell and
    must never itself be the thing that raises."""
    import conditions
    predicate = conditions.normalize(predicate)
    if not isinstance(predicate, dict):
        return []
    found = set()
    for key, value in predicate.items():
        if key in ("all", "any"):
            for clause in value if isinstance(value, list) else []:
                found.update(non_latching_referents(clause))
        elif key == "not":
            found.update(non_latching_referents(value))
        elif key not in LATCHING and key not in _COMPARATORS:
            found.add(key)
    return sorted(found)


class Precondition(MechanicEngine):
    slot = "gate"
    name = "precondition"
    # Adjudicates rather than resolves, so its order never matters; kept low so that a future
    # engine wanting to read a gate verdict finds it already decided.
    resolve_order = 10
    # §5.4. One line per currently-shut gate plus a header; a story with a dozen gates open
    # at once is authoring a maze rather than a world, and should be told so.
    prompt_budget = 900

    def resolve(self, cfg, ctx, observations, events):
        """Nothing. §7.4: this engine adjudicates, it does not resolve - it owns no state and
        emits no effects, and its verdicts are consumed by the turn pipeline and the narration
        prompt rather than applied.

        Spelled out rather than inherited because the base `resolve` raises: an engine that
        forgets to implement it should fail loudly, and one that genuinely has nothing to do
        should say so where a reader can see it."""
        return []

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

    def blocking(self, cfg, ctx, location_id):
        """The gate refusing entry to `location_id` right now, or None.

        This is the hard rail, and it is the reason the detector is allowed to be imperfect.
        The pre-action check is a model judging prose, measured at ~90% recall over repeated
        runs (docs/analysis_and_plans/ENGINE_V2/GATE_DETECTION_MEASUREMENT.md) - fine for
        deciding whether to raise a modal, not fine as the only thing standing between a player
        and a locked room. `scene_update.location` is a closed set
        the model picks from, so vetoing it needs no judgement at all and is right every time.
        Same split as `mechanics.stats.readout` (P-7): the prompt makes the model usually
        comply, the engine makes it always true."""
        if not location_id:
            return None
        for g in self.gates(cfg):
            if g.get("target") == location_id and not satisfied(g.get("requires"), ctx):
                return g
        return None

    @staticmethod
    def target_name(gate, ctx) -> str:
        """What to call this gate's target when a model is going to read it.

        `target` is a location id, because that is what `blocking()` matches
        `scene_update.location` against - but an id is a system identifier that happens to look
        like a noun phrase, and 45aa6b0 already had to stop the model writing one into
        player-facing prose. Resolving it through `world.locations` costs nothing, needs no
        second authored field, and is also what the detector needs: a gate is recognised most
        reliably when its name reads the way the fiction refers to the place
        (docs/analysis_and_plans/ENGINE_V2/GATE_DETECTION_MEASUREMENT.md).

        Falls back to the raw target, so a gate on something that is not a location - a topic,
        a person - still renders as whatever the author wrote."""
        target = gate.get("target", "")
        location = (ctx["story"].get("world", {}).get("locations", {}) or {}).get(target)
        if isinstance(location, dict) and location.get("name"):
            return location["name"]
        return target or "somewhere"

    def prompt_sections(self, cfg, ctx) -> dict:
        """Tell the narrator what is shut, so the prose agrees with the rail.

        Without this the veto still holds but reads as a bug: the narration walks the player
        into the vault and the state quietly leaves them outside. P-2 governs the header -
        a story whose gates are all currently satisfied contributes nothing at all."""
        unmet = self.unmet(cfg, ctx)
        if not unmet:
            return {}
        lines = "\n".join(
            f"- {self.target_name(g, ctx)}: {g.get('refusal_hint', 'it does not open')}"
            for g in unmet
        )
        return {"closed": (
            "\nCLOSED TO THE PROTAGONIST RIGHT NOW (they cannot get in this turn, however "
            "they try - write the attempt and the refusal, never the entry):\n" + lines
        )}

    def unmet(self, cfg, ctx) -> list:
        """Every gate whose predicate is *not* satisfied right now - i.e. the only gates that
        could refuse anything this turn.

        This is the engine half of §7.4's split, and the reason the detector never sees a
        satisfied gate: the engine decides *whether* a refusal is possible, and only then is
        a model asked whether this particular action reaches for one."""
        return [g for g in self.gates(cfg) if not satisfied(g.get("requires"), ctx)]


ENGINE = register(Precondition())

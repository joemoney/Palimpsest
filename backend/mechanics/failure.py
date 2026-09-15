"""`failure_conditions` / `triggered_ending` - engine v2 phase 4, port 4.

docs/ENGINE_V2_SPEC.md §7.6, docs/analysis_and_plans/ENGINE_V2/ENGINE_V2_PHASES.md phase 4 step 4. Owns which conditions
are currently askable and which one fired; owns nothing about what an ending *is*.

**The effect is deliberately unchanged, and the handler deliberately lives elsewhere.** §7.6
is explicit that a failure firing routes into the existing endgame machinery rather than a
new code path: set `endgame.requested`, build `final_arc` from the condition's authored
`ending_prompt`, append a finale act. That machinery is `story_engine._begin_endgame`, shared
with the player's own "end the story" request, and duplicating it here to keep the engine
self-contained would be trading a real invariant (one ending path) for a cosmetic one (one
file). So this module emits `Effect("failure.trigger", ...)` and `story_engine` registers
what applies it. The engine decides *that* the story ends; it does not own *how*.

That is the same shape as §7.4's refusal rule, one layer down: the engine decides, the
template supplies the words (`title`, `ending_prompt`), and neither of them is this module's.

**Cadence (§7.6): nothing to ask once the story is already ending.** v2 did this too, by
emptying the list at the call site; the difference is that it is now the engine's own
decision, made from state, which is what §5.2 asks for. It is also the only cadence this
engine can currently justify - §7.6 wants it to ask "only about conditions that are currently
reachable", and reachability is a predicate over engine state, which is `gate`'s evaluator
and therefore phase 6. Same split as `triggered_reveal`: ported now, triggers upgraded later.

**One condition per turn, and the first one wins.** The observation field is a single id, not
a list: two endings firing at once is not a thing a story can mean, and `_begin_endgame`
already no-ops if the endgame is under way, so the second would be silently discarded
anyway. Asking for one makes that explicit rather than incidental.
"""
from . import Effect, MechanicEngine, ObservationField, register


class TriggeredEnding(MechanicEngine):
    slot = "failure_conditions"
    name = "triggered_ending"
    # Last of the ported engines, and not by accident: v2's apply block put failure
    # conditions after everything else so a failing turn's items, standing and progress all
    # land before the ending machinery takes over. §6.2 exists to make that ordering
    # declared data instead of the physical order of statements, and this is the number that
    # declares it.
    resolve_order = 90
    # §5.4. This engine contributes no narration section at all - an ending is entered
    # through the endgame machinery, which writes its own act, so there is nothing to say in
    # the prompt while the condition is merely armed.
    prompt_budget = 0

    def conditions(self, cfg):
        """The authored endings. Required, same as every other engine's core config: a
        declared engine with nothing to fire is a mechanic that can never fire."""
        conditions = cfg.get("conditions")
        if not conditions:
            raise ValueError(
                "mechanics.failure_conditions declares engine 'triggered_ending' but "
                "authors no 'conditions'. The v2 shape was a bare list; v3 wraps it so the "
                "block has somewhere to carry `engine` (§8.1)."
            )
        return conditions

    @staticmethod
    def _ending(ctx):
        return bool(ctx["state"]["plot"]["endgame"]["requested"])

    def live(self, cfg, ctx):
        return [] if self._ending(ctx) else list(self.conditions(cfg))

    # --- observation ---------------------------------------------------------------

    def observations(self, cfg, ctx):
        live = self.live(cfg, ctx)
        if not live:
            return None
        schema = ('  "failure_triggered": "<the exact id of a FAILURE CONDITION below that '
                  'has now been met this turn, or null if none have>"')
        triggers = {c["id"]: c["trigger"] for c in live}
        context = "\nFAILURE CONDITIONS (id: trigger): " + "; ".join(
            f"{cid}: {trigger}" for cid, trigger in triggers.items())
        return [ObservationField("failure_triggered", schema, context)]

    def events(self, cfg, ctx, diff):
        fired = diff.get("failure_triggered")
        live = {c["id"] for c in self.live(cfg, ctx)}
        if not fired or fired not in live:
            return []
        return [{"type": "failure_triggered", "id": fired}]

    # --- resolution ----------------------------------------------------------------

    def resolve(self, cfg, ctx, observations, events):
        """At most one effect. `final_arc` is built here rather than in the handler because
        the condition's authored `title`/`ending_prompt` are this engine's config to read,
        and the handler's job is only to route an already-decided ending into the machinery
        that every other ending goes through."""
        by_id = {c["id"]: c for c in self.conditions(cfg)}
        for event in observations or []:
            if event.get("type") != "failure_triggered":
                continue
            condition = by_id.get(event["id"])
            if condition is None:
                continue
            return [Effect(
                "failure.trigger", reason=f"condition:{condition['id']}",
                cause=condition["id"],
                final_arc={"title": condition.get("title") or "The Ending",
                           "description": condition["ending_prompt"]},
            )]
        return []


ENGINE = register(TriggeredEnding())
# No register_effect here. "failure.trigger" is applied by story_engine, which owns
# _begin_endgame - see the module docstring on why that seam is deliberate.

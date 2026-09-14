"""`revelations` / `triggered_reveal` - engine v2 phase 4, port 3.

docs/ENGINE_V2_SPEC.md §7.5, docs/analysis_and_plans/ENGINE_V2/ENGINE_V2_PHASES.md phase 4 step 3. Owns v2's
`mechanics.revelations` wholesale: which entries are live, which have fired, the ordering
constraints between them, the placement queue spec §12 added, and both ends of the pipe -
the unrevealed triggers the observation pass is shown, and the revealed content the narrator
is shown.

**This is the first engine with a cadence (§5.2).** A story whose clue chain is exhausted
asks nothing at all, and a chain of twelve entries contributes one line rather than twelve.
§5.2 attaches a constraint to that privilege: an engine that may skip turns must phrase its
question over a window and tolerate the window being longer than one turn. This engine's
question is "did the narration below satisfy any of these triggers", which is about exactly
the turn it is asked on - so the skip is *structural*, never temporal. It skips when there
is nothing left to ask about, not when it decides to wait. An entry that is skipped because
an ordering constraint has not been met yet cannot fire during the skip, and that is the
whole point of authoring the constraint.

**Two fields became one.** `memory_fragments_revealed` and `revelations_eligible` were
always two halves of one question - "the narration satisfied this trigger, and did it
actually write it onto the page or not" - and §5.4 says an engine needing two fields is one
field with a richer type.

**`config.entries`, and declare-to-bind.** `mechanics.revelations` was a bare list, which
has nowhere to put an `"engine"` key, so it becomes `{"engine": ..., "entries": [...]}`.
Every other slot already had this shape; this one was the exception.

**What is deliberately NOT here: predicate triggers.** §7.5 wants triggers upgraded from
prose to predicates over engine state. The predicate evaluator belongs to `gate`
(§7.4/§7.9), which is phase 6, and building it here would be building phase 6 early and in
the wrong module. So triggers stay prose the observation pass judges, `after` gives ordering
without needing an expression language at all, and `requires` is phase 6's to add on the
evaluator `gate` brings. This engine is ported; its triggers are not yet upgraded, and the
two are separate claims.

**Storage stays put.** `plot.revelations_revealed` and `pacing.reveal_queue` keep their
homes, for the reason §8.3 gives: the engine owns the rules, not the storage, and moving
either would invalidate live saves to buy nothing. `reveal_queue` living under `pacing` is
a little odd now that a different engine owns it, but it is the pacing directive that
consumes it, and relocating it is schema-cutover work rather than port work.
"""
from . import Effect, MechanicEngine, ObservationField, register, register_effect

# How many revealed fragments' content the narrator is shown. A long game reveals more than
# a prompt should carry, and the most recent are the ones still live in the player's head.
# 12 because that is what story_engine.MEMORY_FRAGMENT_PROMPT_LIMIT was: the port moves who
# owns the cap, not what it is, and changing a number while relocating it is how a behaviour
# change hides inside a refactor.
REVEALED_PROMPT_LIMIT = 12


class TriggeredReveal(MechanicEngine):
    slot = "revelations"
    name = "triggered_reveal"
    # After inventory (40): a later `requires` predicate over items or standing (phase 6)
    # should read this turn's, and nothing here is read by an earlier engine.
    resolve_order = 50
    # §5.4. REVEALED MEMORIES is the only section, capped at REVEALED_PROMPT_LIMIT (12)
    # entries; 2600 is twelve two-line fragments plus the header. A story that trips this is
    # authoring fragments the narrator is being asked to recite rather than reference, which
    # is worth being told about rather than silently paying for.
    prompt_budget = 2600

    # --- configuration -------------------------------------------------------------

    def entries(self, cfg):
        """The authored fragments. Required for the same reason `scored_axis` requires a
        price list: a declared engine with nothing to reveal is a mechanic that can never
        fire, and silent inertness is what this architecture exists to remove."""
        entries = cfg.get("entries")
        if not entries:
            raise ValueError(
                "mechanics.revelations declares engine 'triggered_reveal' but authors no "
                "'entries'. The v2 shape was a bare list; v3 wraps it so the block has "
                "somewhere to carry `engine` (§8.1)."
            )
        return entries

    @staticmethod
    def revealed(ctx):
        return ctx["state"]["plot"]["revelations_revealed"]

    @staticmethod
    def queue(ctx):
        return ctx["state"]["pacing"].get("reveal_queue", [])

    def _placement_enabled(self, ctx):
        """Spec §12's queue only exists to be drained by the pacing directive, so a story
        with no `pacing_loop` has nowhere to place a reveal *into* and the queue would be
        dead state. Reading another module's presence, not another engine's state: this is
        a question about what the template authored, which is config, not the cross-engine
        coupling §8.2 rules out."""
        return bool(ctx["story"].get("mechanics", {}).get("pacing_loop"))

    def live(self, cfg, ctx):
        """Entries that could fire right now: not already revealed, and with every entry
        named in `after` already revealed.

        `after` is ordering without an expression language - a clue chain cannot fire out of
        sequence, which is §7.5's "optional ordering constraints" at the cost of one list
        per entry. An `after` naming an id the story does not have would block its entry
        forever, so an unknown id is ignored rather than treated as unmet: a typo should
        degrade to "no constraint", never to an unreachable reveal."""
        revealed = self.revealed(ctx)
        known = {entry["id"] for entry in self.entries(cfg)}
        out = []
        for entry in self.entries(cfg):
            if entry["id"] in revealed:
                continue
            blockers = [rid for rid in (entry.get("after") or []) if rid in known]
            if any(rid not in revealed for rid in blockers):
                continue
            out.append(entry)
        return out

    # --- observation ---------------------------------------------------------------

    def observations(self, cfg, ctx):
        """None once the chain is exhausted - the cadence (§5.2). One field otherwise."""
        live = self.live(cfg, ctx)
        if not live:
            return None
        triggers = "; ".join(f"{e['id']}: {e['trigger']}" for e in live)
        placing = self._placement_enabled(ctx)
        schema = (
            '  "revelations": {"revealed": ["<id of every LIVE TRIGGER below that the '
            'NARRATION itself wrote onto the page this turn>"]'
            + (', "eligible": ["<id of every LIVE TRIGGER whose condition the story has now '
               'satisfied but which the NARRATION did NOT write this turn - these wait for a '
               'better scene. Never list an id in both>"]' if placing else "")
            + "}"
        )
        context = f"\nLIVE TRIGGERS (id: condition): {triggers}"
        # A trigger is authored as a description of an event, and narration renders the
        # event rather than echoing the wording. Without this the model reads the list as
        # context rather than as something to evaluate and fires nothing: 0 of 2 across a
        # 24-turn playthrough whose turns 18 and 23 both plainly satisfied one
        # (docs/analysis_and_plans/SCHEMA_V2/PHASE_0_GATE_REPORT.md §4).
        instruction = (
            "Check the NARRATION against each LIVE TRIGGER and list the id of every one it "
            "satisfies this turn. Judge by what happens in the scene, not by whether the "
            "narration reuses the trigger's wording - a trigger describing an act is "
            "satisfied by the protagonist performing that act however it is written. Never "
            "force a match.\n"
        )
        if placing:
            # §12: the split between "satisfied AND written" and "satisfied but not yet
            # written" is the whole point, and without this sentence the model reads the two
            # as near-synonyms and puts the same id in both.
            instruction += (
                "A trigger can be satisfied without the narration having delivered the "
                "memory on the page. Put an id in revealed only if the NARRATION itself "
                "wrote it into the scene; if the condition is met but the memory has not "
                "surfaced, put it in eligible instead, and never in both.\n"
            )
        return [ObservationField("revelations", schema, context, instruction)]

    def events(self, cfg, ctx, diff):
        """Both halves of the field become events, and an id that is not live is dropped
        here - including one the model put in both lists, where `revealed` wins, since it is
        a claim about what is already on the page and `eligible` is only a request to place
        it later."""
        block = diff.get("revelations")
        if not isinstance(block, dict):
            return []
        live_ids = {e["id"] for e in self.live(cfg, ctx)}
        revealed = [rid for rid in (block.get("revealed") or []) if rid in live_ids]
        events = [{"type": "revelation_revealed", "id": rid} for rid in revealed]
        if self._placement_enabled(ctx):
            seen = set(revealed)
            for rid in block.get("eligible") or []:
                if rid in live_ids and rid not in seen:
                    seen.add(rid)
                    events.append({"type": "revelation_eligible", "id": rid})
        return events

    # --- resolution ----------------------------------------------------------------

    def resolve(self, cfg, ctx, observations, events):
        """Reveals first, then queueing, then a sweep of the queue.

        The sweep is not housekeeping: it is what stops the pacing directive asking the
        narrator for a memory the player has already read. §12 words the queue as "the
        directive consumes one entry per firing", but a firing is an instruction to the
        narrator, not a guarantee - popping unconditionally would silently drop a reveal any
        time the model ignored the bullet, and nothing would re-queue it, since its trigger
        fired once in a scene now well behind. Leaving the entry until it is actually
        reported means the worst case is the next firing citing it again, which is
        self-correcting rather than lossy."""
        turn = ctx["state"].get("pacing", {}).get("turn_count", 0)
        effects = []
        newly = set()
        for event in observations or []:
            if event.get("type") == "revelation_revealed":
                newly.add(event["id"])
                effects.append(Effect("revelations.reveal", reason="narration_delivered",
                                      id=event["id"], turn=turn))
        if not self._placement_enabled(ctx):
            return effects

        queue = list(self.queue(ctx))
        for event in observations or []:
            if event.get("type") != "revelation_eligible":
                continue
            rid = event["id"]
            if rid not in newly and rid not in queue:
                queue.append(rid)
                effects.append(Effect("revelations.queue", reason="trigger_met_unwritten",
                                      id=rid))
        # Bounded implicitly by the template's entry count, but a revealed entry would
        # otherwise sit at the head of the FIFO forever.
        revealed_now = set(self.revealed(ctx)) | newly
        remaining = [rid for rid in queue if rid not in revealed_now]
        if remaining != list(self.queue(ctx)):
            effects.append(Effect("revelations.set_queue", reason="drop_revealed",
                                  queue=remaining))
        return effects

    # --- prompt ---------------------------------------------------------------------

    def prompt_sections(self, cfg, ctx):
        """Only revealed content ever reaches the narrator here; only unrevealed triggers
        ever reach the observation pass. Neither sees the other half, which is CR-03 and is
        the reason a reveal cannot be spoiled by the prompt that is meant to detect it."""
        revealed_map = self.revealed(ctx)
        ordered = sorted(
            (e for e in self.entries(cfg) if e["id"] in revealed_map),
            key=lambda e: revealed_map[e["id"]].get("turn", 0),
            reverse=True,
        )
        if not ordered:
            return {}
        lines = "\n".join(f"- {e['content']}" for e in ordered[:REVEALED_PROMPT_LIMIT])
        return {"memories": (
            "REVEALED MEMORIES (the protagonist already knows these; reference them "
            f"naturally, do not re-reveal them as though they were new):\n{lines}")}

    def queued_content(self, cfg, ctx):
        """The `{queued_reveal}` interpolation, or None. FIFO, so the oldest eligible reveal
        is offered. Only the *content* - the narrator writes the reveal and is never handed
        the id or the trigger. Skips an entry revealed by other means since it was queued,
        so a stale queue entry can never ask for something already read."""
        revealed = self.revealed(ctx)
        by_id = {e["id"]: e for e in self.entries(cfg)}
        for rid in self.queue(ctx):
            if rid in by_id and rid not in revealed:
                return by_id[rid]["content"]
        return None


def _apply_reveal(ctx, effect):
    ctx["state"]["plot"]["revelations_revealed"][effect.payload["id"]] = {
        "turn": effect.payload["turn"]}


def _apply_queue(ctx, effect):
    ctx["state"]["pacing"].setdefault("reveal_queue", []).append(effect.payload["id"])


def _apply_set_queue(ctx, effect):
    ctx["state"]["pacing"]["reveal_queue"] = list(effect.payload["queue"])


ENGINE = register(TriggeredReveal())
register_effect("revelations.reveal", _apply_reveal)
register_effect("revelations.queue", _apply_queue)
register_effect("revelations.set_queue", _apply_set_queue)

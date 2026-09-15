"""`pacing_loop` / `beat_counter` - engine v2 phase 5, port 1 of 2.

docs/ENGINE_V2_SPEC.md §7.8, docs/analysis_and_plans/ENGINE_V2/ENGINE_V2_PHASES.md phase 5.
Owns the beat vocabulary the model classifies into, the counters that vocabulary feeds, the
resets that zero them, and the effective threshold (§13) that decides when a rule arms.

**A relocation, not a redesign.** §7.8 calls this one "already the target shape": the model
emits a beat name and an intensity, and every number after that is the engine's. So this port
moves code and changes no behaviour, which is what makes `test_pacing_loop.py` passing
*unmodified* a meaningful gate rather than a formality.

**One field, `beat`, carrying `{type, intensity}`.** §5.4: "an engine needing two is two
engines, or one field with a richer type", and these two were always one question - *what kind
of scene was this, and how hard did it land*. Phase 5 shipped them as two because its gate was
that `test_pacing_loop.py` not change and that file pinned both names; the merge is the step
that spends that edit deliberately rather than smuggling it in.

**Intensity stays inside the beat rather than becoming its own event.** It is meaningless
without a beat to qualify - an intensity with no type is not a weaker classification, it is no
classification - so nesting it is what makes the invalid state unrepresentable instead of
merely unlikely.

**What deliberately did NOT move.** `_section_pacing_directive` stays in `story_engine`: it runs
at prompt-assembly time rather than in the turn pipeline, and it reads the reveal queue, the
suppression predicates and the act. It calls this engine for `rule()` and `effective_threshold()`
so the §6.2/§13 arithmetic has one home, but the directive itself is narration assembly, not
state resolution. `check_and_advance_act` stays outside the registry entirely, per §7.9.
"""
import sys

from . import Effect, MechanicEngine, ObservationField, current_act, register, register_effect


class BeatCounter(MechanicEngine):
    slot = "pacing_loop"
    name = "beat_counter"
    # After the state-carrying engines (social 30, inventory 40, reveal 50, subplots 60) and
    # before failure (90). Nothing reads counters mid-resolve, so this only has to be stable -
    # but it sits after the engines whose events describe what the beat was made of.
    resolve_order = 70
    # §5.4. Contributes no narration section: the pacing *directive* is assembled by
    # story_engine's SECTIONS, which is not the registry's prompt surface.
    prompt_budget = 0

    # --- configuration -------------------------------------------------------------

    def beats(self, cfg):
        """The authored beat vocabulary. Required, never defaulted: a beat set is a story's
        dramatic physics (P-3), and `example` already disagrees with `new_babel` about how many
        beats a story even has - 2 against 4."""
        beats = cfg.get("beats")
        if not beats:
            raise ValueError(
                "mechanics.pacing_loop declares engine 'beat_counter' but authors no 'beats'. "
                "A pacing loop with no vocabulary can never classify anything."
            )
        return beats

    def rule(self, cfg):
        """v1 scope (spec §6.2): the schema accepts a list of rules so both correction
        directions are expressible without code, but v1 implements and tests exactly one rule
        per story - a template declaring more logs a warning and uses only the first. Multi-rule
        arbitration is deferred (spec §9) since nothing exercises it yet."""
        rules = cfg.get("rules", [])
        if not rules:
            return None
        if len(rules) > 1:
            print(
                f"WARNING: mechanics.pacing_loop declares {len(rules)} rules; v1 only supports "
                f"one per story - using '{rules[0]['id']}', ignoring the rest.",
                file=sys.stderr,
            )
        return rules[0]

    @staticmethod
    def effective_threshold(rule: dict, act: dict | None):
        """Spec §13 resolution order: exact act number -> "finale" if the current act is one ->
        the rule's base threshold. A null at any resolved level disables the rule for that act
        (the caller must treat a None return as "not armable/not eligible this act", not as
        "use the default")."""
        by_act = rule.get("threshold_by_act", {})
        if act:
            act_key = str(act["act_number"])
            if act_key in by_act:
                return by_act[act_key]
            if act.get("is_finale") and "finale" in by_act:
                return by_act["finale"]
        return rule.get("threshold")

    # --- observation ---------------------------------------------------------------

    def observations(self, cfg, ctx):
        """One field (§5.4), carrying the classification and its weight together.

        No cadence (§5.2): a beat is classified every turn, because a turn that advanced nothing
        is itself a beat type in every vocabulary authored so far, and "no answer" and "the quiet
        one" have to stay distinguishable."""
        beats = self.beats(cfg)
        # Definitions verbatim from the template (spec §5), never a constant: the vocabularies
        # genuinely differ between stories, and a hardcoded set would silently reclassify one of
        # them (PHASE_6_HANDOFF.md §2 on why the spec's 4-beat default did not survive validation).
        lines = "\n".join(f"- {name}: {info['definition']}" for name, info in beats.items())
        context = (
            "\nBEAT TYPES (choose exactly one for beat_type, per its definition below):\n"
            f"{lines}"
        )
        tie_break = cfg.get("tie_break", "")
        if tie_break:
            context += f"\n{tie_break}"
        schema = (
            f'  "beat": {{"type": "<exactly one of: {", ".join(beats)} - whichever beat type '
            'above best matches what actually happened on the page this scene>", '
            '"intensity": <integer 1-3 for that beat - 1: pressure present, no immediate '
            "physical danger; 2: direct confrontation or a forced decision in the room; 3: "
            'physical danger, active pursuit, or body-horror escalation>}'
        )
        return [ObservationField("beat", schema, context)]

    def events(self, cfg, ctx, diff):
        """A beat outside the authored vocabulary is dropped rather than stored, and a missing or
        unparseable intensity floors to 1 rather than failing the turn - the classification is
        advisory pressure, and a turn that loses it should still land."""
        block = diff.get("beat")
        if not isinstance(block, dict):
            return []
        beat_type = block.get("type")
        if beat_type not in self.beats(cfg):
            return []
        try:
            intensity = max(1, min(3, int(block.get("intensity"))))
        except (TypeError, ValueError):
            intensity = 1
        return [{"type": "beat", "beat": beat_type, "intensity": intensity}]

    # --- resolution ----------------------------------------------------------------

    def resolve(self, cfg, ctx, observations, events):
        """Record the beat, move its counters, then arm whatever crossed.

        The counter effect carries the whole map rather than one delta per counter, for the
        reason every other engine here emits absolutes: "the counters are now this" replays
        identically where "add 2 to stasis" depends on what has already been applied this turn.
        It also carries the seed - a save whose `counters` key does not exist yet starts from the
        authored `counters` block, and that seeding has to survive the move."""
        beats = self.beats(cfg)
        rule = self.rule(cfg)
        counters = dict(ctx["state"]["pacing"].get("counters") or cfg.get("counters", {}))
        effects = []
        for event in observations or []:
            if event.get("type") != "beat":
                continue
            beat_type, intensity = event["beat"], event["intensity"]
            effects.append(Effect("pacing.beat", reason=f"beat:{beat_type}",
                                  type=beat_type, intensity=intensity))

            beat_def = beats[beat_type]
            feeds = beat_def.get("feeds")
            if feeds:
                counters[feeds] = counters.get(feeds, 0) + intensity
            for reset in beat_def.get("resets", []):
                counters[reset] = 0
                # A rule watching a counter that just went to zero is no longer armed: the
                # pressure it was waiting on has been released by the story itself.
                if rule and rule["watch"] == reset:
                    effects.append(Effect("pacing.disarm", reason=f"reset:{beat_type}",
                                          rule=rule["id"]))
            effects.append(Effect("pacing.counters", reason=f"beat:{beat_type}",
                                  counters=dict(counters)))

            if rule:
                threshold = self.effective_threshold(rule, current_act(ctx))
                if threshold is not None and counters.get(rule["watch"], 0) >= threshold:
                    effects.append(Effect("pacing.arm", reason=f"threshold:{threshold}",
                                          rule=rule["id"]))
        return effects


# Lazy-init throughout: a save predating this module has none of these keys, and per
# docs/ARCHITECTURE.md's "Keeping LLM Context Bounded" this project never writes a migration for
# that - setdefault instead, same as every other lazily-added field.
def _apply_beat(ctx, effect):
    ctx["state"]["pacing"]["last_beat"] = {
        "type": effect.payload["type"], "intensity": effect.payload["intensity"]
    }


def _apply_counters(ctx, effect):
    ctx["state"]["pacing"]["counters"] = dict(effect.payload["counters"])


def _apply_arm(ctx, effect):
    ctx["state"]["pacing"].setdefault("armed", {}).setdefault(
        effect.payload["rule"], {"deferrals": 0}
    )


def _apply_disarm(ctx, effect):
    ctx["state"]["pacing"].setdefault("armed", {}).pop(effect.payload["rule"], None)


ENGINE = register(BeatCounter())
register_effect("pacing.beat", _apply_beat)
register_effect("pacing.counters", _apply_counters)
register_effect("pacing.arm", _apply_arm)
register_effect("pacing.disarm", _apply_disarm)

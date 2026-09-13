"""`stats` / `bounded_counter` - the first ported mechanic (engine v2 phase 2).

docs/ENGINE_V2_SPEC.md §7.1, docs/ENGINE_V2_PHASES.md phase 2. Owns every decision the
engine used to spread across story_engine: the bounds and their defaults, the clamp, the
visibility dial, and the deterministic readout that P-7 exists for.

**Declare-to-bind.** A story gets this mechanic by authoring
`mechanics.stats.engine = "bounded_counter"`, and gets nothing without it. That deletes the
old implicit `STAT_FLOOR = 0`, which was a live P-3 violation: SCHEMA_V2_SPEC §3.6 claims
`mechanics.stats` "replaces the global STAT_FLOOR = 0", and it did not - it shadowed it,
leaving an engine constant deciding a creative question for any story that stayed quiet.
A story that wants a floor of 0 now says so.

**Stats stay at `state.protagonist.stats`.** The engine owns the rules, not the storage.
Moving the sheet into `state.mechanics.stats` would buy nothing and would break every
existing save for no reason - see phase 2's note in ENGINE_V2_PHASES.md on why the schema
cutover is deferred to the first phase that genuinely relocates save state.

**What is deliberately still v2-shaped.** The state-update pass still asks for a
`stat_changes` delta map - a number the model chooses. Phase 4 moved the field onto the
real `observations()` contract alongside the engines ported beside it, but did not convert
it to the E-3 event vocabulary §5.1 wants ("travel, long", priced by the engine): that needs
a per-axis `costs` table (§8.1) authored in every story that has stats, which is content
work for a mechanic that is already ported. See ENGINE_V2_PHASES.md phase 4 for why it is
tracked as its own item rather than folded into one of the five ports.
"""
import re

from . import Effect, MechanicEngine, ObservationField, register, register_effect

# Only used when a bound story omits `floor` - not a global default any more, since an
# unbound story has no floor at all rather than falling back to one.
DEFAULT_FLOOR = 0


class BoundedCounter(MechanicEngine):
    slot = "stats"
    name = "bounded_counter"
    # Before relationships/inventory: a later engine reading a stat threshold should see
    # this turn's value, not last turn's.
    resolve_order = 20
    # §5.4. Largest real section today is the_missing_core's readout footer at 433 chars;
    # 600 leaves headroom for a longer axis list without being vacuous. What reaches a
    # prompt must stay bounded even though the disk record need not.
    prompt_budget = 600

    # --- configuration -------------------------------------------------------------

    def bounds(self, cfg):
        return cfg.get("floor", DEFAULT_FLOOR), cfg.get("ceiling")

    def visible(self, cfg):
        """Whether the narrator may state a raw number to the player. Defaults False: the
        opaque reading is what every story predating the dial relies on."""
        return bool(cfg.get("visible", False))

    def readout(self, cfg):
        """`readout` is only live when it actually names labels - an empty one renders
        nothing and must not trigger the token instruction."""
        cfg_readout = cfg.get("readout")
        return cfg_readout if cfg_readout and cfg_readout.get("labels") else None

    @staticmethod
    def current(ctx):
        return ctx["state"]["protagonist"].get("stats", {})

    # --- observation ---------------------------------------------------------------

    def observations(self, cfg, ctx):
        """The `stat_changes` field, or None when the story has no stats seeded yet (P-2:
        no stats, no field). The axis list is interpolated so the model can never introduce
        an axis outside the fixed, story-authored set.

        Phase 4 moved this off the bespoke `schema_field()` story_engine used to call by
        name and onto the real §3.1 contract, alongside the four engines ported beside it.
        The field itself is still v2-shaped - a delta map the model chooses the numbers for,
        not the event vocabulary §5.1 wants it to become. Converting it needs per-axis
        `costs` tables (§8.1) in every story that has stats, which is content work with no
        engine ported behind it; see ENGINE_V2_PHASES.md phase 4 for why it is called out
        separately rather than smuggled in here.

        Its context line (CURRENT STATS) stays where it is: unlike the other engines',
        it is interpolated into the prompt body rather than appended, and moving it would
        change the observation prompt for no gain this phase."""
        stats = self.current(ctx)
        if not stats:
            return None
        schema = (
            f'  "stat_changes": {{"<stat name, must be one of: {", ".join(stats)}>": <integer '
            "delta this turn, positive or negative - only stats the turn's events actually "
            "moved, never a stat name outside that fixed list>}"
        )
        return [ObservationField("stat_changes", schema)]

    def events(self, cfg, ctx, diff):
        """One event carrying the whole delta map. `resolve` already reads this shape - it
        is what story_engine hand-built at the phase 2 call site - so the port is the
        translation moving into the engine that owns the field, not a change of format."""
        changes = diff.get("stat_changes")
        return [{"type": "stat_changes", "changes": changes}] if changes else []

    # --- resolution ----------------------------------------------------------------

    def resolve(self, cfg, ctx, observations, events):
        """Clamp each requested delta and emit one effect per axis that actually moves.

        Only ever adjusts an axis already present in protagonist.stats - the CLAUDE.md
        invariant that the model can never introduce a new stat axis lives here now."""
        floor, ceiling = self.bounds(cfg)
        stats = self.current(ctx)
        effects = []
        for observation in observations or []:
            if observation.get("type") != "stat_changes":
                continue
            for axis, delta in (observation.get("changes") or {}).items():
                if axis not in stats:
                    continue
                try:
                    moved = stats[axis] + int(delta)
                except (TypeError, ValueError):
                    continue
                value = max(floor, moved)
                if ceiling is not None:
                    value = min(ceiling, value)
                effects.append(Effect("stats.set", reason=f"stat_changes:{axis}",
                                      axis=axis, value=value))
        return effects

    # --- prompt ---------------------------------------------------------------------

    def prompt_sections(self, cfg, ctx):
        """The two fragments this mechanic contributes, both omitted entirely when the
        story has no stats seeded. `player_line` carries its own leading separator because
        it is appended inline to the PLAYER line."""
        stats = self.current(ctx)
        if not stats:
            return {}
        visible = self.visible(cfg)
        shown = "SHOWN to the player by this story" if visible else "opaque to the player"
        return {"player_line": f" | Stats ({shown}): {stats}",
                "footer": self._footer(cfg, visible)}

    def _footer(self, cfg, visible):
        if not visible:
            return (
                "\nThe PLAYER line's Stats are for your own internal reasoning only - never state a "
                "stat's raw numeric value to the player. Reflect what it means narratively instead "
                "(strain, fatigue, confidence, risk) without quoting the number."
            )
        readout = self.readout(cfg)
        if not readout:
            return (
                "\nThe PLAYER line's Stats are known to the player in this story and their raw "
                "numeric values may be stated directly, in the voice and format the story's own "
                "rules establish for them. Report every change you narrate through stat_changes "
                "so the numbers you show stay true to the state."
            )
        # P-7: the model marks the place, the engine fills it in. It is never asked to
        # transcribe a number, because measured on a real 70-turn save it does not - the
        # displayed SYNC read 26 for four consecutive turns while the save held 34.
        token = readout.get("token", "[[STATS]]")
        return (
            f"\nThis story shows the player their own figures, but you must NEVER write a "
            f"number for one yourself. Where a line of figures belongs, put {token} alone "
            f"on its own line and nothing else - no labels, no values, no punctuation. It "
            f"is replaced with the true current figures after your reply. Writing the "
            f"numbers out by hand instead will show the player values that are wrong. "
            f"Report what actually changed through stat_changes as normal."
        )

    # --- render ---------------------------------------------------------------------

    def line(self, cfg, ctx):
        """The authoritative stat line, built from state. Label order follows the authored
        `labels` dict, which JSON preserves, so a story controls ordering without the engine
        having an opinion. A label with no seeded stat is skipped, not rendered as 0."""
        readout = self.readout(cfg)
        if not readout:
            return None
        stats = self.current(ctx)
        entry_format = readout.get("entry_format", "**{label}** {value}")
        parts = [entry_format.format(label=label, value=stats[key])
                 for key, label in readout["labels"].items() if key in stats]
        return readout.get("separator", " - ").join(parts) if parts else None

    def render(self, cfg, ctx, text):
        """Substitute the token, and - as a backstop - rewrite any figure line the model
        wrote by hand anyway. The backstop keys on a line carrying two or more configured
        labels each followed by a number, which prose does not accidentally resemble.
        Without it the guarantee would hold only while the model cooperated, which is the
        assumption P-7 exists to remove."""
        readout = self.readout(cfg)
        line = self.line(cfg, ctx)
        if not readout or line is None:
            return text
        text = text.replace(readout.get("token", "[[STATS]]"), line)
        labels = [re.escape(l) for l in readout["labels"].values()]
        label_hit = r"(?:" + "|".join(labels) + r")\**\s*-?\s*\d+"
        handwritten = re.compile(rf"^.*?{label_hit}.*?{label_hit}.*$", re.MULTILINE)
        return handwritten.sub(lambda m: line, text)


def _apply_set(ctx, effect):
    ctx["state"]["protagonist"].setdefault("stats", {})[effect.payload["axis"]] = effect.payload["value"]


ENGINE = register(BoundedCounter())
register_effect("stats.set", _apply_set)

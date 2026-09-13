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

**What is deliberately still v2-shaped.** `observations()` returns None and the state-update
pass still asks for a `stat_changes` delta map through `schema_field()`. Converting that to
an E-3 event vocabulary ("travel, long" priced by the engine) changes the prompt, and phase
2's gate is that no observable behaviour changes. Phase 4 is where that conversion belongs.
"""
import re

from . import Effect, MechanicEngine, register, register_effect

# Only used when a bound story omits `floor` - not a global default any more, since an
# unbound story has no floor at all rather than falling back to one.
DEFAULT_FLOOR = 0


class BoundedCounter(MechanicEngine):
    slot = "stats"
    name = "bounded_counter"
    # Before relationships/inventory: a later engine reading a stat threshold should see
    # this turn's value, not last turn's.
    resolve_order = 20

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

    def schema_field(self, cfg, ctx):
        """The `stat_changes` line for the state-update schema, or None when the story has
        no stats seeded yet (P-2: no stats, no field). The axis list is interpolated so the
        model can never introduce an axis outside the fixed, story-authored set."""
        stats = self.current(ctx)
        if not stats:
            return None
        return (
            f'  "stat_changes": {{"<stat name, must be one of: {", ".join(stats)}>": <integer '
            "delta this turn, positive or negative - only stats the turn's events actually "
            "moved, never a stat name outside that fixed list>}"
        )

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

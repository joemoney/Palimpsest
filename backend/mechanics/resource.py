"""`stats` / `bounded_counter` - the first ported mechanic (engine v2 phase 2).

docs/ENGINE_V2_SPEC.md §7.1, docs/analysis_and_plans/ENGINE_V2/ENGINE_V2_PHASES.md phase 2. Owns every decision the
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
existing save for no reason - see phase 2's note in docs/analysis_and_plans/ENGINE_V2/ENGINE_V2_PHASES.md on why the schema
cutover is deferred to the first phase that genuinely relocates save state.

**What is deliberately still v2-shaped.** The state-update pass still asks for a
`stat_changes` delta map - a number the model chooses. Phase 4 moved the field onto the
real `observations()` contract alongside the engines ported beside it, but did not convert
it to the E-3 event vocabulary §5.1 wants ("travel, long", priced by the engine): that needs
a per-axis `costs` table (§8.1) authored in every story that has stats, which is content
work for a mechanic that is already ported. See docs/analysis_and_plans/ENGINE_V2/ENGINE_V2_PHASES.md phase 4 for why it is
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

    def axes(self, cfg):
        """§8.1's per-axis block, or {}. Absent means every axis shares the block-level
        floor/ceiling and the story prices its own stats through the model (see
        `observations`) - P-2 again: no `axes`, no per-axis feature."""
        return cfg.get("axes") or {}

    def bounds(self, cfg, axis=None):
        """Block-level bounds, overridden per axis where `axes.<axis>` says so. The block
        level stays the default rather than being replaced, because most stories want one
        floor for everything and should not have to restate it per axis."""
        floor, ceiling = cfg.get("floor", DEFAULT_FLOOR), cfg.get("ceiling")
        per_axis = self.axes(cfg).get(axis) or {}
        return per_axis.get("floor", floor), per_axis.get("ceiling", ceiling)

    def costs(self, cfg):
        """`{event_key: {axis: magnitude}}`, inverted from the authored per-axis tables.

        Authored per axis because that is how a designer thinks about it - "what moves fuel"
        - and consumed per event because that is what arrives from the observation pass. One
        event may move several axes, which is the whole reason a salvage run can cost frame
        and buy reach in the same breath."""
        table = {}
        for axis, spec in self.axes(cfg).items():
            for key, magnitude in ((spec or {}).get("costs") or {}).items():
                table.setdefault(key, {})[axis] = magnitude
        return table

    def drift(self, cfg):
        """Per-axis `per_turn`, for the axes that have one. §7.1: a deadline that only
        arrives when the model remembers to decrement it is not a deadline."""
        return {axis: (spec or {}).get("per_turn")
                for axis, spec in self.axes(cfg).items()
                if (spec or {}).get("per_turn")}

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

        **Two shapes, and which one you get is authored.** A story that writes per-axis
        `costs` tables (§8.1) is asked what *happened* - a key from its own vocabulary - and
        the engine prices it. A story that writes none keeps the v2 delta map, where the
        model chooses the numbers. That is declare-to-bind inside one engine rather than a
        back-compat shim: an absent `costs` table means the story has not said what anything
        is worth, and inventing prices for it would be the engine holding a creative opinion
        (P-3).

        **Why the priced shape is worth authoring.** Measured over 24 held-out turns, three
        runs each, `stat_changes` reproduces at **25%** - the least reproducible field in the
        observation pass, below even `subplot_beats`. Discarding the magnitudes and asking
        only which axes moved and in which direction still only reaches 29%: the model does
        not agree with itself about *which* axis a turn touched. Pricing does not fix the
        classification - `subplot_beats` has a closed vocabulary and still sits at 33% - but
        it makes the consequence of a given answer exact and authored instead of invented,
        which is P-7's whole claim. See TIER_OBSERVATION_MEASUREMENT.md.

        Its context line (CURRENT STATS) stays where it is: unlike the other engines',
        it is interpolated into the prompt body rather than appended, and moving it would
        change the observation prompt for no gain."""
        stats = self.current(ctx)
        if not stats:
            return None
        vocabulary = sorted(self.costs(cfg))
        if not vocabulary:
            schema = (
                f'  "stat_changes": {{"<stat name, must be one of: {", ".join(stats)}>": <integer '
                "delta this turn, positive or negative - only stats the turn's events actually "
                "moved, never a stat name outside that fixed list>}"
            )
            return [ObservationField("stat_changes", schema)]
        schema = (
            f'  "stat_events": ["<zero or more of: {", ".join(vocabulary)} - name only what '
            "the narration actually shows happening this turn, once per occurrence. Never a "
            'number: what each one costs is fixed by the story, not by you>"]'
        )
        return [ObservationField("stat_events", schema)]

    def events(self, cfg, ctx, diff):
        """Whichever field this story authored. A key outside the vocabulary is dropped
        rather than guessed at, for the same reason `weighted_threads` drops a bare number:
        a mechanic that is only sometimes enforced is not enforced."""
        vocabulary = self.costs(cfg)
        if vocabulary:
            return [{"type": "stat_event", "key": key}
                    for key in (diff.get("stat_events") or [])
                    if isinstance(key, str) and key in vocabulary]
        changes = diff.get("stat_changes")
        return [{"type": "stat_changes", "changes": changes}] if changes else []

    # --- resolution ----------------------------------------------------------------

    def resolve(self, cfg, ctx, observations, events):
        """Clamp each requested delta and emit one effect per axis that actually moves.

        Only ever adjusts an axis already present in protagonist.stats - the CLAUDE.md
        invariant that the model can never introduce a new stat axis lives here now."""
        costs = self.costs(cfg)
        stats = self.current(ctx)
        # Projected, not read fresh per effect: several events in one turn may move the same
        # axis, and each has to price against what the ones before it already did, or the
        # last one silently wins. Absolute values for the same reason every other engine
        # emits them - "set it to 12" replays, "subtract 3" depends on what already applied.
        projected = dict(stats)
        effects = []

        def move(axis, delta, reason):
            if axis not in projected:
                return                       # CLAUDE.md: the model can never add an axis
            try:
                moved = projected[axis] + delta
            except TypeError:
                return
            floor, ceiling = self.bounds(cfg, axis)
            value = max(floor, moved)
            if ceiling is not None:
                value = min(ceiling, value)
            if value != projected[axis]:
                projected[axis] = value
                effects.append(Effect("stats.set", reason=reason, axis=axis, value=value))

        for observation in observations or []:
            kind = observation.get("type")
            if kind == "stat_event":
                key = observation.get("key")
                for axis, magnitude in (costs.get(key) or {}).items():
                    move(axis, magnitude, f"event:{key}")
            elif kind == "stat_changes":
                for axis, delta in (observation.get("changes") or {}).items():
                    try:
                        move(axis, int(delta), f"stat_changes:{axis}")
                    except (TypeError, ValueError):
                        continue

        # §7.1: drift ticks every turn whether or not anything was observed, which is what
        # makes a deadline arrive rather than a number the model remembers to decrement.
        for axis, per_turn in self.drift(cfg).items():
            move(axis, per_turn, "per_turn")
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

    def _report_clause(self, cfg):
        """What to tell the narrator to report changes *through*. Naming the wrong field is
        not cosmetic - it is the narration prompt instructing the model to answer a question
        the observation pass never asks."""
        return ("stat_events, naming what happened rather than any number"
                if self.costs(cfg) else "stat_changes")

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
                "rules establish for them. Report every change you narrate through "
                f"{self._report_clause(cfg)} so the numbers you show stay true to the state."
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
            f"Report what actually changed through {self._report_clause(cfg)} as normal."
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

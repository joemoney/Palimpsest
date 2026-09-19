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

from . import (TURN_SCRATCH, Effect, MechanicEngine, ObservationField, register,
               register_effect)

# Only used when a bound story omits `floor` - not a global default any more, since an
# unbound story has no floor at all rather than falling back to one.
DEFAULT_FLOOR = 0


class BoundedCounter(MechanicEngine):
    slot = "stats"
    name = "bounded_counter"
    # Before relationships/inventory: a later engine reading a stat threshold should see
    # this turn's value, not last turn's.
    resolve_order = 20
    # §5.4. Three instructions now: the absolute-token half (477 chars), the delta_block
    # marker half (399) when a story authors one, and the tier block - which, unlike the
    # other two, DOES scale, with one authored line per axis currently sitting in a tier
    # that wrote guidance. the_missing_core's five tiers are one line at a time (only the
    # reached tier renders), but a story tiering every axis at once pays for all of them.
    # Measured, not asserted: re-run scripts/measure_baseline.py after changing any of them.
    prompt_budget = 1400

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
        """Per-axis `(per_turn, interval)`, for the axes that have a `per_turn`. §7.1: a
        deadline that only arrives when the model remembers to decrement it is not a
        deadline. `per_turn_interval` (default 1, every turn) lets a story make that
        arrival slower than every turn - a signature that fades over a week reads
        differently authored as -1 every turn than as -1 every 5 - without weakening the
        guarantee that it still arrives on its own: the interval is a cadence, not a
        chance."""
        return {axis: ((spec or {}).get("per_turn"), (spec or {}).get("per_turn_interval", 1))
                for axis, spec in self.axes(cfg).items()
                if (spec or {}).get("per_turn")}

    def visible(self, cfg):
        """Whether the narrator may state a raw number to the player. Defaults False: the
        opaque reading is what every story predating the dial relies on."""
        return bool(cfg.get("visible", False))

    def tiers(self, cfg, axis):
        """Authored `axes.<axis>.tiers`, sorted so `tier_for` can scan deterministically.

        The same shape scored_axis uses for relationships (`at`/`label`/`narration`), ported
        here because a stat crossing a threshold is the same kind of fact as a standing
        crossing one: a band the narration has to honour, not a number to recite. Only the
        upward direction, unlike scored_axis - a stat has a floor and climbs away from it,
        so there is no "at or below" case to disambiguate."""
        return sorted((self.axes(cfg).get(axis) or {}).get("tiers") or [], key=lambda t: t["at"])

    def tier_for(self, cfg, axis, value):
        """The tier `value` currently sits in on `axis`, or None if it has reached none."""
        best = None
        for tier in self.tiers(cfg, axis):
            if value >= tier["at"] and (best is None or tier["at"] >= best["at"]):
                best = tier
        return best

    def readout(self, cfg):
        """`readout` is only live when it actually names labels - an empty one renders
        nothing and must not trigger the token instruction."""
        cfg_readout = cfg.get("readout")
        return cfg_readout if cfg_readout and cfg_readout.get("labels") else None

    def delta_block(self, cfg):
        """`readout.delta_block`, or None. P-7 one level up from the `token`: the token
        renders *absolutes* (what the sheet says now), this renders *deltas* (what moved
        this turn) and composes the surrounding wrapper too, so the model writes neither the
        figure nor the frame around it - only a marker and, at most, a short clause.

        P-2: a story that authors none keeps today's behaviour, where the model writes the
        whole block itself. A block naming no marker is not live, for the same reason an
        empty `readout` is not."""
        block = (self.readout(cfg) or {}).get("delta_block")
        return block if block and block.get("marker") else None

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

        # §7.1: drift ticks on its authored cadence whether or not anything was observed,
        # which is what makes a deadline arrive rather than a number the model remembers to
        # decrement. turn_count is already the post-increment "this is turn N" value by the
        # time resolve() runs (see update_state_after_turn), so interval=5 ticks on turn 5,
        # 10, 15... - a story that authors no interval keeps today's every-turn cadence.
        turn_count = ctx["state"]["pacing"]["turn_count"]
        for axis, (per_turn, interval) in self.drift(cfg).items():
            if turn_count % interval == 0:
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
        sections = {"player_line": f" | Stats ({shown}): {stats}",
                    "footer": self._footer(cfg, visible)}
        if (tiers := self._tier_footer(cfg, ctx)):
            sections["tiers"] = tiers
        return sections

    def _tier_footer(self, cfg, ctx):
        """One authored line per axis currently sitting in a tier that wrote guidance.

        Same rule scored_axis's own tier footer follows: a `label`-only tier already says
        everything it has to say wherever the figure is displayed, so repeating it here
        would be prompt spend for nothing."""
        stats = self.current(ctx)
        lines = []
        for axis, value in stats.items():
            tier = self.tier_for(cfg, axis, value)
            if tier and tier.get("narration"):
                label = (self.readout(cfg) or {}).get("labels", {}).get(axis, axis).upper()
                lines.append(f"- {label} ({tier['label']}): {tier['narration']}")
        return f"\nWHERE THE FIGURES STAND NOW:\n" + "\n".join(lines) if lines else ""

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
        ) + self._delta_block_footer(cfg)

    def _delta_block_footer(self, cfg):
        """The marker instruction, and only when a delta_block is authored (P-2).

        It describes the marker and the clause, and deliberately does not restate what the
        clause may be *about* beyond naming whose voice it is - that is the story's rule to
        make, and the word cap is what this side enforces."""
        block = self.delta_block(cfg)
        if not block:
            return ""
        marker, cap = block["marker"], block.get("clause_max_words")
        limit = f", at most {cap} words" if cap else ""
        return (
            f"\nWhere a change actually lands in the scene, put [[{marker}]] on its own line "
            f"- or [[{marker}: <clause>]] to carry one short remark in its own voice{limit}. "
            f"Never write the surrounding heading, a label or a figure yourself: the block is "
            f"composed from what genuinely moved this turn, so a marker on a turn where "
            f"nothing moved renders nothing at all, and an over-long clause is dropped whole."
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

    @staticmethod
    def _degraded_token_re(token):
        """A line that is *only* the token's bare word, optionally bracketed and/or
        wrapped in one of the three emphasis markers - observed in production
        (the_missing_core, turn 100): the model wrote `**STATS**` instead of copying
        `[[STATS]]` literally, the same instinct that turned a bare `OPTIONS:` heading
        bold elsewhere (see parse_narration_and_options's own tolerance). Anchored to the
        whole line and to the token's exact bare word, so ordinary prose that happens to
        mention the word can't be mistaken for the placeholder."""
        # [ \t]*, not \s* - MULTILINE's ^/$ already anchor to line boundaries; \s* would
        # additionally match newlines and could swallow the blank lines around the match,
        # shifting the surrounding narration's paragraph breaks for no reason.
        bare = re.escape(re.sub(r"[^A-Za-z0-9]", "", token))
        return re.compile(
            rf"^[ \t]*(?:\*\*|__)?[ \t]*\[{{0,2}}[ \t]*{bare}[ \t]*\]{{0,2}}[ \t]*(?:\*\*|__)?[ \t]*$",
            re.IGNORECASE | re.MULTILINE,
        )

    def render(self, cfg, ctx, text):
        """Substitute the token, and - as a backstop - rewrite any figure line the model
        wrote by hand anyway. The backstop keys on a line carrying two or more configured
        labels each followed by a number, which prose does not accidentally resemble.
        Without it the guarantee would hold only while the model cooperated, which is the
        assumption P-7 exists to remove."""
        readout = self.readout(cfg)
        if not readout:
            return text
        line = self.line(cfg, ctx)
        if line is not None:
            token = readout.get("token", "[[STATS]]")
            if token in text:
                text = text.replace(token, line)
            else:
                # The literal token never appeared at all - try the degraded shape before
                # falling through to the handwritten-figures backstop below, which can't
                # catch this: a bare mis-styled heading carries no label+number pairs to key
                # on, so without this the player sees a dangling "**STATS**" with nothing
                # under it and no figures at all, worse than either a token or the truth.
                text = self._degraded_token_re(token).sub(lambda m: line, text)
            labels = [re.escape(l) for l in readout["labels"].values()]
            label_hit = r"(?:" + "|".join(labels) + r")\**\s*-?\s*\d+"
            handwritten = re.compile(rf"^.*?{label_hit}.*?{label_hit}.*$", re.MULTILINE)
            text = handwritten.sub(lambda m: line, text)
        # Strictly after the backstop above. A composed delta block carries two or more
        # label+number pairs on one line, which is the exact shape that regex rewrites into
        # the absolute line - substituting the marker first would feed it its own output.
        return self._render_delta_block(cfg, ctx, text)

    def _delta_body(self, cfg, ctx):
        """The priced half of the block: what actually moved this turn, after clamping.

        Read back from TURN_SCRATCH rather than re-priced from the event log, because the
        event log records what the model *named* and the costs table what it is *worth* -
        neither knows that an axis was already on its floor and only moved 2 of the 5 it
        was charged. Showing the charge instead of the movement would be P-7 telling the
        player a number that is not true, which is the whole thing it exists to stop.

        Label order follows the authored `labels` dict, like `line()`."""
        block, labels = self.delta_block(cfg), self.readout(cfg)["labels"]
        deltas = (ctx.get(TURN_SCRATCH) or {}).get("stat_deltas") or {}
        fmt = block.get("fact_format", "{label} {signed}")
        parts = [fmt.format(label=label, signed=f"{deltas[axis]:+d}")
                 for axis, label in labels.items() if deltas.get(axis)]
        return block.get("separator", " ").join(parts) if parts else None

    def _clause_cap(self, cfg, ctx):
        """How many words of its own the model may add to the block this turn.

        A constant unless `clause_max_words_axis` names an axis, in which case that axis's
        current tier decides it and the block-level value is the floor for any value below
        the lowest authored tier. This is what makes terseness a *measurement* rather than
        an instruction: a voice that is allowed four words cannot write a paragraph, however
        the prompt is worded, so the register cannot drift the way a prose rule about it
        would. A tier that authors no cap of its own leaves the block-level one in force."""
        block = self.delta_block(cfg)
        cap = block.get("clause_max_words")
        axis = block.get("clause_max_words_axis")
        if not axis:
            return cap
        tier = self.tier_for(cfg, axis, self.current(ctx).get(axis, 0))
        return (tier or {}).get("clause_max_words", cap)

    def _render_delta_block(self, cfg, ctx, text):
        """Replace the model's marker with the engine-composed block, or with nothing.

        Nothing is the common case and the point of the design: no priced movement means no
        block, so "a status readout requires a reason" stops being a rule the model has to
        remember and becomes a property of how the text is assembled. An over-long clause is
        dropped rather than truncated - a sentence cut mid-phrase reads as a bug, and the cap
        exists to make a tactical briefing structurally impossible, which dropping achieves
        and truncating does not."""
        block = self.delta_block(cfg)
        if not block:
            return text
        marker = re.escape(block["marker"])
        pattern = re.compile(rf"\[\[\s*{marker}\s*(?::\s*([^\]]*?))?\s*\]\]")
        if not pattern.search(text):
            return text
        body = self._delta_body(cfg, ctx)
        cap = self._clause_cap(cfg, ctx)

        def replace(match):
            if body is None:
                return ""
            clause = (match.group(1) or "").strip()
            if clause and cap and len(clause.split()) > int(cap):
                clause = ""
            joined = block.get("separator", " ").join([body] + ([clause] if clause else []))
            return block.get("wrapper", "{body}").format(body=joined)

        return re.sub(r"\n{3,}", "\n\n", pattern.sub(replace, text)).strip()


def _apply_set(ctx, effect):
    stats = ctx["state"]["protagonist"].setdefault("stats", {})
    axis, value = effect.payload["axis"], effect.payload["value"]
    before = stats.get(axis)
    stats[axis] = value
    # Record what the move actually came to, for delta_block to read back. Drift is excluded
    # because the story's own rule scopes a readout to things that happened in the room - it
    # lists "a figure actually moved" beside gaining an unlock and the System issuing a
    # directive, all events - and per_turn is ambient by construction. Including it would
    # qualify every single turn on any story with a drifting axis, which is the opposite of
    # "and that is most scenes".
    if isinstance(before, int) and not effect.reason.startswith("per_turn"):
        deltas = ctx.setdefault(TURN_SCRATCH, {}).setdefault("stat_deltas", {})
        deltas[axis] = deltas.get(axis, 0) + (value - before)


ENGINE = register(BoundedCounter())
register_effect("stats.set", _apply_set)

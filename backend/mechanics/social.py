"""`relationships` / `scored_axis` - engine v2 phase 4, port 1.

docs/ENGINE_V2_SPEC.md §7.2, docs/ENGINE_V2_PHASES.md phase 4 step 1. Owns per-character
scores, the scale, tier thresholds and their labels, per-character-per-window delta caps,
and eviction.

**The slot stays `relationships`, not the spec's `relationship`.** Same call phase 2 made
for `stats` over `resource` (§7.1): renaming a slot rewrites every authored template and
every save-adjacent doc for no behavioural gain. §8.1's example uses the singular; the
plural is what ships.

**The model stops choosing numbers.** This is the whole point of the port and the thing
that reads as a regression if you only look at the diff. v2 asked for
`relationship_changes: {"Mrs. Abbott": 7}` - a number the model invented, holistically, per
turn. v3 asks what socially *happened* (`{"target": "Mrs. Abbott", "register":
"confided_secret", "reciprocated": true}`) and the template's own price list turns that into
a number. Two things follow that are easy to lose:

  - `registers` is required config, not an engine default. A price list is a creative
    decision about how much a betrayal costs in *this* story, and P-3 puts creative
    decisions in the template. An engine-shipped default table would be the engine quietly
    authoring the story's social physics, which is exactly the class of thing §7.1's
    `STAT_FLOOR = 0` turned out to be.
  - `axis.description` is no longer interpolated into an arithmetic instruction. §3.3: a
    field whose only consumer is an f-string inside a schema instruction is configuration
    for a decision the engine should be making. It survives as a display label.

**Storage stays at `state.characters[name]["relationship"]`**, for the same reason stats
stayed at `protagonist.stats` (§8.3): the engine owns the rules, not the storage, and
relocating it would invalidate every live save to buy nothing. The one piece of genuinely
new state - the delta window behind `cap_per_window` - is created lazily and only when a
story authors the cap, so a v2 save that never had it simply has no cap history, which is
the correct reading of "no cap has been applied yet." That is additive state, not relocated
state, so it does not trip §8.3's deferred schema cutover.
"""
from . import Effect, MechanicEngine, ObservationField, register, register_effect

# Only used when a bound story omits `limit`. Not a global: an unbound story tracks no
# relationship scores at all, so there is nothing for a fallback to apply to.
DEFAULT_LIMIT = 20
DEFAULT_SCALE = (-100, 100)


class ScoredAxis(MechanicEngine):
    slot = "relationships"
    name = "scored_axis"
    # After bounded_counter (20), before inventory: a tier threshold is a fact about this
    # turn's score, and a later engine reading one should not see last turn's.
    resolve_order = 30
    # §5.4. The PLAYER line grows with the roster, which `limit` already bounds - 20
    # characters at "Name +100 (label), " is roughly 600 chars, and the tier footer adds
    # one authored sentence per tier actually reached. 1200 is that with headroom; a story
    # raising `limit` far past the default is what should trip this, and it should.
    prompt_budget = 1200

    # --- configuration -------------------------------------------------------------

    def scale(self, cfg):
        scale = cfg.get("scale") or {}
        return scale.get("min", DEFAULT_SCALE[0]), scale.get("max", DEFAULT_SCALE[1])

    def axis(self, cfg):
        return cfg.get("axis") or {}

    def registers(self, cfg):
        """The authored price list. Required, and validated loudly: a story that declares
        this engine and no registers has bound a mechanic that can never move a score, and
        silent inertness is the failure mode this architecture exists to remove."""
        registers = cfg.get("registers")
        if not registers:
            raise ValueError(
                "mechanics.relationships declares engine 'scored_axis' but authors no "
                "'registers' price list. The model no longer picks relationship numbers; "
                "the story prices its own social vocabulary (§7.2)."
            )
        return registers

    def limit(self, cfg):
        return cfg.get("limit", DEFAULT_LIMIT)

    def tiers(self, cfg):
        """Authored thresholds, sorted so `tier_for` can scan deterministically. A tier
        with a non-negative `at` reads as "score at or above"; a negative one as "score at
        or below" - which is how §8.1's example means `{"at": -25, "label": "closed off"}`
        without needing a direction field."""
        return sorted(cfg.get("tiers") or [], key=lambda t: t["at"])

    def tier_for(self, cfg, score):
        """The tier a score currently sits in, or None. A score cannot satisfy both a
        positive and a negative threshold, so the two scans cannot collide."""
        best = None
        for tier in self.tiers(cfg):
            at = tier["at"]
            if at >= 0 and score >= at:
                if best is None or at >= best["at"]:
                    best = tier
            elif at < 0 and score <= at:
                if best is None or at <= best["at"]:
                    best = tier
        return best

    # --- state ---------------------------------------------------------------------

    def init_state(self, cfg, ctx):
        """Only the delta window is engine-owned state, and only when a cap is authored.
        Returning {} otherwise keeps P-2 structural down to the save file: a story with no
        cap gets no `mechanics.relationships` key at all, not an empty one."""
        return {"window": {}} if cfg.get("cap_per_window") else {}

    @staticmethod
    def scores(ctx):
        return ctx["state"]["characters"]

    @staticmethod
    def _turn(ctx):
        return ctx["state"].get("pacing", {}).get("turn_count", 0)

    def _window_room(self, cfg, ctx, name, turn):
        """How much of the per-window cap this character has left, or None for no cap.

        The window is a rolling `turns`-long span, not a fixed bucket: a cap that resets on
        a boundary lets a story alternate "spend it all, wait for the reset" which is
        exactly the ratcheting the cap exists to stop."""
        cap = cfg.get("cap_per_window")
        if not cap:
            return None
        span = max(1, int(cap.get("turns", 1)))
        spent = sum(
            abs(delta) for recorded_turn, delta
            in (ctx["state"].get("mechanics", {}).get(self.slot, {}).get("window", {}).get(name) or [])
            if recorded_turn > turn - span
        )
        return max(0, int(cap.get("delta", 0)) - spent)

    # --- observation ---------------------------------------------------------------

    def observations(self, cfg, ctx):
        """One field (§5.4): what socially happened, never by how much.

        No cadence (§7.2) - a social beat is only legible about the turn it happened on, so
        this engine may not skip turns even when the roster is empty. A story can be one
        turn away from its first named character."""
        registers = self.registers(cfg)
        axis = self.axis(cfg)
        low, high = self.scale(cfg)
        vocabulary = ", ".join(sorted(registers))
        schema = (
            '  "social": [{"target": "<character name, copied verbatim from EXISTING CHARACTERS '
            'above when they are listed there>", "register": "<exactly one of: '
            f'{vocabulary}>", "reciprocated": <true if the other party met the gesture in kind, '
            'false if it went unanswered or was rebuffed>}]'
        )
        # Deliberately not a JSON dump of the price list: showing the model the numbers
        # invites it to reason about the arithmetic it no longer owns, and a register it
        # chose "because the number looked right" is worse data than one it chose because
        # it described the scene.
        context = (
            f"\nCURRENT STANDING (name: score, {low} {axis.get('negative', 'hostile')} to "
            f"+{high} {axis.get('positive', 'devoted')}, 0 neutral/unknown): "
            f"{self._standing_line(cfg, ctx)}"
        )
        instruction = (
            "For social, report one entry per distinct social beat the NARRATION actually "
            "contains - who it was with and which register best describes it. Do not report "
            "a magnitude: how much each register moves a relationship is fixed by this "
            "story, not by you. Pick the single closest register or omit the beat entirely; "
            "never invent a register outside the list. [] if nothing social happened.\n"
            "If a target is someone already listed in EXISTING CHARACTERS, its value must be "
            "that exact string, copied verbatim - never a shortened, reordered, or "
            "paraphrased version of it (e.g. if EXISTING CHARACTERS lists \"Salome Vence "
            "(the Advocate)\", use that exact string, not \"Salome Vence\" or \"the "
            "advocate\"). This is what keeps the standing attached to that character's "
            "record instead of silently forking into a seemingly-new name. A "
            "generic-label character should still get a social entry as usual, just not a "
            "new_characters one.\n"
        )
        return [ObservationField("social", schema, context, instruction)]

    def _standing_line(self, cfg, ctx):
        scores = self.scores(ctx)
        if not scores:
            return "none yet"
        return ", ".join(
            f"{name} {entry.get('relationship', 0):+d}"
            for name, entry in sorted(scores.items())
        )

    def events(self, cfg, ctx, diff):
        """Read back `social` into §8.2's event stream. Anything malformed is dropped here
        rather than in `resolve`, so the log holds only events that were actually
        well-formed observations - the log is the audit trail, and an entry that no engine
        could ever price is noise in it."""
        registers = self.registers(cfg)
        events = []
        for entry in diff.get("social") or []:
            if not isinstance(entry, dict):
                continue
            target, register = entry.get("target"), entry.get("register")
            if not target or register not in registers:
                continue
            events.append({
                "type": "social",
                "target": target,
                "register": register,
                "reciprocated": bool(entry.get("reciprocated", True)),
            })
        return events

    # --- resolution ----------------------------------------------------------------

    def resolve(self, cfg, ctx, observations, events):
        """Price each social event, clamp to the scale, then evict down to `limit`.

        Ordering inside one turn matters and is not incidental: adjustments land before
        eviction so a character who only just appeared is scored before the roster is
        measured, which is what lets a strong new bond displace a neutral old one rather
        than being dropped on arrival."""
        registers = self.registers(cfg)
        low, high = self.scale(cfg)
        turn = self._turn(ctx)
        scores = self.scores(ctx)
        # Projected, so several beats with the same target in one turn accumulate against
        # the clamp and the cap together instead of each being priced from the same start.
        projected = {name: entry.get("relationship", 0) for name, entry in scores.items()}
        room = {}
        effects = []

        for event in observations or []:
            if event.get("type") != "social":
                continue
            target = event["target"]
            delta = self._price(cfg, registers, event)
            if target not in room:
                room[target] = self._window_room(cfg, ctx, target, turn)
            if room[target] is not None:
                allowed = min(abs(delta), room[target])
                room[target] -= allowed
                delta = allowed if delta >= 0 else -allowed
            current = projected.get(target, 0)
            value = max(low, min(high, current + delta))
            projected[target] = value
            effects.append(Effect(
                "relationships.set",
                reason=f"social:{event['register']}"
                       f"{'' if event.get('reciprocated', True) else ':unreciprocated'}",
                target=target, value=value, delta=value - current, turn=turn,
                windowed=bool(cfg.get("cap_per_window")),
            ))

        effects.extend(self._evictions(cfg, ctx, projected))
        return effects

    def _price(self, cfg, registers, event):
        """The authored price, discounted when a positive gesture went unanswered.

        The discount is opt-in (`unreciprocated_factor`, default 1.0 = no effect) because
        the spec names `reciprocated` as part of the observation without saying what it
        buys, and inventing a mandatory rule here would be the engine authoring story
        physics again. Applied only to positive registers: warmth offered and not returned
        plausibly moves less, but a slight that went unanswered is not a lesser slight."""
        delta = int(registers[event["register"]])
        if delta > 0 and not event.get("reciprocated", True):
            delta = int(delta * float(cfg.get("unreciprocated_factor", 1.0)))
        return delta

    def _evictions(self, cfg, ctx, projected):
        """v2's rule, unchanged: over `limit`, drop whatever sits closest to neutral first.

        Never the strongest bonds and never an authored character - a story's fiercest
        rivalry is exactly the entry that must not silently disappear, however long ago it
        was set, and a character the template wrote down is not the runtime's to forget."""
        limit = self.limit(cfg)
        if len(projected) <= limit:
            return []
        authored = set(ctx["story"]["world"].get("characters", {}).keys())
        removable = sorted(
            (n for n in projected if n not in authored),
            key=lambda n: (abs(projected[n]), n),
        )
        return [Effect("relationships.evict", reason="over_limit", target=name)
                for name in removable[:len(projected) - limit]]

    # --- prompt ---------------------------------------------------------------------

    def prompt_sections(self, cfg, ctx):
        """What the narrator is handed. Empty roster contributes nothing at all rather than
        an empty dict (P-2), which is also why a story's first turns carry no social block."""
        scores = self.scores(ctx)
        if not scores:
            return {}
        sections = {"player_line": f" | Relationships: {self._player_line(cfg, scores)}"}
        footer = self._tier_footer(cfg, scores)
        if footer:
            sections["tiers"] = footer
        return sections

    def _player_line(self, cfg, scores):
        parts = []
        for name, entry in sorted(scores.items()):
            score = entry.get("relationship", 0)
            tier = self.tier_for(cfg, score)
            parts.append(f"{name} {score:+d}" + (f" ({tier['label']})" if tier else ""))
        return ", ".join(parts)

    def _tier_footer(self, cfg, scores):
        """§7.2's actual unlock: a tier that gates behaviour instead of merely labelling a
        number. Only tiers currently reached by someone on the roster, and only those whose
        author wrote guidance - a `label`-only tier already says everything it has to say on
        the PLAYER line, and repeating it here would be prompt spend for nothing."""
        reached = {}
        for name, entry in sorted(scores.items()):
            tier = self.tier_for(cfg, entry.get("relationship", 0))
            if tier and tier.get("narration"):
                reached.setdefault(tier["label"], (tier["narration"], []))[1].append(name)
        if not reached:
            return ""
        lines = "\n".join(
            f"- {', '.join(names)} ({label}): {narration}"
            for label, (narration, names) in reached.items()
        )
        return f"\nSTANDING NOW IN EFFECT:\n{lines}"

    def axis_hint(self, cfg):
        """The scale, as one clause for whoever is already writing a character roster line.
        Not a `prompt_sections` entry: it is punctuation inside someone else's header, and
        a section that cannot stand alone should not pretend to be one."""
        axis = self.axis(cfg)
        low, high = self.scale(cfg)
        return (f"; standing is {low} {axis.get('negative', 'hostile')} "
                f"to +{high} {axis.get('positive', 'devoted')}")


def _apply_set(ctx, effect):
    """Write the priced score, and record the spend against the delta window.

    `setdefault` rather than a lookup: a social beat with a character who has no record yet
    is the normal way a generic-label figure ("the advocate") starts being tracked, and the
    v2 path created them here too."""
    payload = effect.payload
    characters = ctx["state"]["characters"]
    entry = characters.setdefault(
        payload["target"], {"relationship": 0, "first_seen_turn": payload["turn"]})
    entry["relationship"] = payload["value"]
    # A social beat this turn means the narration actually put them on the page, which is
    # what flips them off generate_pacing_nudge's "CHARACTERS TO WEAVE IN" line.
    entry["introduced"] = True
    if payload["windowed"] and payload["delta"]:
        window = (ctx["state"].setdefault("mechanics", {})
                  .setdefault(ScoredAxis.slot, {}).setdefault("window", {}))
        history = window.setdefault(payload["target"], [])
        history.append([payload["turn"], payload["delta"]])
        # Bounded by construction: only the current window can ever be consulted, so
        # anything older is dead weight that would otherwise grow for the whole game.
        window[payload["target"]] = history[-32:]


def _apply_evict(ctx, effect):
    ctx["state"]["characters"].pop(effect.payload["target"], None)
    window = ctx["state"].get("mechanics", {}).get(ScoredAxis.slot, {}).get("window")
    if window:
        window.pop(effect.payload["target"], None)


ENGINE = register(ScoredAxis())
register_effect("relationships.set", _apply_set)
register_effect("relationships.evict", _apply_evict)

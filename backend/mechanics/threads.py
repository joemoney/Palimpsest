"""`subplots` / `weighted_threads` - engine v2 phase 4, port 5.

docs/ENGINE_V2_SPEC.md §2.1 (the paragraph that ends "that makes subplot progress ordinary
registry work"), docs/analysis_and_plans/ENGINE_V2/ENGINE_V2_PHASES.md phase 4 step 5.

**This is the port §2.1 spent a page refusing to make an exception for.** Act advancement
keeps its verdict with the model, on E-7's test: nobody was shown a number and the engine has
no better basis for "has this act resolved" than the model does. Subplot progress resolves
the other way and the reasoning is worth keeping to hand, because the two look alike:
`subplot_progress` asked the model for an integer 0-100 that the engine then added to a
running total and compared against a threshold. That is arithmetic performed by the model -
the same shape as `stat_changes` and `relationship_changes`, and exactly what E-3 forbids.

So the model classifies how materially a beat moved a thread, and the engine prices the
classification:

    touched   - the thread was present, but nothing about it actually changed
    advanced  - real, concrete progress
    decisive  - a beat that substantially settles it
    resolved  - this beat concluded the thread

**`resolved` is priced structurally, not from the table.** It completes the thread, whatever
its threshold and wherever it had got to. That is the one classification the model cannot
express as a number without doing the engine's arithmetic for it, and it is the thing CR-08
added the progress readout for in the first place: "this beat should finish the thread" is a
reading of the scene, while "so that's 40 more points" is not.

**Weights are absolute, and that is what makes `span: multi_act` work.** A `multi_act`
subplot's `completion_threshold` is 250 rather than 100 (see `story_engine.insert_subplot`),
and a decisive beat is worth the same in both - so the longer thread genuinely takes more
beats, which is the entire meaning of the span. Scaling the weights to the threshold would
have quietly undone the one lever that distinguishes them.

**The model is no longer shown `[progress/threshold]`.** CR-08 added those numbers so the
model could tell a nudge from a finishing blow, which is a real need - but showing a running
total to a model that is no longer allowed to do arithmetic is an invitation to start again
("it's at 90 of 100, so I'll say 10"). It sees a band instead: *just begun*, *under way*,
*close to resolution*. Same information, none of the arithmetic.

**Declare-to-bind exposed another unconditional field.** `subplot_progress` was asked of
every story every turn, including `courtroom`, which authors no subplots at all and is a
deliberately single-thread story. Same P-2 violation `items_gained`/`items_lost` turned out
to be, and it goes the same way.

**What deliberately did NOT move.** `check_subplot_status` still lives in `story_engine`:
completion detection is called from the turn loop *and* from `subplot_manager`'s manual
path, and it drives act advancement, subplot regeneration and `completed_subplots`. §2.1
scopes this port to the progress arithmetic, and moving the rest would pull act advancement
- the declared exception - toward a registry it is specifically not in.
"""
from . import Effect, MechanicEngine, ObservationField, register, register_effect


def subplot_view(ctx: dict, sid: str) -> dict:
    """Merged view of one subplot: a seeded subplot resolves title/description/priority/
    ties_to_main_plot/completion_threshold/span from the template; a generated one (no
    template counterpart) carries all of that on its own runtime entry instead, since
    there's nothing to resolve it against. If a seeded subplot's template entry has since
    been removed by an author (SCHEMA_V2_SPEC.md §2.3 reconciliation), falls back to a
    placeholder rather than raising - the runtime copy stays in place either way.

    A module function rather than a method, and `story_engine._subplot_view` delegates to
    it. Two reasons it cannot be an engine method: `check_subplot_status`,
    `generate_new_subplot`, act advancement and `subplot_manager` all need the view, and
    none of them may stop working because a story has not declared this engine. And the
    alternative - the engine recomputing the merge privately - is a second copy of a rule
    that only ever had one, which is how the two drift apart.

    It reads only `ctx`, so `resolve()` stays pure with it."""
    seed = ctx["story"]["plot"]["subplots"].get(sid, {})
    runtime = ctx["state"]["plot"]["subplots"].get(sid, {})
    return {
        "id": sid,
        "title": runtime.get("title") or seed.get("title") or f"(removed from template: {sid})",
        "description": runtime.get("description", seed.get("description", "")),
        "priority": runtime.get("priority", seed.get("priority", "medium")),
        "ties_to_main_plot": runtime.get("ties_to_main_plot", seed.get("ties_to_main_plot", "")),
        "completion_threshold": runtime.get("completion_threshold", seed.get("completion_threshold", 100)),
        "span": runtime.get("span", seed.get("span", "single_act")),
        "progress": runtime.get("progress", 0),
        "status": runtime.get("status", "not_started"),
        "active": runtime.get("active", False),
    }


def all_subplots(ctx: dict) -> dict:
    """{id: merged view} for every subplot that currently exists - ctx["state"]["plot"]
    ["subplots"] is the authoritative id set (every template-seeded subplot is instantiated
    into it at save creation, and every generated one is added to it directly), so iterating
    its keys covers both kinds."""
    return {sid: subplot_view(ctx, sid) for sid in ctx["state"]["plot"]["subplots"]}


# Priced against the default 100-point threshold: roughly five `advanced` beats or two
# `decisive` ones to close a single-act thread, which is the pace v2's free-integer answers
# actually produced. A story wanting a different rhythm authors `weights`.
DEFAULT_WEIGHTS = {"touched": 5, "advanced": 20, "decisive": 45}
# Not in DEFAULT_WEIGHTS because it has no fixed value - see the module docstring.
RESOLVED = "resolved"
# CR-07: the thread concluded but the scene never showed its payoff. Offered to the model only in
# a story that authors rewards, so a story without them keeps the plain vocabulary (P-2).
RESOLVED_UNSHOWN = "resolved_unshown"


class WeightedThreads(MechanicEngine):
    slot = "subplots"
    name = "weighted_threads"
    # Before failure (90), after everything that might colour a beat. Nothing else reads
    # subplot progress during resolve, so this only has to be somewhere stable.
    resolve_order = 60
    # §5.4. This engine contributes no narration section: the narrator already gets its
    # threads through the pacing/act machinery, which is not the registry's.
    # CR-07's pre-arm line and owed-payoff directive; a story authoring neither contributes none.
    prompt_budget = 800

    # --- configuration -------------------------------------------------------------

    def weights(self, cfg):
        """The authored ladder, or the default one. Unlike `scored_axis`'s registers this
        does ship a default, and the difference is real: a register table is a story's
        social physics (P-3 creative), while "how much of a thread does a decisive beat
        settle" is structural pacing that reads the same in every genre. A story that
        disagrees overrides it; none has to invent one to get a working mechanic."""
        return {**DEFAULT_WEIGHTS, **(cfg.get("weights") or {})}

    @staticmethod
    def reward_keys(cfg, ctx, sid):
        """The stat events thread `sid` pays on completion (CR-07). A thread's own
        `on_complete.stat_events` wins when it names any (the board reads a blank list as
        "inherit", so the engine does too); otherwise its priority's row. A generated thread has no template entry, so it can only
        inherit."""
        seed = ctx["story"]["plot"]["subplots"].get(sid) or {}
        own = seed.get("on_complete")
        if isinstance(own, dict) and own.get("stat_events"):
            return list(own["stat_events"])
        priority = subplot_view(ctx, sid)["priority"]
        return list((cfg.get("completion_rewards") or {}).get(priority) or [])

    def _pays_anything(self, cfg, ctx):
        return bool(cfg.get("completion_rewards")) or any(
            isinstance(sp, dict) and isinstance(sp.get("on_complete"), dict) and sp["on_complete"].get("stat_events")
            for sp in ctx["story"]["plot"]["subplots"].values())

    @staticmethod
    def _views(ctx):
        """The merged seed+runtime view of every subplot. Reads only ctx, so `resolve()`
        stays pure."""
        return all_subplots(ctx)

    # --- observation ---------------------------------------------------------------

    def observations(self, cfg, ctx):
        """One field, or none at all when no thread is active - which is both the cadence
        and the P-2 fix: a single-thread story is never asked about subplots it does not
        have."""
        active = {sid: view for sid, view in self._views(ctx).items() if view.get("active")}
        if not active:
            return None
        # Escalating order, not alphabetical: the list the model reads should be a ladder,
        # and it has to match the order the instruction below explains them in. Sorting by
        # weight keeps it deterministic (phase 1's gate) without hardcoding the vocabulary,
        # so an authored `weights` block reorders it correctly for free.
        weights = self.weights(cfg)
        ladder = sorted(weights, key=lambda k: (weights[k], k)) + [RESOLVED]
        if self._pays_anything(cfg, ctx):
            ladder.append(RESOLVED_UNSHOWN)
        vocabulary = ", ".join(ladder)
        schema = (
            '  "subplot_beats": {"<subplot_id from ACTIVE SUBPLOTS above>": "<exactly one of: '
            f'{vocabulary}>"}}'
        )
        lines = "\n".join(
            f"  {sid}: {view['title']} - {view['description']} ({self._band(view)})"
            for sid, view in active.items()
        )
        context = f"\nACTIVE SUBPLOTS (id: title - description, how far along):\n{lines}"
        instruction = (
            "For subplot_beats, include only threads this turn actually moved: touched if it "
            "was present but unchanged, advanced for real progress, decisive for a beat that "
            "substantially settles it, resolved only if this beat concluded it"
            + (" and the scene showed what the thread paid back, resolved_unshown if it concluded "
               "but the scene did not show that payoff" if self._pays_anything(cfg, ctx) else "")
            + ". Never report a number - what each is worth is fixed by the engine. {} if none moved.\n"
        )
        return [ObservationField("subplot_beats", schema, context, instruction)]

    @staticmethod
    def _band(view):
        """Where a thread stands, qualitatively. Replaces CR-08's `[progress/threshold]` -
        same information, without handing a running total to a model that must not add."""
        threshold = view.get("completion_threshold") or 100
        ratio = (view.get("progress") or 0) / threshold
        if ratio < 0.25:
            return "just begun"
        return "under way" if ratio < 0.75 else "close to resolution"

    def events(self, cfg, ctx, diff):
        """Classifications outside the vocabulary are dropped, and so is a bare number.

        Dropping an integer rather than honouring it is the point of the port: accepting one
        would quietly restore the model's arithmetic for any turn it slipped back into the
        old habit, and a mechanic that is only sometimes enforced is not enforced."""
        vocabulary = set(self.weights(cfg)) | {RESOLVED}
        if self._pays_anything(cfg, ctx):
            vocabulary.add(RESOLVED_UNSHOWN)
        active = {sid for sid, view in self._views(ctx).items() if view.get("active")}
        events = []
        for subplot_id, beat in (diff.get("subplot_beats") or {}).items():
            if subplot_id in active and isinstance(beat, str) and beat in vocabulary:
                events.append({"type": "subplot_beat", "id": subplot_id, "beat": beat})
        return events

    # --- resolution ----------------------------------------------------------------

    def resolve(self, cfg, ctx, observations, events):
        """Price each beat and emit the resulting absolute progress.

        Absolute rather than a delta, for the reason every other engine here does the same:
        an effect that says "set it to 60" replays identically, while one that says "add 20"
        depends on what has already been applied this turn."""
        weights = self.weights(cfg)
        views = self._views(ctx)
        projected = {sid: view.get("progress") or 0 for sid, view in views.items()}
        effects = []
        for event in observations or []:
            if event.get("type") != "subplot_beat":
                continue
            subplot_id, beat = event["id"], event["beat"]
            view = views.get(subplot_id)
            if view is None:
                continue
            threshold = view.get("completion_threshold") or 100
            if beat in (RESOLVED, RESOLVED_UNSHOWN):
                value = threshold
            else:
                value = min(threshold, projected[subplot_id] + int(weights[beat]))
            value = max(0, value)
            projected[subplot_id] = value
            effects.append(Effect("subplots.progress", reason=f"beat:{beat}",
                                  id=subplot_id, value=value, unshown=beat == RESOLVED_UNSHOWN))
        return effects

    # --- CR-07: rewards -------------------------------------------------------------

    def settle(self, cfg, ctx):
        """Pay each finished thread's reward, once. Decided after the beats have applied and
        before the stat readout renders (so the readout shows the payment), by code rather
        than by asking the extraction pass to remember (P-7). `reward_paid` on the runtime
        record is what makes a second call, from `check_subplot_status` or a manual closure,
        a no-op; a regenerate restores the whole pre-turn snapshot, so the flag goes with it."""
        if not self._pays_anything(cfg, ctx):
            return []
        from . import bound_for
        runtime = ctx["state"]["plot"]["subplots"]
        turn = ctx["state"].get("pacing", {}).get("turn_count", 0)
        due, keys = [], []
        for sid, view in self._views(ctx).items():
            record = runtime[sid]
            if record.get("reward_paid") or view["status"] == "failed":
                continue
            done = view["status"] == "completed" or (
                view["active"] and view["progress"] >= view["completion_threshold"])
            if done:
                due.append(sid)
                keys.extend(self.reward_keys(cfg, ctx, sid))
        if not due:
            return []
        effects = []
        stats = bound_for(ctx["story"], "stats")
        if keys and stats is not None:
            effects.extend(stats.engine.pay(stats.cfg, ctx, keys, "thread_reward"))
        effects.append(Effect("subplots.reward_paid", reason="reward", ids=due, turn=turn,
                              owed=[sid for sid in due if ctx["state"]["plot"]["subplots"][sid].get("unshown")]))
        return effects

    def prompt_sections(self, cfg, ctx):
        sections = {}
        margin = cfg.get("near_completion_margin")
        turn = ctx["state"].get("pacing", {}).get("turn_count", 0)
        if margin:
            lines = []
            for sid, view in sorted(self._views(ctx).items()):
                gap = view["completion_threshold"] - view["progress"]
                if view["active"] and 0 < gap <= margin:
                    pays = bool(self.reward_keys(cfg, ctx, sid))
                    lines.append(f"- '{view['title']}' may resolve this scene" + (
                        "; if it does, let what it pays back be seen on the page in that same scene." if pays else "."))
            if lines:
                sections["arm"] = ("\nTHREADS NEAR THEIR END:\n" + "\n".join(lines))[:780]
        owed = [sid for sid, rec in ctx["state"]["plot"]["subplots"].items()
                if rec.get("owed_turn") == turn]
        if owed:
            titles = ", ".join(f"'{subplot_view(ctx, sid)['title']}'" for sid in sorted(owed))
            sections["directive"] = (f"\nA PAYOFF IS OWED. {titles} concluded last scene but what it "
                                     f"pays back never reached the page. Show it now, this scene.")[:780]
        return sections


def _apply_progress(ctx, effect):
    subplot = ctx["state"]["plot"]["subplots"].get(effect.payload["id"])
    if subplot is not None:
        subplot["progress"] = effect.payload["value"]
        if effect.payload.get("unshown"):
            subplot["unshown"] = True


def _apply_reward_paid(ctx, effect):
    for sid in effect.payload["ids"]:
        record = ctx["state"]["plot"]["subplots"][sid]
        record["reward_paid"] = True
        if sid in effect.payload["owed"]:
            # Shown to the narration that follows this turn: it reads off the turn it was recorded on.
            record["owed_turn"] = effect.payload["turn"]


ENGINE = register(WeightedThreads())
register_effect("subplots.progress", _apply_progress)
register_effect("subplots.reward_paid", _apply_reward_paid)

"""The mechanic-engine registry - engine v2 phase 1.

See docs/ENGINE_V2_SPEC.md (§3 the contract, §6 effects and resolution, §8.2 the event
log) and docs/analysis_and_plans/ENGINE_V2/ENGINE_V2_PHASES.md phase 1. Phase 1 builds the architecture and ports
nothing, so on every story that ships today this module resolves to zero bound engines and
the whole pipeline is a no-op. That is the phase gate, not an accident: assembled prompts
must stay byte-identical until phase 2 ports `resource`.

The one rule that makes this safe to land early:

    A mechanics entry is registry-managed only if it carries an explicit "engine" key.

Every `mechanics` block in every current template predates the registry and has no such
key, so none of them bind, and `story_engine`'s existing `.get("mechanics", ...)` paths
keep owning them exactly as before. Porting a mechanic means adding `"engine": "<name>"` to
the template and deleting the old path in the same change - never one without the other.

What is deliberately NOT here yet, because nothing would use it:
  - engines. The first one lands in phase 2 as backend/mechanics/resource.py.
  - effect handlers. `register_effect` exists so phase 2 has somewhere to plug in;
    `apply_effects` raises on a kind nobody registered rather than dropping it silently.
"""


class UnknownEngineError(ValueError):
    """A template names an engine this build does not have.

    Raised at load (§3.2), not at first use. A story naming a mechanic the runtime cannot
    provide is broken content, and the player should find that out from the story list
    rather than forty turns in, when the rule silently fails to apply."""


class Effect:
    """A state change an engine wants made, as data rather than a mutation (§6.1).

    `resolve()` returns these instead of touching ctx, which keeps it pure and therefore
    unit-testable with no stubs, makes conflicting effects detectable instead of
    order-dependent by accident, and keeps replay honest.

    `reason` is not decoration. It is what makes a save auditable afterwards - "why is my
    fuel 12" has to have an answer that is not "the model said so"."""

    __slots__ = ("kind", "reason", "payload")

    def __init__(self, kind: str, reason: str = "", **payload):
        self.kind = kind
        self.reason = reason
        self.payload = payload

    def __eq__(self, other):
        return (isinstance(other, Effect) and self.kind == other.kind
                and self.reason == other.reason and self.payload == other.payload)

    def __hash__(self):
        return hash((self.kind, self.reason, tuple(sorted(self.payload.items()))))

    def __repr__(self):
        args = "".join(f", {k}={v!r}" for k, v in sorted(self.payload.items()))
        return f"Effect({self.kind!r}, reason={self.reason!r}{args})"


class ObservationField:
    """One typed question an engine puts to the observation pass (§3.1, §5.4).

    Phase 2 let `bounded_counter` reach the observation prompt through a bespoke
    `schema_field()` that `story_engine` called by name. That does not scale past one
    engine, and phase 4 ports four more, so the contract becomes real here: an engine
    returns these and never knows where in the prompt they land.

    Three parts because the v2 prompt already has three places a mechanic speaks:
      `schema`      - the line inside the JSON shape the model must answer in.
      `context`     - a line of current state above it ("CURRENT RELATIONSHIPS: ...").
      `instruction` - a paragraph of how to answer, below the shape.

    `name` is the JSON key, and it is what §5.4's field budget counts. One per engine per
    turn: an engine needing two is two engines, or one field with a richer type."""

    __slots__ = ("name", "schema", "context", "instruction")

    def __init__(self, name: str, schema: str, context: str = "", instruction: str = ""):
        self.name, self.schema = name, schema
        self.context, self.instruction = context, instruction

    def __repr__(self):
        return f"ObservationField({self.name!r})"


class MechanicEngine:
    """Base contract (§3.1). Every method except `resolve` has a do-nothing default, so a
    stateless engine that only prices events implements exactly one of them."""

    slot = ""            # the mechanics.<slot> key this serves
    name = ""            # the value of mechanics.<slot>.engine
    resolve_order = 100  # §6.2 - lower resolves first; ties broken by slot name
    prompt_budget = 0    # §5.4 - max chars prompt_sections may contribute, 0 = none

    def init_state(self, cfg, ctx) -> dict:
        """Runtime state this engine owns, at save creation. {} for a stateless engine."""
        return {}

    def observations(self, cfg, ctx):
        """Typed questions for the observation pass, or None to ask nothing this turn.
        None is the normal case for a gated engine (§5.2), not an error."""
        return None

    def events(self, cfg, ctx, diff) -> list:
        """This engine's own observation field, as it came back from the model, turned into
        the typed events of §8.2. Only the engine that asked the question knows how to read
        the answer, so the translation lives here rather than in a central parser.

        Returns raw event dicts, not Effects: an event is what was observed, an Effect is
        what an engine decided to do about it, and the log records the first so the second
        stays replayable (§8.2)."""
        return []

    def prompt_sections(self, cfg, ctx) -> dict:
        """Named fragments for the narration prompt. An omitted key is an omitted section,
        never an empty header (P-2)."""
        return {}

    def resolve(self, cfg, ctx, observations, events) -> list:
        """Pure: no I/O, no LLM (except a declared §6.4 judgement), deterministic."""
        raise NotImplementedError(f"{type(self).__name__} must implement resolve()")

    def render(self, cfg, ctx, text: str) -> str:
        """Deterministic post-narration substitution - the apply_stat_readouts slot."""
        return text


class BoundEngine:
    """One engine plus the template config it was bound to, resolved once per load."""

    __slots__ = ("engine", "cfg", "slot")

    def __init__(self, engine, cfg, slot):
        self.engine, self.cfg, self.slot = engine, cfg, slot

    def __repr__(self):
        return f"BoundEngine({self.slot}={self.engine.name})"


# A per-turn scratch namespace on `ctx`, cleared at the top of every run_turn_pipeline.
# It sits on ctx rather than ctx["state"] precisely because only ctx["state"] is written to
# disk - an engine that needs to hand something to its own render() pass this turn can put
# it here without adding a save field or a migration. Effects stay absolute (§6.1); this is
# how an applier reports what a move actually came to *after* clamping, which no one can
# re-derive later from state alone.
TURN_SCRATCH = "_turn"

# (slot, name) -> engine instance. Keyed by both so two engines may serve one slot - a
# story picks between them with mechanics.<slot>.engine.
_REGISTRY = {}
_EFFECT_HANDLERS = {}


def register(engine: MechanicEngine):
    """Register an engine instance. Idempotent for the same object so that a module
    re-imported under a different name (which the test stubs can do) does not trip the
    duplicate check."""
    if not engine.slot or not engine.name:
        raise ValueError(f"{type(engine).__name__} must set both slot and name")
    key = (engine.slot, engine.name)
    existing = _REGISTRY.get(key)
    if existing is not None and existing is not engine:
        raise ValueError(f"an engine is already registered for {key}")
    _REGISTRY[key] = engine
    return engine


def register_effect(kind: str, handler):
    """Register the applier for one effect kind. Phase 1 registers none."""
    if kind in _EFFECT_HANDLERS and _EFFECT_HANDLERS[kind] is not handler:
        raise ValueError(f"an effect handler is already registered for {kind!r}")
    _EFFECT_HANDLERS[kind] = handler
    return handler


def registered_engines() -> dict:
    """Copy of the registry, for tests and diagnostics."""
    return dict(_REGISTRY)


def _declared(story) -> list:
    """(slot, cfg) for every mechanics entry that opts into the registry - i.e. carries an
    explicit "engine" key. A falsy cfg is skipped: P-2 says an absent module does not
    exist, and `"stats": {}` is the same statement as omitting it."""
    mechanics = story.get("mechanics", {}) or {}
    out = []
    for slot, cfg in mechanics.items():
        if cfg and isinstance(cfg, dict) and cfg.get("engine"):
            out.append((slot, cfg))
    return out


def validate(story):
    """Fail loudly at load on a template naming an engine this build does not have (§3.2).

    Deliberately silent about a mechanics entry with no "engine" key - that is not an
    error, it is a mechanic the registry does not own yet."""
    # Declare-to-bind created one new way to author a story wrongly: seed protagonist.stats
    # (or a character_creation starting_stats) but never declare the engine, and the stats
    # sit in state doing nothing - no bounds, no prompt line, no stat_changes field. Silent
    # inertness is exactly the failure this architecture exists to remove, so say so. A
    # warning rather than a raise: it is an authoring smell, not broken content, and a
    # story may legitimately carry a vestigial stat block.
    seeded = story.get("protagonist", {}).get("stats") or any(
        option.get("starting_stats")
        for step in story.get("character_creation", []) or []
        for option in step.get("options", []) or []
    )
    declared = {slot for slot, _ in _declared(story)}
    if seeded and "stats" not in declared:
        print("WARNING: this story seeds protagonist stats but declares no "
              "mechanics.stats.engine - they will be inert (no bounds, no prompt line, "
              "no stat_changes field). Add \"engine\": \"bounded_counter\" to use them.")
    # Phase 4 gave inventory the same failure mode declare-to-bind created for stats: seed
    # starting_inventory, never declare the engine, and the items sit in the save with no
    # prompt line and no way to gain or lose one.
    if story.get("protagonist", {}).get("starting_inventory") and "inventory" not in declared:
        print("WARNING: this story seeds protagonist.starting_inventory but declares no "
              "mechanics.inventory.engine - the items will be inert (no prompt line, no way "
              "to gain or spend one). Add \"engine\": \"tagged_items\" to use them.")
    # Same shape again for subplots. This one is the most damaging of the three to get
    # wrong: the threads still exist, still reach the narration prompt through the act and
    # pacing machinery, and simply never progress - so the story looks fine and quietly
    # never resolves a thread.
    if story.get("plot", {}).get("subplots") and "subplots" not in declared:
        print("WARNING: this story authors plot.subplots but declares no "
              "mechanics.subplots.engine - the threads will never progress (no subplot_beats "
              "field, so nothing ever completes). Add \"engine\": \"weighted_threads\".")

    # §2.2: an act's `requires` must latch, because a completion condition that can
    # un-satisfy itself is worse than no condition at all. `stat` and `item_tag` are perfectly
    # good on a door and a trap on an act - the act would advance, then un-advance when the
    # player spends the item or the stat drifts back. A warning rather than a raise: it is an
    # authoring smell, and there may be a story where the axis genuinely only moves one way.
    for act in story.get("plot", {}).get("main_thread", {}).get("acts", []) or []:
        loose = gate.non_latching_referents(act.get("requires"))
        if loose:
            print(f"WARNING: act {act.get('act_number')} requires {', '.join(loose)}, which "
                  f"§2.2 marks non-latching - the act can un-satisfy its own precondition "
                  f"after advancing. Prefer revelation or flag.")

    # D1 lets the board write a CR field before the engine that reads it exists; this is the
    # "names every CR field present with no reader" warning for CR-01's tier hooks, so an
    # author playing a half-built story is told why a tier crossing does nothing. Delete it in
    # the change that gives on_enter a reader (AUTHORING_TOOL_PHASES.md S5 step 2).
    stat_axes = ((story.get("mechanics") or {}).get("stats") or {}).get("axes") or {}
    hooked = sorted(axis for axis, spec in stat_axes.items()
                    if any(isinstance(t, dict) and t.get("on_enter") for t in (spec or {}).get("tiers") or []))
    if hooked:
        print(f"WARNING: tiers on {', '.join(hooked)} author on_enter (CR-01), which this build "
              f"does not read yet - crossing into those tiers fires nothing.")

    for slot, cfg in _declared(story):
        if (slot, cfg["engine"]) not in _REGISTRY:
            known = sorted(n for s, n in _REGISTRY if s == slot)
            raise UnknownEngineError(
                f"mechanics.{slot}.engine is {cfg['engine']!r}, which this build does not "
                f"have. Known engines for {slot!r}: {known or 'none'}"
            )


def bind(story) -> list:
    """Bound engines for a story, in resolve order (§6.2). Empty for every template that
    ships today, which is what makes phase 1 a no-op."""
    bound = [BoundEngine(_REGISTRY[(slot, cfg["engine"])], cfg, slot)
             for slot, cfg in _declared(story)]
    bound.sort(key=lambda b: (b.engine.resolve_order, b.slot))
    return bound


def observation_fields(ctx) -> list:
    """Every bound engine's observation field for this turn, in resolve order, skipping
    engines that return None because they have nothing to ask (§5.2).

    Order is `bind`'s, i.e. `resolve_order` then slot name, so the assembled observation
    prompt is deterministic for a given story - the same property `_existing_character_names`
    had to be fixed for at phase 1's gate, and for the same reason: a prompt whose bytes
    vary run to run defeats caching and cannot be regression-tested."""
    fields = []
    for b in bind(ctx["story"]):
        field = b.engine.observations(b.cfg, ctx)
        if field:
            fields.extend(field if isinstance(field, list) else [field])
    return fields


def observation_field_count(ctx) -> int:
    """§5.4's budget, measured rather than asserted. Counts only what engines contribute;
    the three core fields (`flags_set`, `scene_update`, `new_characters`) belong to no
    engine and porting removes none of them, which is why the budget is stated as
    `core + 7` rather than as a flat total."""
    return len(observation_fields(ctx))


def events_from_diff(ctx, diff) -> list:
    """The turn's typed event stream (§8.2), assembled from each bound engine reading back
    its own observation field. An engine that asked nothing this turn contributes nothing."""
    events = []
    for b in bind(ctx["story"]):
        events.extend(b.engine.events(b.cfg, ctx, diff) or [])
    return events


def prompt_sections(ctx) -> dict:
    """Merged narration-prompt fragments from every bound engine, checked against each
    engine's declared budget (§5.4)."""
    sections = {}
    for b in bind(ctx["story"]):
        for key, text in (b.engine.prompt_sections(b.cfg, ctx) or {}).items():
            if not text:
                continue  # P-2: an omitted section, never an empty header
            # §5.4 is structural rather than advisory: an engine that contributes prompt
            # text must declare what it is allowed to spend. Leaving prompt_budget at 0
            # and calling it "unlimited" is how a bounded prompt stops being bounded.
            if not b.engine.prompt_budget:
                raise ValueError(
                    f"{b.slot}:{key} contributes prompt text but {type(b.engine).__name__} "
                    f"declares no prompt_budget (§5.4)"
                )
            if len(text) > b.engine.prompt_budget:
                raise ValueError(
                    f"{b.slot}:{key} is {len(text)} chars, over its "
                    f"{b.engine.prompt_budget}-char budget"
                )
            sections[f"{b.slot}.{key}"] = text
    return sections


def record_events(state: dict, events: list):
    """Append raw observations to the save's event log (§8.2).

    setdefault rather than a migration, like every other lazily-added field in this
    project. The log is disk-only: engines read *state*, never history, so this never
    reaches a prompt and its unbounded growth costs nothing."""
    if not events:
        return
    log = state.setdefault("events", [])
    turn = state.get("pacing", {}).get("turn_count")
    for event in events:
        log.append({"turn": turn, **event} if turn is not None else dict(event))


def resolve_all(ctx, observations=None) -> list:
    """Run every bound engine's resolve() in order and return the effects, unapplied.

    Each engine sees the same observations and the save's event log. No engine reads
    another's state or another's observations (E-3), which is what makes the observation
    pass safe to shard concurrently later."""
    observations = observations or []
    events = ctx["state"].get("events", [])
    effects = []
    for b in bind(ctx["story"]):
        effects.extend(b.engine.resolve(b.cfg, ctx, observations, events) or [])
    return effects


def apply_effects(ctx, effects: list):
    """Apply effects in the order their engines produced them (§6.2).

    An unregistered kind raises rather than being skipped: an effect nobody applies is a
    state change the player was promised and did not get, which is exactly the failure
    mode this architecture exists to remove."""
    for effect in effects or []:
        handler = _EFFECT_HANDLERS.get(effect.kind)
        if handler is None:
            raise ValueError(f"no handler registered for effect kind {effect.kind!r}")
        handler(ctx, effect)


def all_acts(ctx) -> list:
    """Every act - the template's authored acts (annotated with their runtime completed/
    optional flags from act_completion, since the template entry itself is frozen) plus
    every act generated during play - sorted by act_number.

    Here rather than in `story_engine` because `beat_counter` resolves a rule's effective
    threshold against the current act (§13) and an engine may not import the module that
    imports it. `story_engine._all_acts` delegates here, so there is one implementation
    rather than a copy that drifts.

    This is engines *reading* act state, which §7.9 does not forbid - what stays outside the
    registry is act advancement (`check_and_advance_act`), i.e. owning the verdict on when an
    act ends. Reading which act is current is not owning that."""
    completion = ctx["state"]["plot"]["act_completion"]
    merged = []
    for act in ctx["story"]["plot"]["main_thread"]["acts"]:
        overlay = completion.get(str(act["act_number"]), {})
        entry = {
            "act_number": act["act_number"],
            "title": act["title"],
            "description": act["description"],
            "completion_signals": list(act.get("completion_signals", [])),
            "completed": overlay.get("completed", False),
            "optional": overlay.get("optional", False),
        }
        # §2.2's authored precondition, carried through only when the act has one. This list
        # is a fixed projection rather than a copy of the act, so a field not named here is
        # silently dropped - which is what happened to `requires` until phase 6's act half
        # went looking for it. Conditional rather than defaulted to {} because P-2 says an
        # absent optional is absent, and `_requires_unmet` reads "no key" as "no floor".
        if act.get("requires"):
            entry["requires"] = act["requires"]
        merged.append(entry)
    for act in ctx["state"]["plot"]["generated_acts"]:
        merged.append(dict(act))
    merged.sort(key=lambda a: a["act_number"])
    return merged


def current_act(ctx):
    """The main thread's currently-active act, looked up by act_number (CR-17) across the
    merged act list rather than by list position - list position breaks as soon as act
    numbering stops being contiguous. None if `current_act` matches no act."""
    number = ctx["state"]["plot"]["current_act"]
    return next((act for act in all_acts(ctx) if act["act_number"] == number), None)


def bound_for(story, slot: str):
    """The single bound engine serving `slot`, or None. Most call sites want one specific
    mechanic rather than the whole list."""
    for b in bind(story):
        if b.slot == slot:
            return b
    return None


def render_all(ctx, text: str) -> str:
    """Each bound engine's deterministic post-narration substitution, in resolve order."""
    for b in bind(ctx["story"]):
        text = b.engine.render(b.cfg, ctx, text)
    return text


def run_turn_pipeline(ctx, observations=None):
    """The whole engine-side half of a turn (§4): record what was observed, resolve it into
    effects, apply them. Called unconditionally from story_engine.update_state_after_turn;
    with no bound engines it records nothing, resolves nothing and applies nothing."""
    ctx[TURN_SCRATCH] = {}
    record_events(ctx["state"], observations)
    apply_effects(ctx, resolve_all(ctx, observations))


def run_observation_pipeline(ctx, diff):
    """`run_turn_pipeline` starting one step earlier, from the raw observation-pass diff:
    each engine reads back its own field, the resulting events are logged, and every engine
    resolves against the whole stream.

    Why every engine sees every event rather than only its own: pricing is cross-cutting by
    design (§7.1 - the resource engine prices a `travel` event the movement vocabulary
    produced). E-3's independence claim is about the *questions* being answerable
    separately, which is what makes sharding safe, not about an engine being blind to what
    the others asked."""
    events = events_from_diff(ctx, diff)
    run_turn_pipeline(ctx, events)
    return events


# Engines register by being imported. At the bottom, because each one imports names from
# this module - the package is the contract, the modules are the implementations.
from . import failure, gate, items, ledger, pacing, resource, reveal, social, threads  # noqa: E402,F401

"""The authoring tool's Preview tab (AUTHORING_TOOL_PHASES.md S4, Authoring_Tool_Spec §4.5).

"What would the model actually be sent?" - answered by the real prompt builders, never a
parallel description of them (D3). A **sample state** (the same shape `author_evaluate` takes) is
laid over a freshly seeded save: the story goes through `playable_projection` (an engine this
build lacks is dropped and *named*, not approximated), `new_save_state` seeds the state the way a
real save is seeded, and the sample overwrites the parts the author set. Then:

- the narrator prompt is built section by section from `story_engine.SECTIONS`, so the listing
  is the prompt split at the seams the engine already has, and `matches` says the sections
  rejoin into exactly `build_system_prompt(ctx)` (the S4 gate: not a byte differs);
- the state-update prompt is *captured*, not rebuilt: `update_progress_from_turn` assembles it
  inline beside its LLM call, so the one way to show the true text is to run it with the call
  replaced by a recorder that hands back an empty diff. Nothing leaves the process.

Both work on a deep copy of the state, because a section may mutate (the pacing nudge resets
`turns_since_nudge`). Preview is static: no LLM call, no disk, no effect on any save.

Not shown: the act-generator prompt. An act advances on a condition and only then asks a model,
so there is no single prompt to render for a sample state; the CR-06 lore; and any engine this
build lacks, which `left_out` names.
"""
import copy
import threading

import author_evaluate
import author_model
import derived
import engine_trace
import mechanics
import state_store
import story_engine

# `story_engine.call_llm_json` is a module global, so capturing the state-update prompt means
# swapping it for the duration of one call. Serialised: two previews in flight must not see each
# other's recorder (or, worse, a real turn's).
_CAPTURE_LOCK = threading.Lock()

# What the recorder returns: a diff that changes nothing.
_EMPTY_DIFF = {
    "subplot_beats": {}, "flags_set": {}, "revelations": {"revealed": [], "eligible": []},
    "inventory": {"gained": [], "used": []}, "new_characters": [],
    "scene_update": {"location": "", "summary": "", "present_npcs": []},
}

# Stand-ins for the two texts a real turn would pass; the preview shows the prompt's frame, not
# a turn that never happened.
SAMPLE_ACTION = "(the player's action)"
SAMPLE_NARRATION = "(the narration the model just wrote)"


def tokens(text: str) -> int:
    """An estimate, not a tokenizer: characters over four. Good for comparing sections and
    watching a budget; not for billing."""
    return max(1, round(len(text) / 4)) if text else 0


def build_ctx(raw: dict, sample: dict) -> tuple:
    """`(ctx, left_out, notes)`: a `{story, state}` the real prompt builders accept, for `raw`
    with `sample` applied. `left_out` is the (slot, engine) pairs the projection dropped; `notes`
    are plain-language things the preview leaves out that are not engine slots."""
    projected, left_out = author_model.playable_projection(raw, set(mechanics.registered_engines()))
    notes = []
    state = state_store.new_save_state(projected, "preview")
    ctx = {"story": state_store.freeze(projected), "state": state}
    _overlay(ctx, author_evaluate.build_ctx(projected, sample)["state"])
    if derived.rules(projected):
        # CR-04: the real engine fills `{var}` once creation completes; the preview fills it from
        # whatever the sample bar has picked, so the author sees the text a narrator would get.
        choices = dict(state["protagonist"].get("creation_choices") or {})
        _, values = derived.resolve(projected, derived.creation_ctx(projected, choices))
        ctx["story"] = state_store.freeze(derived.substitute(projected, values))
    return ctx, left_out, notes


def _overlay(ctx: dict, partial: dict) -> None:
    """Lay the slices of `author_evaluate.build_ctx`'s partial state that the sample set over the
    fully seeded state. Only what the sample bar can express; everything else stays as seeded."""
    state = ctx["state"]
    protagonist, seeded = state["protagonist"], partial["protagonist"]
    protagonist["stats"].update(seeded["stats"])
    for flag, value in seeded["flags"]["active"].items():
        protagonist["flags"]["active"][flag] = value
        protagonist["flags"]["meta"][flag] = {"turn_set": 0, "pinned": False}
    protagonist["inventory"].extend(seeded["inventory"])
    protagonist["creation_choices"].update(seeded["creation_choices"])

    plot, seeded_plot = state["plot"], partial["plot"]
    plot["revelations_revealed"].update(seeded_plot["revelations_revealed"])
    plot["current_act"] = seeded_plot["current_act"]
    for sid, sp in seeded_plot["subplots"].items():
        plot["subplots"].setdefault(sid, {}).update(sp)
    state["pacing"]["turn_count"] = partial["pacing"]["turn_count"]
    if "story_clock" in partial["pacing"]:  # CR-13: the sample's Turn is the clock's value too
        state["pacing"]["story_clock"] = partial["pacing"]["story_clock"]

    for name, entry in partial["characters"].items():
        state["characters"].setdefault(name, {"first_seen_turn": 0}).update(
            {"introduced": True, "origin": "sample", **entry})

    for slot, bucket in (partial.get("mechanics") or {}).items():
        state.setdefault("mechanics", {}).setdefault(slot, {}).update(copy.deepcopy(bucket))


def narrator_sections(ctx: dict) -> tuple:
    """`(sections, prompt, matches)`. `sections` is `[{name, text, tokens}]` in prompt order for
    each section that renders; `prompt` is the assembled narrator prompt; `matches` is whether
    the sections rejoin into exactly what `build_system_prompt` returns for the same state."""
    walk = {"story": ctx["story"], "state": copy.deepcopy(ctx["state"])}
    sections = []
    with engine_trace.muted():  # building a prompt to show it is not play, and leaves no trace
        for fn in story_engine.SECTIONS:
            text = fn(walk)
            if text is not None:
                sections.append({"name": fn.__name__.removeprefix("_section_"), "text": text, "tokens": tokens(text)})
        body = "\n\n".join(s["text"] for s in sections)
        prompt = f"You are the narrator of an interactive story.\n\n{body}\n"
        whole = story_engine.build_system_prompt({"story": ctx["story"], "state": copy.deepcopy(ctx["state"])})
    return sections, prompt, prompt == whole


def state_update_prompt(ctx: dict) -> str:
    """The prompt `update_progress_from_turn` sends the state-update model, captured from the
    real function with the model call replaced by a recorder (see the module docstring)."""
    seen = []

    def recorder(prompt, **_kwargs):
        seen.append(prompt)
        return copy.deepcopy(_EMPTY_DIFF)

    work = {"story": ctx["story"], "state": copy.deepcopy(ctx["state"])}
    with _CAPTURE_LOCK, engine_trace.muted():
        original = story_engine.call_llm_json
        story_engine.call_llm_json = recorder
        try:
            story_engine.update_progress_from_turn(work, SAMPLE_ACTION, SAMPLE_NARRATION)
        finally:
            story_engine.call_llm_json = original
    return seen[0] if seen else ""


def preview(raw: dict, sample: dict) -> dict:
    """Everything the Preview tab shows, or `{"error": ...}` when the prompts cannot be built - a
    story that cannot seed a state (no main thread yet) or a sample the engine itself refuses (more
    revealed fragments than the prompt's own budget allows) - which the tab reports, not a crash."""
    try:
        ctx, left_out, notes = build_ctx(raw, sample)
        sections, prompt, matches = narrator_sections(ctx)
        update = state_update_prompt(ctx)
    except (KeyError, TypeError, ValueError) as e:
        return {"error": f"Could not build the preview for this story and sample state: {type(e).__name__}: {e}"}
    return {
        "narrator": {"sections": sections, "prompt": prompt, "tokens": tokens(prompt), "matches": matches},
        "state_update": {"prompt": update, "tokens": tokens(update)},
        "left_out": left_out,
        "notes": notes,
    }

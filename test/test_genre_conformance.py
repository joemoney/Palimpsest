"""P-6 conformance fixtures (SCHEMA_V2_SPEC.md §1 P-6, §7) - the executable form of the
claim that this engine is generic. Three minimal templates under test/fixtures/, each
using a different subset of optional modules, none of them resembling the stories that
actually ship.

These exist to fail loudly when someone reintroduces a genre assumption. That is not
hypothetical: the "stats are opaque to the player, never quote a number" instruction was
hardcoded in build_system_prompt for the whole of v1 and only surfaced when a LitRPG-style
story needed the inverse. A fixture asserting "a story with no stats gets no stats
instruction, and a story with stats gets whatever its template asked for" would have
caught it immediately.

Per §7 each fixture asserts four things - it loads; build_system_prompt emits no header
for an absent module; the state-update schema omits the corresponding fields; and one
stubbed turn applies cleanly - plus the fourth global check: no fixture may require a
Python change. If one does, the schema is not done.

Run directly: python3 test/test_genre_conformance.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import RecordingLLM, load_story_engine  # noqa: E402

se = load_story_engine()
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

# Marker each optional module puts into a prompt. Absent module -> marker must not appear
# anywhere, not as an empty header and not as a zeroed field. Split by which prompt it
# lands in, since the two are built by different code paths.
# NOTE: revelations is checked via the state-update prompt only. Its narration-side marker
# (REVEALED MEMORIES) appears only once a fragment has actually been revealed, so at turn 0
# it is absent for a story that authors the module too - it is not a presence signal.
NARRATION_MARKERS = {
    "locations": "HERE:",
    "factions": "FACTIONS:",
    "characters": "KNOWN CHARACTERS",
    "tracked_entity": "TRACKED ENTITY",
    "stats": "Stats (",
    # Phase 4: inventory became a declared module, so the PLAYER line's Inventory segment
    # is now a presence signal rather than something every story carries. Two fixtures
    # deliberately omit it - a courtroom drama and a comedy of manners have no inventory
    # concept, and used to be asked about items_gained/items_lost every turn regardless.
    "inventory": "| Inventory:",
    # Phase 4: "Relationships:" was the v2 marker, but the PLAYER line now omits the
    # fragment entirely while the roster is empty rather than rendering "Relationships: {}"
    # - a zeroed header is exactly what P-2 forbids, and a fixture at turn 0 has no
    # discovered characters yet. The scale clause on the KNOWN CHARACTERS header is the
    # stable signal: it appears iff the scored_axis engine is bound and the story has a
    # roster to state it over.
    "relationships": "standing is",
    # Phase 6. Only present while a gate is actually shut - a story whose gates are all
    # satisfied contributes no header, which is P-2 rather than an accident of timing.
    "gate": "CLOSED TO THE PROTAGONIST",
}
STATE_UPDATE_MARKERS = {
    "tracked_entity": "entity_interaction",
    "stats": "stat_changes",
    "relationships": '"social"',
    "inventory": '"inventory"',
    "subplots": '"subplot_beats"',
    "failure_conditions": "failure_triggered",
    "progression": '"leverage"',
    "pacing_loop": '"beat"',
    "revelations": "LIVE TRIGGERS",
}

# What each fixture deliberately does NOT author. Kept here rather than derived from the
# file so that deleting a module from a fixture by accident fails instead of silently
# weakening the test.
EXPECTED_ABSENT = {
    "regency.json": ["locations", "factions", "stats", "tracked_entity",
                     "failure_conditions", "pacing_loop", "inventory", "gate"],
    "courtroom.json": ["locations", "factions", "tracked_entity", "stats",
                       "relationships", "progression", "pacing_loop", "inventory",
                       "subplots"],
    "survival.json": ["relationships", "characters", "revelations", "progression", "gate"],
}
EXPECTED_PRESENT = {
    "regency.json": ["relationships", "characters", "revelations", "subplots",
                     "progression"],
    "courtroom.json": ["characters", "revelations", "failure_conditions", "gate"],
    "survival.json": ["stats", "tracked_entity", "failure_conditions", "locations",
                      "inventory", "subplots", "pacing_loop"],
}

# --- the registry dimension (engine v2 phase 3) ------------------------------------
# Which mechanic *engines* each fixture binds, i.e. which of its mechanics blocks carry an
# explicit "engine" key. Written out here rather than derived from the fixture files, for
# the same reason EXPECTED_ABSENT is: deleting a declaration from a fixture has to fail
# loudly instead of silently shrinking what is covered.
#
# P-6 asks that each fixture use a deliberately *different subset*, not that the subsets be
# disjoint, so an engine legitimately appearing in two fixtures is not a defect here.
# **Every phase 4 port must add its engine to this table and to at least one fixture in the
# same commit**, or nothing is guarding P-2 for it.
EXPECTED_ENGINES = {
    "regency.json": ["progression", "relationships", "revelations", "subplots"],
    # courtroom authors no plot.subplots at all - it is the deliberately single-thread
    # fixture, and the reason the subplot field stopped being unconditional (P-2).
    "courtroom.json": ["failure_conditions", "gate", "revelations"],
    "survival.json": ["failure_conditions", "inventory", "pacing_loop", "stats",
                      "subplots"],
}
ALL_ENGINE_SLOTS = sorted({slot for slot, _ in se.mechanics.registered_engines()})

EMPTY_DIFF = {
    "subplot_beats": {}, "flags_set": {}, "revelations": {"revealed": [], "eligible": []},
    "inventory": {"gained": [], "used": []}, "new_characters": [],
    "scene_update": {"location": "", "summary": "unchanged", "present_npcs": []},
}


def load_fixture(name):
    """Fixtures live outside stories/ deliberately - they must never appear in
    state_store.list_stories() and be startable by a real player. That means going
    through freeze/new_save_state directly rather than load_state."""
    with open(os.path.join(FIXTURES, name)) as f:
        story = json.load(f)
    return {"story": se.state_store.freeze(story), "state": se.state_store.new_save_state(story, name)}


def authored_modules(ctx):
    mechanics = se.state_store.thaw(ctx["story"]).get("mechanics", {})
    world = se.state_store.thaw(ctx["story"])["world"]
    present = {k for k, v in mechanics.items() if v}
    for key in ("locations", "factions", "characters"):
        if world.get(key):
            present.add(key)
    return present


for name in sorted(EXPECTED_ABSENT):
    ctx = load_fixture(name)

    # (1) it loads, and the modules it claims to use/omit are the ones actually authored
    present = authored_modules(ctx)
    for module in EXPECTED_PRESENT[name]:
        assert module in present, f"{name}: expected to author {module}, but it isn't there"
    for module in EXPECTED_ABSENT[name]:
        assert module not in present, f"{name}: expected NOT to author {module}"

    # (2) build_system_prompt emits no marker for an absent module
    prompt = se.build_system_prompt(ctx)
    for module in EXPECTED_ABSENT[name]:
        marker = NARRATION_MARKERS.get(module)
        if marker:
            assert marker not in prompt, \
                f"{name}: omits {module} but its narration prompt still contains {marker!r}"
    for module in EXPECTED_PRESENT[name]:
        marker = NARRATION_MARKERS.get(module)
        if marker:
            assert marker in prompt, \
                f"{name}: authors {module} but {marker!r} never reached the narration prompt"

    # (3) the state-update schema omits the corresponding fields
    recorder = RecordingLLM(lambda p: dict(EMPTY_DIFF))
    se.call_llm_json = recorder
    se.update_progress_from_turn(ctx, "a player action", "some narration")
    schema_prompt = recorder.prompts[-1]
    for module in EXPECTED_ABSENT[name]:
        marker = STATE_UPDATE_MARKERS.get(module)
        if marker:
            assert marker not in schema_prompt, \
                f"{name}: omits {module} but the state-update schema still asks for {marker!r}"
    for module in EXPECTED_PRESENT[name]:
        marker = STATE_UPDATE_MARKERS.get(module)
        if marker:
            assert marker in schema_prompt, \
                f"{name}: authors {module} but {marker!r} never reached the state-update schema"

    # (4) one stubbed turn applies cleanly - no KeyError from an absent module's state
    assert ctx["state"]["pacing"]["turn_count"] >= 0
    se.update_progress_from_turn(ctx, "a second action", "more narration")

    # (5) the registry binds exactly what the fixture declares - both directions again.
    # An engine that was never wired up would bind nothing and pass a one-directional
    # absence check happily, which is the failure mode this whole file exists to catch.
    story_dict = se.state_store.thaw(ctx["story"])
    bound = sorted(b.slot for b in se.mechanics.bind(story_dict))
    assert bound == EXPECTED_ENGINES[name], \
        f"{name}: binds {bound}, expected {EXPECTED_ENGINES[name]}"

    # (6) an unbound slot contributes no prompt section - P-2 at the registry level, where
    # it is structural rather than a matter of .get() discipline.
    sections = se.mechanics.prompt_sections(ctx)
    for slot in ALL_ENGINE_SLOTS:
        contributed = [k for k in sections if k.startswith(f"{slot}.")]
        if slot in EXPECTED_ENGINES[name]:
            continue  # presence is asserted by the marker checks above, which are content-aware
        assert not contributed, \
            f"{name}: does not declare a {slot} engine but contributed {contributed}"

    # (7) every section that did reach the prompt is inside its engine's declared budget.
    # prompt_sections raises on a breach, so reaching here is the assertion; this pins the
    # budget as a real number rather than an unset one (§5.4).
    #
    # Phase 4 note: `prompt_budget = 0` is legitimate for an engine contributing no
    # narration text at all - triggered_ending is one, since an ending is entered through
    # the endgame machinery, which writes its own act. So the assertion is the *pairing*,
    # both ways: text implies a budget, no budget implies no text. Asserting "> 0"
    # unconditionally would force a made-up number onto an engine that spends nothing,
    # which is how a budget stops meaning anything.
    for b in se.mechanics.bind(story_dict):
        contributed = [k for k in sections if k.startswith(f"{b.slot}.")]
        if contributed:
            assert b.engine.prompt_budget > 0, \
                f"{name}: {b.slot} contributed {contributed} but declares no prompt_budget"
        elif not b.engine.prompt_budget:
            assert not b.engine.prompt_sections(b.cfg, ctx), \
                f"{name}: {b.slot} declares no prompt_budget but returned prompt text"

    print(f"OK: {name} - loads, no leaked markers either direction, a stubbed turn applies, "
          f"binds exactly {EXPECTED_ENGINES[name] or 'no engines'}")

# --- P-4's minimal template: meta, world.rules, plot.main_thread, plot.opening_scene ---
# §7's fourth check in its strictest form. Everything else in the schema is optional, so
# this must run with no Python change and no placeholder blocks.
minimal = {
    "meta": {"title": "Minimal"},
    "world": {"rules": ["One rule."]},
    "plot": {
        "main_thread": {"title": "T", "description": "D", "acts": [
            {"act_number": 1, "title": "A", "description": "D", "completion_signals": ["s"]}]},
        "opening_scene": {"narration_before_name": "Name?", "narration_after_name": "'{player_name}.'"},
    },
}
ctx = {"story": se.state_store.freeze(minimal), "state": se.state_store.new_save_state(minimal, "minimal")}
prompt = se.build_system_prompt(ctx)
assert prompt, "P-4's minimal template must build a prompt"
for module, marker in NARRATION_MARKERS.items():
    assert marker not in prompt, f"minimal template leaked {marker!r} for absent {module}"
assert "CONTENT RULES" not in prompt, "absent meta.content_rules must contribute no label"
assert "GENRE" not in prompt, "absent meta.genre must contribute no label"
# P-4 through the registry: a template with no mechanics block at all binds nothing,
# contributes no section, and survives the whole turn pipeline.
assert se.mechanics.bind(minimal) == [], "the minimal template must bind no engines"
assert se.mechanics.prompt_sections(ctx) == {}, "the minimal template must contribute no section"
se.mechanics.run_turn_pipeline(ctx)
assert "events" not in ctx["state"], "a no-engine turn must not create an event log"
print(f"OK: P-4 minimal template (meta, world.rules, main_thread, opening_scene) runs - "
      f"{len(prompt)} bytes, no empty modules")

print("\nALL CHECKS PASSED: test_genre_conformance")

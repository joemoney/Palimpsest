"""The mechanic-engine registry - engine v2 phase 1 (docs/ENGINE_V2_SPEC.md §3/§6/§8.2,
docs/ENGINE_V2_PHASES.md phase 1).

Phase 1 ports no mechanic, so the headline assertion is a negative one: every template
that ships today binds zero engines and the turn pipeline is a no-op. The rest pins the
contract the phase 2 port will be written against.

Pure state machine, no LLM and no stubbed model call anywhere in this file - which is the
whole point of moving mechanics into code (§1.4): resolve() is pure, so its tests need no
scaffolding at all.

Run directly: python3 test/test_mechanics_registry.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import load_state_store  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "backend"))
import mechanics  # noqa: E402

MINIMAL_TEMPLATE = {
    "schema_version": 2,
    "meta": {"title": "Registry Test", "genre": "test"},
    "world": {"rules": ["a rule"]},
    "plot": {
        "main_thread": {"title": "T", "description": "d",
                        "acts": [{"act_number": 1, "title": "A", "description": "d",
                                  "completion_signals": ["s"]}]},
        "subplots": {},
        "pacing": {"nudge_frequency": 8, "act_check_frequency": 12, "max_parallel_subplots": 3},
        "opening_scene": {"narration_before_name": "", "narration_after_name": ""},
        "initial_scene": {"location": "", "summary": ""},
    },
}


class _Counter(mechanics.MechanicEngine):
    slot, name, resolve_order = "counter", "test_counter", 10

    def init_state(self, cfg, ctx):
        return {"value": cfg.get("start", 0)}

    def observations(self, cfg, ctx):
        return {"type": "counter_tick"} if cfg.get("asks") else None

    def prompt_sections(self, cfg, ctx):
        return {"line": cfg.get("line", "")}

    def resolve(self, cfg, ctx, observations, events):
        return [mechanics.Effect("counter.bump", reason="tick", by=len(observations))]

    def render(self, cfg, ctx, text):
        return text.replace("[[C]]", "rendered")


class _Late(mechanics.MechanicEngine):
    slot, name, resolve_order = "late", "test_late", 90

    def resolve(self, cfg, ctx, observations, events):
        return [mechanics.Effect("counter.bump", reason="late", by=0)]


mechanics.register(_Counter())
mechanics.register(_Late())

applied = []
mechanics.register_effect("counter.bump", lambda ctx, eff: applied.append(eff))


def ctx_for(mechanics_block):
    story = {**MINIMAL_TEMPLATE, "mechanics": mechanics_block} if mechanics_block else dict(MINIMAL_TEMPLATE)
    return {"story": story, "state": {"pacing": {"turn_count": 7}}}


# --- (1) the phase 1 headline: nothing that ships today binds an engine -------------
roots = [os.path.join(REPO_ROOT, "stories"), os.path.join(REPO_ROOT, "stories", "private")]
checked = []
for root in roots:
    if not os.path.isdir(root):
        continue
    for slug in sorted(os.listdir(root)):
        path = os.path.join(root, slug, "template.json")
        if slug == "private" or not os.path.isfile(path):
            continue
        with open(path) as f:
            story = json.load(f)
        mechanics.validate(story)  # must not raise
        assert mechanics.bind(story) == [], f"{slug} unexpectedly binds an engine in phase 1"
        checked.append(slug)
assert checked, "no story templates were checked - discovery is broken"
print(f"OK: every shipped template ({', '.join(checked)}) validates and binds zero engines")

# An authored mechanics block with no "engine" key is invisible to the registry - that is
# what lets phase 1 land without touching story_engine's existing .get("mechanics") paths.
legacy = ctx_for({"relationships": {"axis": {"negative": "a", "positive": "b"}, "limit": 20}})
mechanics.validate(legacy["story"])
assert mechanics.bind(legacy["story"]) == []
assert mechanics.observation_fields(legacy) == []
assert mechanics.prompt_sections(legacy) == {}
print("OK: a mechanics entry with no 'engine' key is not registry-managed")

# --- (2) unknown engine names fail at load, loudly (§3.2) ---------------------------
try:
    mechanics.validate({"mechanics": {"counter": {"engine": "does_not_exist"}}})
    assert False, "expected an unknown engine name to raise"
except mechanics.UnknownEngineError as e:
    assert "does_not_exist" in str(e) and "test_counter" in str(e), str(e)
print("OK: an unknown engine name raises UnknownEngineError naming the known alternatives")

# It has to raise through the real load path, not just the helper.
tmp_dir = tempfile.mkdtemp(prefix="cyoa_mechanics_test_")
ss = load_state_store(tmp_dir)
story_dir = os.path.join(ss.STORIES_DIR, "broken")
os.makedirs(story_dir, exist_ok=True)
with open(os.path.join(story_dir, "template.json"), "w") as f:
    json.dump({**MINIMAL_TEMPLATE, "mechanics": {"counter": {"engine": "nope"}}}, f)
try:
    ss.load_template("broken")
    assert False, "expected load_template to reject an unknown engine"
except mechanics.UnknownEngineError:
    pass
print("OK: load_template refuses a template naming an engine this build doesn't have")

# --- (3) P-2: engine state exists only when an engine asks for it -------------------
plain = ss.new_save_state(dict(MINIMAL_TEMPLATE), "plain")
assert "mechanics" not in plain, "a story with no bound engines must get no mechanics state"
seeded = ss.new_save_state({**MINIMAL_TEMPLATE,
                            "mechanics": {"counter": {"engine": "test_counter", "start": 5}}},
                           "seeded")
assert seeded["mechanics"] == {"counter": {"value": 5}}, seeded.get("mechanics")
print("OK: init_state seeds state.mechanics.<slot>, and an unbound story gets no key at all")

# --- (4) resolve order, effects, and application ------------------------------------
both = ctx_for({"counter": {"engine": "test_counter"}, "late": {"engine": "test_late"}})
assert [b.slot for b in mechanics.bind(both["story"])] == ["counter", "late"]
effects = mechanics.resolve_all(both, [{"type": "x"}, {"type": "y"}])
assert [e.reason for e in effects] == ["tick", "late"], effects
assert effects[0] == mechanics.Effect("counter.bump", reason="tick", by=2)
assert "counter.bump" in repr(effects[0]) and "by=2" in repr(effects[0])
print("OK: engines resolve in resolve_order and return comparable Effects")

applied.clear()
mechanics.apply_effects(both, effects)
assert [e.reason for e in applied] == ["tick", "late"]
try:
    mechanics.apply_effects(both, [mechanics.Effect("counter.unhandled")])
    assert False, "expected an unhandled effect kind to raise"
except ValueError as e:
    assert "counter.unhandled" in str(e)
print("OK: effects apply in order, and an unhandled kind raises instead of vanishing")

# --- (5) cadence, prompt budget, render, event log ----------------------------------
assert mechanics.observation_fields(ctx_for({"counter": {"engine": "test_counter"}})) == []
asking = ctx_for({"counter": {"engine": "test_counter", "asks": True}})
assert mechanics.observation_fields(asking) == [{"type": "counter_tick"}]
print("OK: an engine returning None from observations() contributes no field (§5.2)")

assert mechanics.prompt_sections(ctx_for({"counter": {"engine": "test_counter"}})) == {}
lined = ctx_for({"counter": {"engine": "test_counter", "line": "HERE: x"}})
assert mechanics.prompt_sections(lined) == {"counter.line": "HERE: x"}
_Counter.prompt_budget = 3
try:
    mechanics.prompt_sections(lined)
    assert False, "expected an over-budget prompt section to raise"
except ValueError as e:
    assert "budget" in str(e)
_Counter.prompt_budget = 0
print("OK: an empty prompt section is omitted, and an over-budget one raises (§5.4)")

assert mechanics.render_all(lined, "a [[C]] b") == "a rendered b"
print("OK: render() runs as a deterministic post-narration substitution")

state = {"pacing": {"turn_count": 12}}
mechanics.record_events(state, [{"type": "travel", "distance": "long"}])
assert state["events"] == [{"turn": 12, "type": "travel", "distance": "long"}]
mechanics.record_events(state, [])
assert len(state["events"]) == 1, "recording nothing must not append"
print("OK: record_events stamps the turn and setdefaults the log (§8.2)")

# --- (6) the pipeline is a genuine no-op for an unported story ----------------------
untouched = ctx_for(None)
before = json.dumps(untouched["state"], sort_keys=True)
mechanics.run_turn_pipeline(untouched)
assert json.dumps(untouched["state"], sort_keys=True) == before
assert "events" not in untouched["state"], "a no-engine turn must not create an event log"
print("OK: run_turn_pipeline changes nothing for a story with no bound engines")

print("\nALL CHECKS PASSED: test_mechanics_registry")

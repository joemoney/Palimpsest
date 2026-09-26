"""CR-03: the schema's `x-visibility` annotations agree with what the engine actually sends.

`visibility.py` only *reads* the annotations; the engine decides what a model sees. So this checks
the two agree by running the real prompts (`author_preview`) over the three shipped stories under
a sample state that opens as much as the sample bar can, and looking in both directions
(CLAUDE.md, Testing: one-directional absence testing passes for a mechanism that was never wired):

- nothing marked `x-secret` reaches the narrator or the state-update prompt (the leak test);
- a field marked `narrator` / `every turn`, where the story authors it, does reach the narrator
  prompt (so the annotation is not merely hopeful).

Also: the annotations are well-formed, the leak lint fires when a secret is pasted into a narrator
field, and a `_` note or an unannotated field is never a source or target.

Run directly: python3 test/test_visibility.py
"""
import copy
import io
import contextlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import load_story_engine  # noqa: E402

se = load_story_engine()
import author_lint  # noqa: E402
import author_preview  # noqa: E402
import visibility  # noqa: E402

schema = author_lint.template_schema()


# --- the annotations themselves ----------------------------------------------------------------
def annotated(node, path=""):
    if isinstance(node, dict):
        if "x-visibility" in node:
            yield path, node
        elif "x-visible-when" in node or "x-secret" in node:
            raise AssertionError(f"{path}: x-visible-when / x-secret without x-visibility")
        for k, v in node.items():
            yield from annotated(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from annotated(v, f"{path}[{i}]")


found = list(annotated(schema))
assert len(found) >= 60, len(found)
for path, node in found:
    assert node["x-visibility"] in visibility.VISIBILITIES, (path, node["x-visibility"])
    assert node.get("x-visible-when") is None or isinstance(node["x-visible-when"], str), path
    assert not node.get("x-secret") or node["x-visibility"] in ("author", "judge"), \
        f"{path}: a secret must be author or judge text, never narrator text"
print(f"OK: {len(found)} annotations, all well-formed; a secret is never narrator-visible")

# --- classification ----------------------------------------------------------------------------
sample_story = {
    "world": {"characters": {"Lark": {"name": "Lark", "description": "a diver", "role": "the buyer",
                                       "canon": {"truth": "she sold the codes", "_note": "x"}}}},
    "plot": {"main_thread": {"title": "T", "description": "D", "_authored": "note",
                              "acts": [{"act_number": 1, "title": "A", "description": "B"}]}},
    "meta": {"title": "M", "synopsis": "a public pitch"},
}
sample_story["plot"]["subplots"] = {"s1": {"title": "S", "description": "d", "ties_to_main_plot": "how it ties in"}}
by_path = {f["path"]: f for f in visibility.classify(sample_story, schema)}
assert by_path["world.characters.Lark.description"]["visibility"] == "narrator"
assert by_path["world.characters.Lark.canon.truth"]["visibility"] == "author" and by_path["world.characters.Lark.canon.truth"]["secret"]
assert by_path["world.characters.Lark.role"]["visibility"] == "author" and not by_path["world.characters.Lark.role"]["secret"]
assert by_path["plot.main_thread.acts[0].description"]["visibility"] == "narrator"
assert by_path["meta.synopsis"]["visibility"] == "author" and not by_path["meta.synopsis"]["secret"]
assert "plot.subplots.s1.ties_to_main_plot" not in by_path, "an unannotated field is neither source nor target"
assert not any("_note" in p or "_authored" in p for p in by_path), "author notes are skipped"
print("OK: classify resolves nested paths, inherits from a container, skips notes and unannotated fields")

# --- the leak lint -----------------------------------------------------------------------------
SECRET = "the ledger she keeps was written by the buyer who owns the whole ship outright"
story = {"meta": {"title": "T"},
         "world": {"characters": {"Lark": {"name": "Lark", "description": "a diver",
                                            "canon": {"truth": SECRET}}}},
         "plot": {"main_thread": {"title": "T", "description": "D", "acts": [
             {"act_number": 1, "title": "A", "description": "B"}]}}}
assert visibility.leak_issues(story, schema) == []
leaky = copy.deepcopy(story)
leaky["plot"]["main_thread"]["acts"][0]["description"] = f"Act one: {SECRET}."
got = visibility.leak_issues(leaky, schema)
assert [(i["id"], i["severity"]) for i in got] == [("L03", visibility.LEAK_SEVERITY)], got
assert "plot.main_thread.acts[0].description" in got[0]["message"] and "world.characters.Lark.canon.truth" in got[0]["message"]
own = copy.deepcopy(story)
own["world"]["characters"]["Lark"]["description"] = SECRET
assert visibility.leak_issues(own, schema) == [], "a character's own description is reported by cast_issues, not twice"
note = copy.deepcopy(story)
note["plot"]["main_thread"]["_authored"] = SECRET
assert visibility.leak_issues(note, schema) == [], "an author note may restate anything"
JUDGE_SECRET = "the scene only counts as the ending if the operator gives up the ship in person"
judge = copy.deepcopy(story)
judge["mechanics"] = {"endings": {"entries": [{"id": "e", "kind": "destination", "name": "E", "criteria": JUDGE_SECRET}]}}
assert visibility.leak_issues(judge, schema) == []
judge["plot"]["main_thread"]["description"] = JUDGE_SECRET
got = visibility.leak_issues(judge, schema)
assert [i["id"] for i in got] == ["L04"] and "mechanics.endings.entries[0].criteria" in got[0]["message"], got
print("OK: L03 (secret -> narrator/judge) and L04 (secret judge text -> narrator) fire; notes and a character's own canon do not")

# --- the engine agrees, in both directions -----------------------------------------------------
def prompts(slug):
    raw = se.state_store.load_template_raw(slug)
    m = raw.get("mechanics") or {}
    revealed = [e["id"] for e in (m.get("revelations") or {}).get("entries", []) if isinstance(e, dict)][:2]
    chars = list((raw.get("world") or {}).get("characters") or {})
    sample = {"flags": [f["id"] for f in (m.get("flags") or {}).get("declared", []) if isinstance(f, dict)],
              "revealed": revealed, "relationships": {c: 30 for c in chars}, "peaks": {c: 50 for c in chars},
              "turn": 12}
    with contextlib.redirect_stdout(io.StringIO()):
        result = author_preview.preview(raw, sample)
    assert "error" not in result, (slug, result)
    return raw, visibility.norm(result["narrator"]["prompt"]), visibility.norm(result["state_update"]["prompt"])


# Real leaks this test found in shipped content. new_babel's tracked_entity.description, sent every
# turn, repeats a run of the entity's own secret dialogue_style. Listed, not hidden: the test fails
# if a NEW secret reaches a prompt, and also if one listed here stops leaking (delete it then).
KNOWN_LEAKS = {("new_babel", "mechanics.tracked_entity.canon.harvested_v1_record.dialogue_style")}
leaked = set()
checked_secret = checked_shown = 0
for slug in ("example", "new_babel", "the_missing_core"):
    raw, narrator, update = prompts(slug)
    for f in visibility.classify(raw, schema):
        text = visibility.norm(f["text"])
        if f["secret"] and len(text) >= visibility.LEAK_MIN_LEN:
            checked_secret += 1
            if visibility.shares_a_run(narrator, text) or visibility.shares_a_run(update, text):
                leaked.add((slug, f["path"]))
        if f["visibility"] == "narrator" and f["when"] == "every turn" and len(text) >= 30:
            checked_shown += 1
            assert text[:30] in narrator, f"{slug}: {f['path']} is annotated narrator/every turn but is not in the prompt"
assert leaked == KNOWN_LEAKS, f"secrets reaching a prompt: {sorted(leaked)}; known: {sorted(KNOWN_LEAKS)}"
assert checked_secret >= 5 and checked_shown >= 30, (checked_secret, checked_shown)
print(f"OK: of {checked_secret} secret fields only the {len(KNOWN_LEAKS)} known one reaches a prompt; "
      f"{checked_shown} 'every turn' narrator fields all do (3 stories)")

print("\nALL CHECKS PASSED: test_visibility")

"""Who sees a template field - the schema's `x-visibility` annotations, read (CR-03; Authoring
Tool decision D3: one reading of the schema, shared by the board, lint and the leak test).

Each annotation is on a schema property:

    "x-visibility": "narrator" | "judge" | "author"
    "x-visible-when": "every turn"        (optional; the board's badge text)

- **narrator**: sent to the model that writes the scene.
- **judge**: sent to a model that adjudicates (the state-update pass, an ending judge, the act
  check) but never to the narrator.
- **author**: secret. Never sent to any model, on purpose: canon, a character's role, plot notes,
  a protagonist's hidden background.
- **`"x-secret": true`** (beside either of the first two): text the narrator must never learn.
  Only a secret is a leak *source*. A judge's `trigger` or `detect` describes something
  observable that a scene may well echo, and a character's `role` is a working note; neither is
  hidden, so repeating their wording is not a leak.
- **no annotation**: not sent to any model, but not a secret either (a synopsis, an id, a
  number). It is neither a leak source nor a leak target.

An annotation covers everything under the property (a `canon` object's every value). Keys that
start with `_` are author notes and are never sources or targets: an author note may legitimately
restate what the template says elsewhere.

This module only reads the schema and the template. It never decides what the engine sends - the
engine does, and `test/test_visibility.py` checks the two agree by running the real prompts.

`leak_issues` is L03 and L04 of the linter: a 40-character run of secret `author` text that
reappears in a `narrator` or `judge` field (L03), or of secret `judge` text in a `narrator` field
(L04), is a secret the narrator will play from the first turn.
"""
import json
import os

LEAK_MIN_LEN = 40

# Authoring_Tool_Spec §6: L03/L04 are errors. A lint error takes a story off the player-facing list
# (`app._story_blocked_by_lint`), so a leak must be fixed before the story can be played again.
# Promoted 2026-10-03 at the author's direction. (The character-only check in
# `author_lint.cast_issues` has always been an error too.)
LEAK_SEVERITY = "error"

VISIBILITIES = ("narrator", "judge", "author")


def norm(s) -> str:
    return " ".join((s or "").lower().split()) if isinstance(s, str) else ""


def shares_a_run(hay: str, needle: str) -> bool:
    """Whether a `LEAK_MIN_LEN`-character run of `needle` appears in `hay` (both already
    `norm`ed). The needle is windowed in steps of four, so a run of 43+ characters is always
    found and one of exactly 40 usually is: the same sampling the character check has always used."""
    if len(hay) < LEAK_MIN_LEN or len(needle) < LEAK_MIN_LEN:
        return False
    return any(needle[i:i + LEAK_MIN_LEN] in hay for i in range(0, len(needle) - LEAK_MIN_LEN + 1, 4))


def _resolve(node, schema):
    guard = 0
    while isinstance(node, dict) and "$ref" in node and guard < 20:
        node = schema.get("$defs", {}).get(node["$ref"].split("/")[-1])
        guard += 1
    return node if isinstance(node, dict) else {}


def _variants(node, schema):
    """`node` and, for a oneOf/anyOf/allOf, each of its branches, resolved."""
    node = _resolve(node, schema)
    out = [node]
    for key in ("oneOf", "anyOf", "allOf"):
        for branch in node.get(key) or []:
            out.extend(_variants(branch, schema))
    return out


def _child(node, key, schema):
    """The schema node a JSON object key or list item lands on, or None."""
    for v in _variants(node, schema):
        if isinstance(key, int):
            if isinstance(v.get("items"), dict):
                return v["items"]
        else:
            props = v.get("properties") or {}
            if key in props:
                return props[key]
            if isinstance(v.get("additionalProperties"), dict):
                return v["additionalProperties"]
    return None


def annotation(node, schema):
    """`(visibility, when, secret)` a schema node carries itself, or (None, None, False)."""
    for candidate in (node, _resolve(node, schema)):
        if isinstance(candidate, dict) and candidate.get("x-visibility") in VISIBILITIES:
            return candidate["x-visibility"], candidate.get("x-visible-when"), bool(candidate.get("x-secret"))
    return None, None, False


def _walk(raw, schema, pick):
    """`[(path, string, picked)]` for every string in `raw` under a schema node for which
    `pick(node)` is not None, `picked` being what the nearest such ancestor returned. `_` keys are
    author notes and skipped."""
    out = []

    def step(child, inherited):
        got = pick(child) if child is not None else None
        return got if got is not None else inherited

    def walk(value, node, path, inherited):
        if isinstance(value, str):
            if inherited is not None:
                out.append((path, value, inherited))
        elif isinstance(value, dict):
            for k, v in value.items():
                if isinstance(k, str) and k.startswith("_"):
                    continue
                child = _child(node, k, schema)
                walk(v, child, f"{path}.{k}" if path else k, step(child, inherited))
        elif isinstance(value, list):
            for i, v in enumerate(value):
                child = _child(node, i, schema)
                walk(v, child, f"{path}[{i}]", step(child, inherited))

    walk(raw, schema, "", None)
    return out


def classify(raw: dict, schema: dict) -> list:
    """`[{path, text, visibility, when, secret}]` for every string in `raw` that some annotation
    covers, in template order. `path` is dotted with `[i]` for list items."""
    def pick(node):
        vis, when, secret = annotation(node, schema)
        return (vis, when, secret) if vis else None

    return [{"path": path, "text": text, "visibility": vis, "when": when, "secret": secret}
            for path, text, (vis, when, secret) in _walk(raw, schema, pick)]


def annotated_strings(raw: dict, schema: dict, key: str) -> list:
    """`[(path, text, value)]` for every string under a schema node carrying annotation `key`
    (e.g. `x-assist`), `value` being the annotation's value. The linter's way to find "every
    field of kind X" without listing paths that would drift from the schema."""
    def pick(node):
        for candidate in (node, _resolve(node, schema)):
            if isinstance(candidate, dict) and key in candidate:
                return candidate[key]
        return None

    return _walk(raw, schema, pick)


def _same_character(source: str, target: str) -> bool:
    """A character's own canon repeated in that character's own description/first contact/hook:
    already reported, with better wording, by `author_lint.cast_issues`, so not twice."""
    if not source.startswith("world.characters.") or not target.startswith("world.characters."):
        return False
    name = lambda p: p[len("world.characters."):].split(".")[0]  # noqa: E731
    return name(source) == name(target) and ".canon" in source


def leak_issues(raw: dict, schema: dict) -> list:
    """L03 and L04 (Authoring_Tool_Spec §6): `[{id, severity, message}]`."""
    fields = classify(raw, schema)
    out = []
    for source in fields:
        if not source["secret"] or len(norm(source["text"])) < LEAK_MIN_LEN:
            continue
        needle = norm(source["text"])
        for target in fields:
            if target is source or target["path"] == source["path"]:
                continue
            if source["visibility"] == "author" and target["visibility"] not in ("narrator", "judge"):
                continue
            if source["visibility"] == "judge" and target["visibility"] != "narrator":
                continue
            if _same_character(source["path"], target["path"]):
                continue
            if shares_a_run(norm(target["text"]), needle):
                rule, what = ("L03", "author-only text") if source["visibility"] == "author" else ("L04", "judge-only text")
                who = ("the narrator, so the secret is played from the first turn" if target["visibility"] == "narrator"
                       else "a judge, which was never meant to know it")
                out.append({"id": rule, "severity": LEAK_SEVERITY,
                            "message": f"{target['path']} repeats {what} from {source['path']}. It reaches {who}."})
    return out


_SCHEMA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "schema", "template.v3.schema.json")
_schema = None


def template_schema() -> dict:
    """The template JSON Schema, loaded once and shared (the linter, the board and the loader
    all read the same annotations - D3)."""
    global _schema
    if _schema is None:
        with open(_SCHEMA_PATH, "r", encoding="utf-8") as f:
            _schema = json.load(f)
    return _schema


def split_author(raw, schema=None):
    """`(engine_view, author_only)` for a template (CR-03, *Engine behaviour*): the loader's
    structural allowlist. `engine_view` is `raw` with every `author`-visibility subtree and every
    `_` author-note key removed, so a prompt builder holding it *cannot* reach a secret - it is not
    that the engine happens to read three keys, it is that only three keys are there.
    `author_only` is exactly what was removed, same shape, for the screens that show it (the Plot
    Manager, the cast card). Neither shares structure with `raw`."""
    schema = schema or template_schema()

    def walk(value, node):
        if isinstance(value, dict):
            kept, removed = {}, {}
            for k, v in value.items():
                child = _child(node, k, schema) if node is not None else None
                if (isinstance(k, str) and k.startswith("_")) or annotation(child, schema)[0] == "author":
                    removed[k] = v
                    continue
                keep, gone = walk(v, child)
                kept[k] = keep
                if gone not in (None, {}, []):
                    removed[k] = gone
            return kept, removed
        if isinstance(value, list):
            kept, removed = [], []
            for i, v in enumerate(value):
                keep, gone = walk(v, _child(node, i, schema) if node is not None else None)
                kept.append(keep)
                removed.append(gone)
            return kept, (removed if any(r not in (None, {}, []) for r in removed) else [])
        return value, None

    return walk(raw, schema)

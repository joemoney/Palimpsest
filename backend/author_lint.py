"""S1's server-side lint subset (AUTHORING_TOOL_PHASES.md decision D3): L01/L08/L09/L16, the
board's structural-flow checks (Authoring_Tool_Spec.md §4.6), and the Cast checks carried over
from the reference prototype (`docs/Missing_Core_Storyboard_Reference_Design.html`,
`issues()`/`canonLeaks()`). Pure and offline-testable - no Flask, no engine imports. L10 (S2) is the one check that
reads a condition: it walks `conditions.iter_conditions`. Everything else runs entirely against `author_model.to_board_model`'s `{story, nodes, edges, characters}`
projection plus the raw template dict (for L01).

Every issue is `{id, severity, message, node_id?, char?}`. `severity` is `"error"` (blocks a
save) or `"warning"` (doesn't) - CLAUDE.md's "schema -> lint (errors block, warnings don't)".
"""
import json
import os

import jsonschema

import conditions

_SCHEMA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "schema", "template.v3.schema.json")

_schema = None
_validator = None


def _get_validator():
    # Loaded lazily and cached rather than at import time, so a missing/old jsonschema (this
    # repo's own local environment has 3.2.0, which has no Draft202012Validator - see CLAUDE.md
    # Phase 0) doesn't break importing this module; only calling lint() with L01 live requires it.
    global _schema, _validator
    if _validator is None:
        with open(_SCHEMA_PATH, "r", encoding="utf-8") as f:
            _schema = json.load(f)
        _validator = jsonschema.Draft202012Validator(_schema)
    return _validator


def schema_errors(raw: dict) -> list:
    """L01. One issue per schema violation, path included in the message so an author can find
    the field without a JSON viewer."""
    validator = _get_validator()
    out = []
    for e in sorted(validator.iter_errors(raw), key=lambda e: list(e.path)):
        path = ".".join(str(p) for p in e.path) or "(top level)"
        out.append({"id": "L01", "severity": "error", "message": f"{path}: {e.message}"})
    return out


def _node(nodes: list, node_id: str):
    return next((n for n in nodes if n.get("id") == node_id), None)


def _destinations(nodes: list) -> list:
    return [n for n in nodes if n.get("kind") == "ending" and n.get("ekind") == "destination"]


def _subplots(nodes: list) -> list:
    return [n for n in nodes if n.get("kind") == "subplot"]


def _waypoint_carriers(end: dict, edges: list) -> dict:
    """waypoint id -> list of subplot ids whose `delivers` edge names it."""
    out = {}
    for w in end.get("waypoints") or []:
        wid = w.get("id")
        out[wid] = [e["from"] for e in edges
                    if e.get("type") == "delivers" and e.get("to") == end.get("id") and e.get("wp") == wid]
    return out


def structural_issues(model: dict) -> list:
    """L08, L09, L16, and the §4.6 structural-flow checks - everything computable from the
    board model's shape alone, no schema or engine lookup needed."""
    out = []
    nodes = model.get("nodes", [])
    edges = model.get("edges", [])
    node_ids = {n.get("id") for n in nodes}
    destinations = _destinations(nodes)

    # L08 / "no catch-all": at least one destination with no viable_while (catchAll=True).
    if not any(n.get("catchAll") for n in destinations):
        out.append({"id": "L08", "severity": "error",
                     "message": "No catch-all ending. If every other ending is ruled out, "
                                "the story has nowhere to go."})

    for end in destinations:
        waypoints = end.get("waypoints") or []
        carriers = _waypoint_carriers(end, edges)
        threads = {sid for ids in carriers.values() for sid in ids}
        title = end.get("title") or end.get("id")

        if not waypoints:
            out.append({"id": "structural", "severity": "warning", "node_id": end.get("id"),
                        "message": f"{title} has no waypoints, so nothing steers the story toward it."})
        elif not threads:
            out.append({"id": "structural", "severity": "error", "node_id": end.get("id"),
                        "message": f"Nothing leads to {title}: no thread carries any of its waypoints."})
        elif len(threads) == 1 and not end.get("catchAll"):
            carrier = _node(nodes, next(iter(threads)))
            carrier_title = carrier.get("title") if carrier else next(iter(threads))
            out.append({"id": "structural", "severity": "warning", "node_id": end.get("id"),
                        "message": f"{title} rests on one thread ({carrier_title}). "
                                   "If that thread fails, the ending is lost."})

        for w in waypoints:
            # L09: neither done_when nor a judge `detect` text - nothing can ever mark it done.
            if not w.get("done_when") and not w.get("detect"):
                out.append({"id": "L09", "severity": "error", "node_id": end.get("id"),
                            "message": f"{title}: \"{w.get('plant', w.get('id'))}\" has neither "
                                       "done_when nor detect, so it can never be recognised as complete."})
            # Structural: uncarried waypoint - no delivers edge names it at all.
            if threads and not carriers.get(w.get("id")):
                out.append({"id": "structural", "severity": "warning", "node_id": end.get("id"),
                            "message": f"{title}: \"{w.get('plant', w.get('id'))}\" has no thread to carry it."})

    # L16: dangling ids - an edge pointing at a node id that doesn't exist on the board.
    for e in edges:
        for side in ("from", "to"):
            target = e.get(side)
            if target and target not in node_ids:
                out.append({"id": "L16", "severity": "error",
                            "message": f"Edge {side} references unknown id \"{target}\"."})

    for n in _subplots(nodes):
        title = n.get("title") or n.get("id")
        incoming = [e for e in edges if e.get("to") == n.get("id") and e.get("type") in ("opens", "unlocks")]
        for e in incoming:
            if e.get("type") == "unlocks" and not e.get("cond_raw"):
                out.append({"id": "structural", "severity": "error", "node_id": n.get("id"),
                            "message": f"{title} has an unlock link with no condition. Give it one "
                                       "(e.g. {\"stat\": {\"axis\": \"reach\", \"at_least\": 20}}), "
                                       "or make the thread active at start."})
        if not incoming and not n.get("starts_active") and not n.get("activate_when"):
            out.append({"id": "structural", "severity": "error", "node_id": n.get("id"),
                        "message": f"{title} never becomes active: nothing opens or unlocks it."})
        carries = any(e.get("type") == "delivers" and e.get("from") == n.get("id") for e in edges)
        if n.get("role") == "spine" and not carries:
            out.append({"id": "structural", "severity": "warning", "node_id": n.get("id"),
                        "message": f"{title} is a spine thread but carries no waypoint."})
        if n.get("role") == "texture" and carries:
            out.append({"id": "structural", "severity": "warning", "node_id": n.get("id"),
                        "message": f"{title} is texture but carries waypoints. Make it spine or personal."})

    # Delivery with no waypoint chosen.
    for e in edges:
        if e.get("type") == "delivers" and not e.get("wp"):
            src = _node(nodes, e.get("from"))
            dst = _node(nodes, e.get("to"))
            out.append({"id": "structural", "severity": "error",
                        "message": f"{src.get('title') if src else e.get('from')} -> "
                                   f"{dst.get('title') if dst else e.get('to')}: "
                                   "choose which waypoint this thread delivers."})

    return out


_LEAK_MIN_LEN = 40


def _norm(s: str) -> str:
    return " ".join((s or "").lower().split())


def _canon_leaks(character: dict) -> list:
    """L03's canon-leak check, scoped to a single character: a 40+ character run of `canon`
    (author-only) text reappearing verbatim in a field the narrator/judge actually sees."""
    out = []
    canon_rows = character.get("canon") or []
    for field in ("description", "first_contact", "hook"):
        hay = _norm(character.get(field))
        if len(hay) < _LEAK_MIN_LEN:
            continue
        for row in canon_rows:
            needle = _norm(row.get("v"))
            for i in range(0, max(0, len(needle) - _LEAK_MIN_LEN) + 1, 4):
                if needle[i:i + _LEAK_MIN_LEN] in hay:
                    out.append({"field": field, "key": row.get("k")})
                    break
    return out


def cast_issues(model: dict) -> list:
    """Carried over from the reference prototype's Cast health checks."""
    out = []
    characters = model.get("characters", [])
    seen = set()
    for c in characters:
        name = (c.get("name") or "").strip()
        if not name:
            out.append({"id": "cast", "severity": "error",
                        "message": "A character has no name. The name is its key in world.characters."})
        elif name in seen:
            out.append({"id": "cast", "severity": "error", "char": name,
                        "message": f"Two characters are named {name}. The second overwrites the first on save."})
        seen.add(name)

        for leak in _canon_leaks(c):
            out.append({"id": "cast", "severity": "error", "char": name,
                        "message": f"{name}: the {leak['field']} repeats canon \"{leak['key']}\". "
                                   "It reaches the narrator, so the secret is played from turn one."})

        if not (c.get("description") or "").strip():
            out.append({"id": "cast", "severity": "warning", "char": name,
                        "message": f"{name} has no description, so the narrator gets the name and nothing else."})

    return out


def condition_issues(raw: dict) -> list:
    """L10. Every condition field in the template, checked by `conditions.check` - the same
    static half of the evaluator's unknown-referent rule, so lint and the engine cannot disagree
    about what is unknown. Save-blocking for every field, not just the fail-closed ones: a typo
    in `ready_when` would silently never fire, and one in a gate silently opens it."""
    out = []
    for path, cond, _polarity, _ending in conditions.iter_conditions(raw):
        for problem in conditions.check(cond, raw):
            out.append({"id": "L10", "severity": "error", "message": f"{path}: {problem}"})
    return out


def flag_issues(raw: dict) -> list:
    """Declared-flag hygiene (`mechanics.flags.declared`): a duplicate id is an error (the second
    would be unreachable by name), and a flag with no `detect` is a warning - the state-update
    pass is only ever told what to look for from that text, so nothing could ever set it."""
    block = (raw.get("mechanics") or {}).get("flags")
    out, seen = [], set()
    for f in (block.get("declared") or []) if isinstance(block, dict) else []:
        if not isinstance(f, dict):
            continue
        fid = f.get("id", "")
        if fid in seen:
            out.append({"id": "flags", "severity": "error", "message": f"Flag {fid} is declared twice."})
        seen.add(fid)
        if not (f.get("detect") or "").strip():
            out.append({"id": "flags", "severity": "warning",
                        "message": f"Flag {fid} has no detect text, so nothing can ever set it."})
    return out


def lint(raw: dict, model: dict) -> list:
    """L01 and L10 against `raw`, everything else against `model`
    (`author_model.to_board_model(raw)`). L10 is skipped when the schema already rejected the
    template: a condition that isn't an object at all is L01's finding, and reporting it twice
    in two vocabularies is noise."""
    schema = schema_errors(raw)
    return (schema + ([] if schema else condition_issues(raw)) + flag_issues(raw)
            + structural_issues(model) + cast_issues(model))


def has_errors(issues: list) -> bool:
    return any(i.get("severity") == "error" for i in issues)

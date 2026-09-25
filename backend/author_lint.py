"""The board's server-side lint (AUTHORING_TOOL_PHASES.md decision D3): L01/L06/L07/L08/L09/L10/L16, the
board's structural-flow checks (Authoring_Tool_Spec.md §4.6), and the Cast checks carried over
from the reference prototype (`docs/Missing_Core_Storyboard_Reference_Design.html`,
`issues()`/`canonLeaks()`). Pure and offline-testable - no Flask, no engine imports. L10 (S2) is the one check that
reads a condition: it walks `conditions.iter_conditions`. L06/L07 (S3) read the stat ladders from
the raw template. Everything else runs entirely against `author_model.to_board_model`'s `{story, nodes, edges, characters}`
projection plus the raw template dict (for L01).

Every issue is `{id, severity, message, node_id?, char?, axis?}`. `severity` is `"error"` (blocks a
save) or `"warning"` (doesn't) - CLAUDE.md's "schema -> lint (errors block, warnings don't)".
"""
import json
import os

import jsonschema

import author_model
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
            # An explicit starts_active: false is a choice - the thread waits for a manual
            # activation in the Subplot Manager - so it is worth a warning, not an error. With
            # neither field at all, nothing will ever start it.
            if n.get("starts_active") is False:
                out.append({"id": "structural", "severity": "warning", "node_id": n.get("id"),
                            "message": f"{title} only starts when activated by hand in the Subplot Manager."})
            else:
                out.append({"id": "structural", "severity": "error", "node_id": n.get("id"),
                            "message": f"{title} never becomes active: set Becomes active in its panel."})
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


def revelation_issues(raw: dict) -> list:
    """Fragment hygiene (`mechanics.revelations`), reported under L16 (dangling ids) where an id
    is involved: a duplicate id is an error (a condition naming it can only ever mean the first),
    as is an `after` naming a fragment that doesn't exist - the engine skips an unknown blocker
    rather than waiting on it forever, so the ordering the author wrote would silently not hold.
    An `after` that names the fragment itself, or a cycle, can never be satisfied: error. A
    missing trigger or content is a warning - the fragment can never be revealed, or reveals
    nothing."""
    entries = author_model._revelation_entries(raw)
    out, seen = [], set()
    ids = {e.get("id") for e in entries}
    after = {e.get("id"): [a for a in e.get("after") or [] if a in ids] for e in entries}
    for e in entries:
        fid = e.get("id") or "(no id)"
        if fid in seen:
            out.append({"id": "L16", "severity": "error", "message": f"Fragment {fid} is authored twice."})
        seen.add(fid)
        for a in e.get("after") or []:
            if a not in ids:
                out.append({"id": "L16", "severity": "error",
                            "message": f"Fragment {fid} waits on {a}, which is not a fragment in this story."})
        if not (e.get("trigger") or "").strip():
            out.append({"id": "fragments", "severity": "warning",
                        "message": f"Fragment {fid} has no trigger, so it can never be revealed."})
        if not (e.get("content") or "").strip():
            out.append({"id": "fragments", "severity": "warning",
                        "message": f"Fragment {fid} has no content, so revealing it tells the narrator nothing."})
    for start in after:
        stack, visited = list(after[start]), set()
        while stack:
            nxt = stack.pop()
            if nxt == start:
                out.append({"id": "fragments", "severity": "error",
                            "message": f"Fragment {start} waits on itself through its 'after' chain, so it can never be revealed."})
                break
            if nxt not in visited:
                visited.add(nxt)
                stack.extend(after.get(nxt, []))
    return out


# L13's stopwords: words so common in narration that a lore key made of one fires every turn.
_STOPWORDS = {"the", "and", "you", "your", "for", "with", "that", "this", "what", "who", "her",
              "his", "him", "she", "they", "them", "there", "then", "when", "have", "has", "was",
              "are", "not", "but", "all", "one", "out", "into", "from"}


def thread_cast_issues(raw: dict) -> list:
    """L16 for a thread's `cast`: every name must be an authored character. A character's name
    is its only identity (CLAUDE.md), so a cast entry left behind by a rename names nobody."""
    known = set(((raw.get("world") or {}).get("characters") or {}))
    out = []
    for sid, sp in ((raw.get("plot") or {}).get("subplots") or {}).items():
        for name in (sp or {}).get("cast") or []:
            if name not in known:
                out.append({"id": "L16", "severity": "error",
                            "message": f"Thread {sp.get('title') or sid} casts {name}, who is not a character in this story."})
    return out


def world_issues(raw: dict) -> list:
    """World-tab hygiene, under L16 (dangling ids) where an id is involved: a `connected_to`, an
    opening location or a gate `target` naming a location the story doesn't author. Only
    checked when the story authors locations at all - a story with none uses free-text scenes.
    A lore entry id authored twice is an error; lore with no keys and no `also_when` can never
    trigger (warning)."""
    out = []
    locations = (raw.get("world") or {}).get("locations") or {}
    if locations:
        for lid, loc in locations.items():
            for c in (loc or {}).get("connected_to") or []:
                if c not in locations:
                    out.append({"id": "L16", "severity": "error",
                                "message": f"Location {lid} connects to {c}, which is not a location in this story."})
        start = ((raw.get("plot") or {}).get("initial_scene") or {}).get("location")
        if start and start not in locations:
            out.append({"id": "L16", "severity": "error",
                        "message": f"The opening scene is at {start}, which is not a location in this story."})
        gate = (raw.get("mechanics") or {}).get("gate")
        for g in (gate.get("gates") or []) if isinstance(gate, dict) else []:
            if isinstance(g, dict) and g.get("target") and g["target"] not in locations:
                out.append({"id": "L16", "severity": "error",
                            "message": f"Gate {g.get('id', g['target'])} guards {g['target']}, which is not a location in this story."})
    seen, key_owners = set(), {}
    for e in author_model._lore_to_board_entries(raw):
        for k in e.get("keys") or []:
            key_owners.setdefault(str(k).strip().lower(), []).append(e.get("id") or "(no id)")
    for key, owners in key_owners.items():
        if len(key) < 3 or key in _STOPWORDS:
            out.append({"id": "L13", "severity": "warning",
                        "message": f"Lore key \"{key}\" ({', '.join(owners)}) is too generic: it will match almost every scene."})
        elif len(set(owners)) > 1:
            out.append({"id": "L13", "severity": "warning",
                        "message": f"Lore key \"{key}\" is shared by {', '.join(sorted(set(owners)))}: one mention injects all of them."})
    for e in author_model._lore_to_board_entries(raw):
        lid = e.get("id") or "(no id)"
        if lid in seen:
            out.append({"id": "L16", "severity": "error", "message": f"Lore entry {lid} is authored twice."})
        seen.add(lid)
        if not (e.get("keys") or e.get("also_when")):
            out.append({"id": "lore", "severity": "warning",
                        "message": f"Lore entry {lid} has no keys and no also_when, so it can never be injected."})
        if not (e.get("content") or "").strip():
            out.append({"id": "lore", "severity": "warning", "message": f"Lore entry {lid} has no content."})
    return out


def _number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def stat_tier_issues(raw: dict) -> list:
    """L06 and L07, over every axis a `bounded_counter` story seeds (`author_model.
    stat_axis_names`). Read from `raw`, not the board model, because the floor it checks against
    is resolved the engine's way - per axis, falling back to the block (`BoundedCounter.bounds`).

    L06 (warning): an axis with no tiers gives the narrator no band guidance at all (CR-01 tiers
    every axis), and a ladder whose lowest tier sits above the floor leaves the bottom of the
    range with no tier - `tier_for` returns None there and the axis drops out of the prompt.
    L07 (error): an unsorted ladder or a duplicate `at`. The engine sorts before it scans, so an
    unsorted list still plays; it is an error because the ladder the author reads is then not the
    ladder that runs, and with two tiers at one `at` which of them wins is an accident of sort
    stability."""
    stats = (raw.get("mechanics") or {}).get("stats")
    if not isinstance(stats, dict) or stats.get("engine") != "bounded_counter":
        return []
    axes = stats.get("axes") if isinstance(stats.get("axes"), dict) else {}
    labels = (stats.get("readout") or {}).get("labels") or {}
    out = []
    for axis in author_model.stat_axis_names(raw):
        spec = axes.get(axis) if isinstance(axes.get(axis), dict) else {}
        name = labels.get(axis, axis.upper())
        ats = [t.get("at") for t in spec.get("tiers") or [] if isinstance(t, dict) and _number(t.get("at"))]
        if not ats:
            out.append({"id": "L06", "severity": "warning", "axis": axis,
                        "message": f"{name} has no tiers, so the narrator gets no guidance on what "
                                   "its value means."})
            continue
        floor = spec.get("floor", stats.get("floor", 0))
        if _number(floor) and min(ats) != floor:
            out.append({"id": "L06", "severity": "warning", "axis": axis,
                        "message": f"{name}'s lowest tier starts at {min(ats)}, not at its floor "
                                   f"({floor}). Below {min(ats)} it has no tier and drops out of "
                                   "the prompt."})
        if ats != sorted(ats):
            out.append({"id": "L07", "severity": "error", "axis": axis,
                        "message": f"{name}'s tiers are out of order ({', '.join(str(a) for a in ats)}). "
                                   "List them from lowest to highest."})
        dupes = sorted({a for a in ats if ats.count(a) > 1})
        if dupes:
            out.append({"id": "L07", "severity": "error", "axis": axis,
                        "message": f"{name} has more than one tier at {', '.join(str(a) for a in dupes)}. "
                                   "Only one of them can ever be current."})
    return out


def lint(raw: dict, model: dict) -> list:
    """L01, L06/L07 and L10 against `raw`, everything else against `model`
    (`author_model.to_board_model(raw)`). L10 is skipped when the schema already rejected the
    template: a condition that isn't an object at all is L01's finding, and reporting it twice
    in two vocabularies is noise."""
    schema = schema_errors(raw)
    return (schema + ([] if schema else condition_issues(raw)) + flag_issues(raw) + revelation_issues(raw) + world_issues(raw) + thread_cast_issues(raw)
            + stat_tier_issues(raw) + structural_issues(model) + cast_issues(model))


def has_errors(issues: list) -> bool:
    return any(i.get("severity") == "error" for i in issues)

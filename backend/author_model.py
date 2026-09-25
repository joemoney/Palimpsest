"""template.json <-> the storyboard's node/edge model (AUTHORING_TOOL_PHASES.md Phase S1).

Two pure, offline-testable functions - `to_board_model` (load) and `from_board_model`
(save) - and `playable_projection` (decision D1). No Flask, no HTMX, no I/O: the board
route (not yet built) is a thin wrapper that calls `state_store.load_template_raw`,
`to_board_model`, hands the result to the canvas, takes back an edited model, calls
`from_board_model`, then `state_store.write_template`.

**The writer patches, it never regenerates** (CLAUDE.md risk this whole module exists to
avoid: "every lossy template editor loses data by rebuilding from its own model"). Concretely:
every node built by `to_board_model` is seeded with `dict(source)` - a full, verbatim copy of
whatever template.json actually had at that id, including fields no UI edits yet
(`completion_threshold`, `ties_to_main_plot`, an ending's `criteria`/`arc`/`epilogue`...).
`from_board_model` writes that same node straight back, then overlays only the handful of
fields it actually derives or edits (title, description, role, waypoints, and - because
they live on edges, not the node - `starts_active`/`activate_when`/`delivers`). A field the
board has never heard of survives by construction, not by a maintained preserve-list, which
is what makes adding a new UI-editable field later a strictly additive change here.

**Round trip is the correctness bar.** `from_board_model(raw, to_board_model(raw))` must
equal `raw` except for `schema_version` (bumped to `TEMPLATE_SCHEMA_VERSION`) and
`_storyboard.positions` (always rewritten, since the board is the only writer of layout).
`test_author_model.py` checks this against every real story and fixture.

**D1 (`mechanics.endings`/CR-10 fields are staged nowhere - final paths from day one).** This
loader neither knows nor cares whether `ending_funnel` is registered; it reads and writes
`mechanics.endings.entries[]` exactly as authored. `load_template()` (real play) is a
separate call path that still runs `mechanics.validate()` and still raises loudly on an
engine this build doesn't have - see `playable_projection` below for the tool's way around
that refusal.

**D5 (failure endings) - deliberately NOT converted here.** `mechanics.failure_conditions`
entries surface as their own `terminal` node kind, in their *current* shape (`title`,
`trigger` as free prose, `ending_prompt`) - not rewritten into a CR-05 `kind: "terminal"`
ending, because that conversion needs an author to write a real `ready_when` condition
(a `trigger` like "the protagonist spends a scene at WARMTH -10" is not one), and the
condition editor doesn't exist until S2. Converting anyway, with a TODO `ready_when`, would
make the round-trip gate a lie about round-tripping. When S2 ships, promoting a failure
condition to a CR-05 terminal becomes an explicit board action, not something this loader
does silently underfoot.

**Two things this module deliberately does not cover yet**, both still true to fact 2 in
AUTHORING_TOOL_PHASES.md: stat tiers (S3's tier ladder) and `plot.main_thread`/lore/timeline
(Forms tab, S4/S6). Nothing here reads or writes them, so they pass through untouched inside
`raw` the same way any other key this module has never heard of does.
"""
import copy

import conditions

TEMPLATE_SCHEMA_VERSION = 3

# Node/edge keys that are board bookkeeping, never a template field - stripped before any
# dict-copied-from-a-node is written back into a template section.
_NODE_ONLY_KEYS = ("id", "kind", "ekind", "x", "y", "_w", "_h")


def _node_base(source: dict, **extra) -> dict:
    """A full, verbatim copy of `source` (never the caller's dict itself - the board mutates
    nodes freely, and a shared reference would let a canvas edit leak back into `raw` before
    `from_board_model` ever runs), plus the board-standard `id`/`kind`/position fields."""
    node = copy.deepcopy(source)
    node.update(extra)
    node.setdefault("x", 0)
    node.setdefault("y", 0)
    return node


def _strip_node_only(node: dict) -> dict:
    return {k: v for k, v in node.items() if k not in _NODE_ONLY_KEYS}


# ---------------------------------------------------------------------------
# Load: template -> board model
# ---------------------------------------------------------------------------

def to_board_model(raw: dict) -> dict:
    """A template dict (as `state_store.load_template_raw` returns - schema_version 2 or 3,
    both accepted, see `state_store.TEMPLATE_SCHEMA_VERSIONS`) projected into
    `{story, nodes, edges, characters}`, the shape the canvas and the Cast tab render.

    Positions come from `_storyboard.positions` when present; a node the board has never
    placed before gets (0, 0) and the canvas's own auto-layout/fit is what actually shows it
    somewhere sane - this module has no opinion on layout beyond carrying forward what was
    already saved."""
    positions = (raw.get("_storyboard") or {}).get("positions") or {}
    nodes = []
    edges = []

    nodes.append(_start_node(raw))

    subplots = raw.get("plot", {}).get("subplots", {}) or {}
    for sid, sp in subplots.items():
        nodes.append(_thread_node(sid, sp))
        edges.extend(_thread_edges(sid, sp))

    for cond in (raw.get("mechanics", {}).get("failure_conditions", {}) or {}).get("conditions", []) or []:
        nodes.append(_terminal_node(cond))

    endings_cfg = raw.get("mechanics", {}).get("endings", {}) or {}
    for entry in endings_cfg.get("entries", []) or []:
        if entry.get("kind") == "terminal":
            # A terminal authored directly under mechanics.endings (as CR-05 intends, once a
            # story is promoted past D5's failure_conditions shape) - a second terminal
            # source, kept separate from failure_conditions's own loop above rather than
            # merged, so each round-trips back into the section it came from.
            nodes.append(_terminal_node_from_entry(entry))
        else:
            # Delivers edges pointing at this ending were already emitted by _thread_edges,
            # above (delivers lives on the subplot side of the template). A delivers target
            # naming an ending id no live subplot points at is exactly what L16/the health
            # panel's "uncarried waypoint" check exists to flag - nothing for the loader to
            # do about it here.
            nodes.append(_ending_node(entry, len([n for n in nodes if n.get("ekind") == "destination"])))

    for node in nodes:
        pos = positions.get(node["id"])
        if pos:
            node["x"], node["y"] = pos.get("x", 0), pos.get("y", 0)

    result = {
        "story": raw.get("meta", {}).get("title", ""),
        "nodes": nodes,
        "edges": edges,
        "characters": _characters_to_board(raw.get("world", {}).get("characters", {}) or {}),
    }
    # endings_settings: the mechanics.endings block's own config (check_every/budget/
    # steer_top/finale_turns) - story-wide, not per-node, so it has nowhere else in this
    # shape to live. P-2: omitted entirely rather than an empty dict when nothing is authored.
    endings_settings = {k: endings_cfg[k] for k in ("check_every", "budget", "steer_top", "finale_turns")
                         if k in endings_cfg}
    if endings_settings:
        result["endings_settings"] = endings_settings
    # flags_declared: mechanics.flags.declared, the ids a condition may name (CR-02) and the
    # `detect` text that will let the state-update pass set each one. Always present (an empty
    # list when none) so the board has something to add to; from_board_model omits the block
    # again if it is still empty, so P-2 holds on disk.
    flags = (raw.get("mechanics", {}) or {}).get("flags")
    declared = flags.get("declared") if isinstance(flags, dict) else None
    result["flags_declared"] = [
        {"id": f.get("id", ""), "detect": f.get("detect", "")}
        for f in (declared or []) if isinstance(f, dict)
    ]
    result["refs"] = _condition_refs(raw)
    return result


def _condition_refs(raw: dict) -> dict:
    """Read-only reference data for the board's condition builder dropdowns: what a condition
    can legally name. Never written back (from_board_model ignores it) - it exists so the
    builder offers real axes/revelations instead of a free-text box where a typo reads as an
    unknown referent (L10)."""
    mechanics = raw.get("mechanics", {}) or {}
    stats = list(((mechanics.get("stats") or {}).get("axes") or {}).keys())
    for axis in (raw.get("protagonist", {}) or {}).get("stats", {}) or {}:
        if axis not in stats:
            stats.append(axis)
    revelations = mechanics.get("revelations")
    entries = revelations.get("entries", []) if isinstance(revelations, dict) else []
    flags = ((mechanics.get("flags") or {}).get("declared") or []) if isinstance(mechanics.get("flags"), dict) else []
    return {
        "stats": stats,
        "revelations": [e.get("id") for e in entries if isinstance(e, dict) and e.get("id")],
        "flags": [f.get("id") for f in flags if isinstance(f, dict) and f.get("id")],
    }


def _start_node(raw: dict) -> dict:
    creation = raw.get("character_creation", []) or []
    opening = raw.get("plot", {}).get("opening_scene", {}) or {}
    variants = []
    if creation:
        # The board shows the *first* creation step's option labels as chips (the prototype's
        # "Starting choices"), which is a display simplification for a story that may have
        # several steps - character_creation itself is untouched by this loader either way,
        # since editing more than the first step's chips is Forms-tab (S6) territory.
        variants = [opt.get("name", opt.get("id", "")) for opt in creation[0].get("options", []) or []]
    return _node_base(
        {},
        id="start", kind="start",
        title=raw.get("meta", {}).get("title", "Start"),
        theme=raw.get("meta", {}).get("synopsis", ""),
        variants=variants,
        narration_before_name=opening.get("narration_before_name", ""),
        narration_after_name=opening.get("narration_after_name", ""),
    )


def _thread_node(sid: str, sp: dict) -> dict:
    # starts_active/activate_when/delivers are edge-derived once an edge exists for this
    # subplot (see _thread_edges and _apply_subplots) - but a subplot with none of the three,
    # e.g. `starts_active: false` and no `activate_when` authored at all (the health panel's
    # "never becomes active" case - stories/example's subplot_002, for real), has no edge to
    # derive from. The node keeps them as an ordinary pass-through field for exactly that
    # case: _apply_subplots only ever *overrides* these fields when an edge says so, so an
    # explicit `false` with no edge survives the round trip instead of silently disappearing.
    return _node_base(sp, id=sid, kind="subplot", title=sp.get("title", ""),
                       theme=sp.get("description", ""), role=sp.get("role", "spine"))


def _thread_edges(sid: str, sp: dict) -> list:
    edges = []
    if sp.get("starts_active"):
        edges.append({"type": "opens", "from": "start", "to": sid})
    elif sp.get("activate_when"):
        # CR-10's condition shape isn't rendered as plain English until S2's condition editor
        # exists; for now the raw condition dict is carried as the edge's `cond_raw` and a
        # best-effort string goes in `cond` purely for the edge label. Neither is lossy: the
        # writer always regenerates activate_when from `cond_raw` when present.
        edges.append({"type": "unlocks", "from": "start", "to": sid,
                       "cond": _condition_label(sp["activate_when"]), "cond_raw": sp["activate_when"]})
    for target in sp.get("delivers", []) or []:
        if "." not in target:
            continue
        ending_id, wp_id = target.split(".", 1)
        edges.append({"type": "delivers", "from": sid, "to": ending_id, "wp": wp_id})
    return edges


def _condition_label(cond) -> str:
    """Edge label for a condition loaded from disk: `conditions.describe`, shortened to fit on
    a canvas edge, never round-tripped from. The board's client-side labeller (`condLabel` in
    author_board.html) covers an edit the server hasn't seen yet."""
    if not isinstance(cond, dict) or not cond:
        return "condition"
    if len(cond) == 1 and "condition" in cond:  # legacy free-text shape, not CR-02 grammar
        return str(cond["condition"])
    text = conditions.describe(cond)
    return text if len(text) <= 56 else text[:53] + "..."


def _terminal_node(cond: dict) -> dict:
    """failure_conditions' current shape (D5 deferred - see module docstring): title/trigger
    (free prose)/ending_prompt, not yet a CR-05 `ready_when`."""
    return _node_base(
        cond, id=cond.get("id", ""), kind="ending", ekind="terminal",
        title=cond.get("title", ""), theme=cond.get("ending_prompt", ""),
        trigger=cond.get("trigger", ""),
    )


def _terminal_node_from_entry(entry: dict) -> dict:
    """A CR-05 terminal already authored under mechanics.endings (post-D5-promotion). Kept
    as a distinct constructor from `_terminal_node` (different source shape entirely) even
    though both produce the same node kind, so each writes back into the section it read
    from - see `from_board_model`."""
    arc = entry.get("arc") or {}
    return _node_base(
        entry, id=entry.get("id", ""), kind="ending", ekind="terminal", source="endings",
        title=entry.get("name", arc.get("title", "")), theme=entry.get("epilogue", ""),
        trigger=_condition_label(entry.get("ready_when")) if entry.get("ready_when") else "",
    )


def _ending_node(entry: dict, index: int) -> dict:
    # Colour is a display hint, never persisted (see _apply_endings, which pops it before
    # writing) - assigned by position among destination entries, matching the prototype's
    # own `addEnding` convention (`cols[…length % 4]`) exactly, and deliberately NOT by
    # hashing the id: Python's str hash() is salted per process (PYTHONHASHSEED), so the
    # same ending would get a different colour on every reload - display-only, not a
    # round-trip bug, but exactly the "same content, different bytes" class of bug this
    # codebase has hit before (CLAUDE.md, _existing_character_names's unsorted-set bug) and
    # is worth not reintroducing even where it happens to be harmless.
    cols = ["e1", "e2", "e3", "e4"]
    waypoints = [
        {"id": w.get("id", ""), "plant": w.get("plant", ""), "detect": w.get("detect", ""),
         "done_when": w.get("done_when")}
        for w in entry.get("waypoints", []) or []
    ]
    return _node_base(
        entry, id=entry.get("id", ""), kind="ending", ekind="destination",
        title=entry.get("name", ""), theme=entry.get("_theme", ""),
        color=cols[index % len(cols)],
        catchAll="viable_while" not in entry,
        waypoints=waypoints,
    )


def _characters_to_board(characters: dict) -> list:
    out = []
    for name, c in characters.items():
        canon = c.get("canon")
        canon_rows = [{"k": k, "v": v} for k, v in canon.items()] if isinstance(canon, dict) else []
        out.append({
            "name": c.get("name", name), "role": c.get("role", ""),
            "description": c.get("description", ""), "first_contact": c.get("first_contact", ""),
            "hook": c.get("hook", ""), "canon": canon_rows,
        })
    return out


# ---------------------------------------------------------------------------
# Save: board model -> template
# ---------------------------------------------------------------------------

def from_board_model(raw: dict, model: dict) -> dict:
    """Patches a deep copy of `raw` with everything the board owns (see module docstring for
    the "full copy, then overlay only what's derived" contract) and returns it -
    `state_store.write_template` is the caller that actually writes it to disk. Never
    mutates `raw` or `model`."""
    out = copy.deepcopy(raw)
    out["schema_version"] = TEMPLATE_SCHEMA_VERSION

    nodes = model.get("nodes", [])
    edges = model.get("edges", [])

    _apply_subplots(out, nodes, edges)
    _apply_terminals(out, nodes)
    _apply_endings(out, nodes, model.get("endings_settings"))
    _apply_flags(out, model.get("flags_declared"))
    _apply_characters(out, model.get("characters"))
    _apply_positions(out, nodes)

    return out


def _apply_subplots(out: dict, nodes: list, edges: list) -> None:
    thread_nodes = [n for n in nodes if n.get("kind") == "subplot"]
    plot = out.setdefault("plot", {})
    # P-2/P-4: plot.subplots is optional - a single-thread story (courtroom, one-room
    # horror) authors none, and this must not introduce an empty dict where there was no
    # key at all. Only touch it if there's a thread to add, or one already existed to prune.
    if not thread_nodes and "subplots" not in plot:
        return
    subplots = plot.setdefault("subplots", {})

    live_ids = {n["id"] for n in thread_nodes}
    for sid in list(subplots):
        if sid not in live_ids:
            del subplots[sid]

    # Which of the three edge-derived fields each subplot actually has an edge for, so the
    # loop below overrides only what an edge says something about - a subplot with none of
    # the three keeps whatever it already had (see _thread_node) rather than losing it.
    # Filtered against live_ids (every thread node on the board), not `subplots` - a
    # brand-new thread's dict entry doesn't exist yet at this point (it's created by
    # `setdefault` in the loop below), so filtering against `subplots` here would silently
    # drop an opens/unlocks/delivers edge attached to a thread the author just added.
    opens_to = {e["to"] for e in edges if e.get("type") == "opens" and e.get("to") in live_ids}
    unlocks = {}
    for e in edges:
        if e.get("type") == "unlocks" and e.get("to") in live_ids:
            unlocks.setdefault(e["to"], []).append(e)
    delivers = {}
    for e in edges:
        if e.get("type") == "delivers" and e.get("wp") and e.get("from") in live_ids:
            delivers.setdefault(e["from"], []).append(f"{e['to']}.{e['wp']}")

    for n in thread_nodes:
        sp = subplots.setdefault(n["id"], {})
        sp.update(_strip_node_only(n))
        sp.pop("theme", None)
        sp["title"] = n.get("title", "")
        sp["description"] = n.get("theme", "")
        role = n.get("role")
        if role and role != "spine":
            sp["role"] = role
        else:
            sp.pop("role", None)

        sid = n["id"]
        if sid in opens_to:
            sp["starts_active"] = True
            sp.pop("activate_when", None)
        elif sid in unlocks:
            # Only real CR-02 grammar is ever written. An unlocks edge with no condition yet
            # contributes nothing (author_lint flags it) rather than a {"condition": "TODO"}
            # placeholder, which is not grammar and fails L01 the moment it is saved.
            conds = [e["cond_raw"] for e in unlocks[sid] if e.get("cond_raw")]
            if conds:
                sp["activate_when"] = conds[0] if len(conds) == 1 else {"any": conds}
                sp.pop("starts_active", None)
        # else: no opens/unlocks edge for this subplot - starts_active/activate_when, if the
        # node carried either as a pass-through field, are left exactly as loaded.

        if sid in delivers:
            sp["delivers"] = delivers[sid]
        else:
            sp.pop("delivers", None)


def _apply_terminals(out: dict, nodes: list) -> None:
    """failure_conditions-sourced terminals only - a node whose source is `mechanics.endings`
    (`source: "endings"`, stamped by `_terminal_node_from_entry`) is handled by
    `_apply_endings` instead, so the two never fight over the same node."""
    term_nodes = [n for n in nodes if n.get("kind") == "ending" and n.get("ekind") == "terminal"
                  and n.get("source") != "endings"]
    if not term_nodes and "failure_conditions" not in out.get("mechanics", {}):
        return
    mechanics = out.setdefault("mechanics", {})
    fc = mechanics.setdefault("failure_conditions", {})
    fc.setdefault("engine", "triggered_ending")
    conditions = []
    for n in term_nodes:
        cond = _strip_node_only(n)
        cond.pop("theme", None)
        cond.pop("trigger", None)
        cond["id"] = n["id"]
        cond["title"] = n.get("title", "")
        cond["trigger"] = n.get("trigger", "")
        cond["ending_prompt"] = n.get("theme", "")
        conditions.append(cond)
    fc["conditions"] = conditions
    if not conditions and not fc.get("_authored"):
        mechanics.pop("failure_conditions", None)


def _apply_flags(out: dict, declared) -> None:
    """Write the board's declared-flag list to `mechanics.flags.declared`. `None` means the
    board sent no such key (an older client): leave the template alone. An entry keeps whatever
    else the template already carried for that id (an author-only `_note`, say) - the board only
    ever owns `id` and `detect`. A blank id is dropped, and an emptied list removes the block
    again, so an absent module stays absent (P-2)."""
    if not isinstance(declared, list):
        return
    mechanics = out.get("mechanics")
    block = mechanics.get("flags") if isinstance(mechanics, dict) else None
    block = block if isinstance(block, dict) else {}
    existing = {f.get("id"): f for f in block.get("declared") or [] if isinstance(f, dict)}
    entries = []
    for f in declared:
        fid = (f.get("id") or "").strip() if isinstance(f, dict) else ""
        if not fid:
            continue
        entry = dict(existing.get(fid, {}))
        entry["id"] = fid
        detect = (f.get("detect") or "").strip()
        if detect:
            entry["detect"] = detect
        else:
            entry.pop("detect", None)
        entries.append(entry)
    if not entries and not block:
        return  # nothing declared and nothing to remove: leave `mechanics` exactly as it was
    mechanics = out.setdefault("mechanics", {})
    if entries:
        block["declared"] = entries
        mechanics["flags"] = block
    else:
        block.pop("declared", None)
        if block:
            mechanics["flags"] = block
        else:
            mechanics.pop("flags", None)


def _apply_endings(out: dict, nodes: list, settings: dict = None) -> None:
    dest_nodes = [n for n in nodes if n.get("kind") == "ending" and n.get("ekind") == "destination"]
    term_nodes = [n for n in nodes if n.get("kind") == "ending" and n.get("ekind") == "terminal"
                  and n.get("source") == "endings"]
    if not dest_nodes and not term_nodes and not settings:
        return
    mechanics = out.setdefault("mechanics", {})
    endings_cfg = mechanics.setdefault("endings", {})
    endings_cfg.setdefault("engine", "ending_funnel")

    # The block's own config (check_every/budget/steer_top/finale_turns) - story-wide, not
    # per-entry. Only ever overlays keys the board actually sent; a key it's never heard of
    # (none exist yet, but the same "patch, don't regenerate" discipline as everything else
    # in this module) survives because nothing here touches it.
    for k in ("check_every", "budget", "steer_top", "finale_turns"):
        if settings and k in settings:
            endings_cfg[k] = settings[k]

    entries = []
    for n in dest_nodes:
        entry = _strip_node_only(n)
        for k in ("title", "theme", "color", "catchAll", "waypoints"):
            entry.pop(k, None)
        entry["id"] = n["id"]
        entry["kind"] = "destination"
        entry["name"] = n.get("title", "")
        if n.get("theme"):
            entry["_theme"] = n["theme"]
        entry["waypoints"] = [
            {k: v for k, v in {
                "id": w.get("id", ""), "plant": w.get("plant", ""),
                "detect": w.get("detect") or None, "done_when": w.get("done_when"),
            }.items() if v is not None}
            for w in (n.get("waypoints") or [])
        ]
        entries.append(entry)
    for n in term_nodes:
        entry = _strip_node_only(n)
        for k in ("title", "theme", "trigger", "source"):
            entry.pop(k, None)
        entry["id"] = n["id"]
        entry["kind"] = "terminal"
        entry.setdefault("arc", {})
        if n.get("title"):
            entry["arc"]["title"] = n["title"]
        if n.get("theme"):
            entry["epilogue"] = n["theme"]
        entries.append(entry)
    endings_cfg["entries"] = entries


def _apply_characters(out: dict, characters) -> None:
    if characters is None:
        return
    world = out.setdefault("world", {})
    chars = {}
    for c in characters:
        name = (c.get("name") or "").strip()
        if not name:
            continue
        entry = {"name": name}
        for k in ("description", "role", "first_contact", "hook"):
            if (c.get(k) or "").strip():
                entry[k] = c[k]
        canon_rows = [row for row in (c.get("canon") or []) if (row.get("k") or "").strip()]
        if canon_rows:
            entry["canon"] = {row["k"]: row.get("v", "") for row in canon_rows}
        chars[name] = entry
    if chars:
        world["characters"] = chars
    else:
        world.pop("characters", None)


def _apply_positions(out: dict, nodes: list) -> None:
    # Author-only, engine-ignored (CR-03) - CLAUDE.md's "an absent optional module means the
    # feature does not exist" applies here too: no nodes, no _storyboard key at all, rather
    # than one holding an empty positions dict.
    if not nodes:
        out.pop("_storyboard", None)
        return
    storyboard = out.setdefault("_storyboard", {})
    storyboard["positions"] = {
        n["id"]: {"x": n.get("x", 0), "y": n.get("y", 0)} for n in nodes
    }


# ---------------------------------------------------------------------------
# D1: a playable projection for preview/playtest, independent of what mechanics.validate()
# will accept
# ---------------------------------------------------------------------------

def playable_projection(raw: dict, registered_engines) -> tuple:
    """`raw` with every `mechanics.<slot>` block whose declared engine isn't in
    `registered_engines` removed, plus the list of (slot, engine) pairs that were left out.
    `registered_engines` is `set(mechanics.registered_engines())` from the real registry -
    passed in rather than imported, so this module stays free of the `mechanics` package
    import and stays testable with a synthetic registry.

    This is what S1's preview/playtest build a ctx from instead of `state_store.
    load_template()`, which is deliberately not softened (D1): a template naming an engine
    this build doesn't have is loud everywhere except here, where the omission is shown
    rather than hidden."""
    projected = copy.deepcopy(raw)
    left_out = []
    mech = projected.get("mechanics", {}) or {}
    for slot, cfg in list(mech.items()):
        if isinstance(cfg, dict) and cfg.get("engine") and (slot, cfg["engine"]) not in registered_engines:
            left_out.append((slot, cfg["engine"]))
            del mech[slot]
    return projected, left_out

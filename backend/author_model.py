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

**Stat tiers (S3)** round-trip through `stat_axes`: one entry per seeded axis, carrying that
axis's `mechanics.stats.axes.<axis>.tiers` list verbatim. The writer only touches an axis whose
tier list actually changed, so an unedited axis - including an authored empty `tiers: []` -
survives byte for byte.

**Not covered yet:** `plot.main_thread`/lore (Forms tab, S4/S6). Nothing here reads or writes
them, so they pass through untouched inside `raw` the same way any other key this module has
never heard of does.
"""
import copy

import conditions

TEMPLATE_SCHEMA_VERSION = 3

# Node/edge keys that are board bookkeeping, never a template field - stripped before any
# dict-copied-from-a-node is written back into a template section.
_NODE_ONLY_KEYS = ("id", "kind", "ekind", "x", "y", "_w", "_h")


def _node_base(entry: dict, /, **extra) -> dict:
    """A full, verbatim copy of `entry` (never the caller's dict itself - the board mutates
    nodes freely, and a shared reference would let a canvas edit leak back into `raw` before
    `from_board_model` ever runs), plus the board-standard `id`/`kind`/position fields.

    Positional-only, so any node field can be passed through `extra` - including one called
    `source`, which a terminal authored under mechanics.endings carries (`source="endings"`).
    With a keyword-capable first parameter of that name, every such story crashed the board on
    load; no real story had one until The Missing Core's "Open to the Belt"."""
    node = copy.deepcopy(entry)
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

    names = conditions.display_names(raw)
    subplots = raw.get("plot", {}).get("subplots", {}) or {}
    for sid, sp in subplots.items():
        nodes.append(_thread_node(sid, sp))
        edges.extend(_thread_edges(sid, sp, names))

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
    # revelations: mechanics.revelations entries for the Fragments tab. Always present (an
    # empty list when none) so the board has something to add to; from_board_model leaves the
    # block untouched when the list comes back unchanged, and removes it when emptied (P-2).
    result["revelations"] = _revelations_to_board(raw)
    # meta / world / lore: the World tab. Always present, so the board has somewhere to type;
    # each is written back only when it differs from what the template holds.
    result["meta"] = _meta_to_board(raw)
    result["world"] = _world_to_board(raw)
    result["lore"] = _lore_to_board(raw)
    result["main_thread"] = _main_thread_to_board(raw)
    # CR-11: the Cast tab's bond grid and the Side threads tab edit these two blocks as they
    # stand in the template (None when absent - P-2: the board creates one only when asked).
    for key in CR11_BLOCKS:
        block = (raw.get("mechanics") or {}).get(key)
        result[key] = copy.deepcopy(block) if isinstance(block, dict) else None
    # CR-04: the top-level `derived` rules, as the template holds them ([] when absent).
    result["derived"] = copy.deepcopy(raw.get("derived")) if isinstance(raw.get("derived"), list) else []
    result["forms"] = _forms_to_board(raw)
    result["form_sections"] = [list(s) for s in FORM_SECTIONS]  # read-only: the tab's section list
    result["refs"] = _condition_refs(raw)
    # stat_axes: the tier ladder's data (S3). P-2: omitted when the story has no
    # mechanics.stats block at all - there is no ladder to draw for stats that don't exist.
    stat_axes = _stats_to_board(raw)
    if stat_axes is not None:
        result["stat_axes"] = stat_axes
    return result


def stat_axis_names(raw: dict) -> list:
    """Every stat axis a save of this story can hold, in authored order: `mechanics.stats.axes`
    first, then `protagonist.stats`, then any `character_creation` `starting_stats` - the same
    set `conditions._stat_axes` accepts, ordered so the board lists them stably. CLAUDE.md:
    those are the only seeding sources, and the model can never add an axis."""
    names = list((((raw.get("mechanics") or {}).get("stats") or {}).get("axes") or {}).keys())
    sources = [(raw.get("protagonist") or {}).get("stats") or {}]
    for step in raw.get("character_creation") or []:
        for opt in step.get("options") or []:
            sources.append(opt.get("starting_stats") or {})
    for source in sources:
        for axis in source:
            if axis not in names:
                names.append(axis)
    return names


def _stats_to_board(raw: dict):
    """One entry per seeded axis: `{axis, label, floor, ceiling, tiers}`. `label`, `floor` and
    `ceiling` are read-only display data for the ladder's scale (resolved the way
    `BoundedCounter.bounds` resolves them - per axis, falling back to the block); only `tiers`
    is ever written back. None when there is no stats block."""
    stats = (raw.get("mechanics") or {}).get("stats")
    if not isinstance(stats, dict):
        return None
    axes = stats.get("axes") or {}
    labels = (stats.get("readout") or {}).get("labels") or {}
    out = []
    for axis in stat_axis_names(raw):
        spec = axes.get(axis) or {}
        out.append({
            "axis": axis,
            "label": labels.get(axis, axis.upper()),
            "floor": spec.get("floor", stats.get("floor", 0)),
            "ceiling": spec.get("ceiling", stats.get("ceiling")),
            "tiers": copy.deepcopy(spec.get("tiers") or []),
        })
    return out


def _condition_refs(raw: dict) -> dict:
    """Read-only reference data for the board's condition builder dropdowns: what a condition
    can legally name. Never written back (from_board_model ignores it) - it exists so the
    builder offers real axes/revelations instead of a free-text box where a typo reads as an
    unknown referent (L10)."""
    mechanics = raw.get("mechanics", {}) or {}
    stats = stat_axis_names(raw)
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


def _thread_edges(sid: str, sp: dict, names=None) -> list:
    edges = []
    if sp.get("starts_active"):
        edges.append({"type": "opens", "from": "start", "to": sid})
    elif sp.get("activate_when"):
        # CR-10's condition shape isn't rendered as plain English until S2's condition editor
        # exists; for now the raw condition dict is carried as the edge's `cond_raw` and a
        # best-effort string goes in `cond` purely for the edge label. Neither is lossy: the
        # writer always regenerates activate_when from `cond_raw` when present.
        edges.append({"type": "unlocks", "from": "start", "to": sid,
                       "cond": _condition_label(sp["activate_when"], names), "cond_raw": sp["activate_when"]})
    for target in sp.get("delivers", []) or []:
        if "." not in target:
            continue
        ending_id, wp_id = target.split(".", 1)
        edges.append({"type": "delivers", "from": sid, "to": ending_id, "wp": wp_id})
    return edges


def _condition_label(cond, names=None) -> str:
    """Edge label for a condition loaded from disk: `conditions.describe`, shortened to fit on
    a canvas edge, never round-tripped from. The board's client-side labeller (`condLabel` in
    author_board.html) covers an edit the server hasn't seen yet."""
    if not isinstance(cond, dict) or not cond:
        return "condition"
    if len(cond) == 1 and "condition" in cond:  # legacy free-text shape, not CR-02 grammar
        return str(cond["condition"])
    text = conditions.describe(cond, names=names)
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


def _revelation_entries(raw: dict) -> list:
    """The authored fragment entries, from either the v3 `{engine, entries}` shape or the v2
    bare list (still readable here, though the schema rejects it on save)."""
    block = (raw.get("mechanics") or {}).get("revelations")
    entries = block.get("entries") if isinstance(block, dict) else block
    return [e for e in entries or [] if isinstance(e, dict)]


def _revelations_to_board(raw: dict) -> list:
    """One row per fragment: `id`, `title` (author-only; `_title` is read too), `trigger` (what the
    state-update pass watches for), `content` (what the narrator is given once revealed),
    `after` (fragments that must be revealed first). `orig` is the id as loaded, so a renamed
    fragment still patches its own entry on save rather than being rebuilt."""
    return [{
        "orig": e.get("id", ""), "id": e.get("id", ""), "title": e.get("title") or e.get("_title", ""),
        "trigger": e.get("trigger", ""), "content": e.get("content", ""),
        "after": list(e.get("after") or []),
    } for e in _revelation_entries(raw)]


META_FIELDS = ("title", "synopsis", "genre", "tone")


def _meta_to_board(raw: dict) -> dict:
    meta = raw.get("meta") or {}
    out = {k: meta.get(k, "") for k in META_FIELDS}
    out["content_rules"] = list(meta.get("content_rules") or [])
    return out


def _world_to_board(raw: dict) -> dict:
    """`world` minus characters (the Cast tab owns those), with locations and factions as
    ordered rows carrying `orig` - the key as loaded - so a renamed one patches its own entry
    and a location rename can follow through to what names it. `start_location` is
    `plot.initial_scene.location`, edited here because it must be one of these locations."""
    world = raw.get("world") or {}
    locations = world.get("locations") or {}
    factions = world.get("factions") or {}
    return {
        "setting_summary": world.get("setting_summary", ""),
        "rules": list(world.get("rules") or []),
        "locations": [{"orig": lid, "id": lid, "name": l.get("name", ""), "description": l.get("description", ""),
                       "connected_to": list(l.get("connected_to") or [])}
                      for lid, l in locations.items() if isinstance(l, dict)],
        "factions": [{"orig": fid, "id": fid, "name": f.get("name", ""), "goals": f.get("goals", ""),
                      "relationship_to_player": f.get("relationship_to_player", "")}
                     for fid, f in factions.items() if isinstance(f, dict)],
        "start_location": ((raw.get("plot") or {}).get("initial_scene") or {}).get("location", ""),
    }


# The Forms tab: every template section with no dedicated editor elsewhere on the board, as
# (path, label). Each is carried whole - the page renders its form from the schema - and written
# back whole when changed. Order is the tab's section order.
FORM_SECTIONS = (
    ("narration", "Narration"),
    ("protagonist", "Protagonist"),
    ("character_creation", "Character creation"),
    ("plot.opening_scene", "Opening narration"),
    ("plot.initial_scene", "Opening scene"),
    ("plot.pacing", "Pacing"),
    ("mechanics.stats", "Stats"),
    ("mechanics.relationships", "Relationships"),
    ("mechanics.inventory", "Inventory"),
    ("mechanics.subplots", "Thread progress"),
    ("mechanics.pacing_loop", "Pacing loop"),
    ("mechanics.progression", "Progression"),
    ("mechanics.gate", "Gates"),
    ("mechanics.tracked_entity", "Tracked entity"),
)


def _get_path(raw: dict, path: str):
    node = raw
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _forms_to_board(raw: dict) -> dict:
    """`{path: subtree or None}` for every FORM_SECTIONS path. None means the template doesn't
    author that section (P-2: the form offers to add it, and never writes an empty one)."""
    return {path: copy.deepcopy(_get_path(raw, path)) for path, _ in FORM_SECTIONS}


def _apply_forms(out: dict, raw: dict, forms) -> None:
    """Write back each Forms-tab section the author changed; an unchanged one is not touched.
    None (or an emptied object/list) removes the section. Runs before every other applier, so the
    tabs that own part of a section - the tier ladder's `mechanics.stats.axes.*.tiers`, the World
    tab's `plot.initial_scene.location` - still have the last word on their part."""
    if not isinstance(forms, dict):
        return
    for path, _ in FORM_SECTIONS:
        if path not in forms:
            continue
        value = forms[path]
        if value == _get_path(raw, path):
            continue
        *parents, leaf = path.split(".")
        target = out
        for part in parents:
            target = target.setdefault(part, {})
        if value in (None, {}, []):
            target.pop(leaf, None)
        else:
            target[leaf] = copy.deepcopy(value)


def _main_thread_to_board(raw: dict) -> dict:
    """`plot.main_thread` for the Diagram tab's acts strip: the main plot's title/description,
    `max_acts`, and the authored acts in order. `orig` is each act's index as loaded, so an edited
    act patches its own entry (keeping `requires` and anything else the strip has no editor for)."""
    mt = (raw.get("plot") or {}).get("main_thread") or {}
    return {
        "title": mt.get("title", ""), "description": mt.get("description", ""),
        "plot_notes": mt.get("plot_notes", ""), "max_acts": mt.get("max_acts"),
        "acts": [{"orig": i, "title": a.get("title", ""), "description": a.get("description", ""),
                  "completion_signals": list(a.get("completion_signals") or []),
                  "requires": copy.deepcopy(a.get("requires"))}
                 for i, a in enumerate(mt.get("acts") or []) if isinstance(a, dict)],
    }


LORE_FIELDS = ("priority", "keys", "also_when", "unlock", "sticky_turns", "content")


def _lore_to_board(raw: dict) -> dict:
    block = (raw.get("mechanics") or {}).get("lore")
    block = block if isinstance(block, dict) else {}
    entries = [e for e in block.get("entries") or [] if isinstance(e, dict)]
    return {
        "max_active": block.get("max_active"),
        "entries": [{"orig": e.get("id", ""), "id": e.get("id", ""),
                     **{k: copy.deepcopy(e.get(k)) for k in LORE_FIELDS if k in e}}
                    for e in entries],
    }


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

    _apply_forms(out, raw, model.get("forms"))
    _apply_subplots(out, nodes, edges)
    _apply_terminals(out, nodes)
    _apply_endings(out, nodes, model.get("endings_settings"))
    _apply_flags(out, model.get("flags_declared"))
    _apply_stat_tiers(out, model.get("stat_axes"))
    _apply_characters(out, model.get("characters"))
    _apply_revelations(out, raw, model.get("revelations"))
    _apply_meta(out, raw, model.get("meta"))
    _apply_world(out, raw, model.get("world"))
    _apply_lore(out, raw, model.get("lore"))
    _apply_main_thread(out, raw, model.get("main_thread"))
    for key in CR11_BLOCKS:
        if key in model:
            _apply_cr11_block(out, raw, key, model[key])
    if "derived" in model:
        _apply_derived(out, raw, model["derived"])
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

        # The thread's authored cast: character names, kept in the order the author ticked them.
        # An empty list is absent, not [] (P-2).
        cast = [c for c in (n.get("cast") or []) if isinstance(c, str) and c.strip()]
        if cast:
            sp["cast"] = cast
        else:
            sp.pop("cast", None)


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


def _apply_stat_tiers(out: dict, stat_axes) -> None:
    """Write each axis's tier list back to `mechanics.stats.axes.<axis>.tiers`. `None` (an older
    client) leaves the template alone, and so does a story with no stats block - the board never
    invents one. An axis whose list is unchanged is not touched at all, which is what keeps an
    authored `tiers: []` (or an axis entry the board has no opinion on) byte-identical.

    Tiers are written verbatim, in the order the board sent them: sorting is the engine's job
    (`BoundedCounter.tiers`), and an unsorted list is L07's to report, not this writer's to hide.
    Emptying an axis's ladder removes the `tiers` key, then the axis entry and the `axes` block if
    that left them empty - P-2, an absent module stays absent."""
    if not isinstance(stat_axes, list):
        return
    stats = (out.get("mechanics") or {}).get("stats")
    if not isinstance(stats, dict):
        return
    for entry in stat_axes:
        if not isinstance(entry, dict) or not entry.get("axis") or not isinstance(entry.get("tiers"), list):
            continue
        axis = entry["axis"]
        tiers = [t for t in entry["tiers"] if isinstance(t, dict)]
        axes = stats.get("axes") if isinstance(stats.get("axes"), dict) else None
        spec = axes.get(axis) if axes is not None and isinstance(axes.get(axis), dict) else None
        if tiers == ((spec or {}).get("tiers") or []):
            continue
        if tiers:
            if axes is None:
                axes = stats["axes"] = {}
            axes.setdefault(axis, {})["tiers"] = copy.deepcopy(tiers)
        else:
            spec.pop("tiers", None)
            if not spec:
                del axes[axis]
            if not axes:
                del stats["axes"]


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
        entries.append(_ordered_like(entry, n))
    for n in term_nodes:
        entry = _strip_node_only(n)
        for k in ("title", "theme", "trigger", "source"):
            entry.pop(k, None)
        entry["id"] = n["id"]
        entry["kind"] = "terminal"
        # The title goes back where _terminal_node_from_entry read it from: `name` when the entry
        # authors one, else `arc.title`. Always writing arc.title invented an `arc` for a terminal
        # that authors only a name.
        if n.get("title"):
            if "name" in entry:
                entry["name"] = n["title"]
            else:
                entry.setdefault("arc", {})["title"] = n["title"]
        if n.get("theme"):
            entry["epilogue"] = n["theme"]
        entries.append(_ordered_like(entry, n))
    endings_cfg["entries"] = entries


def _ordered_like(entry: dict, node: dict) -> dict:
    """`entry` with its keys in the order the node carried them - which is the order the template
    authored them, since a node starts as a copy of its entry (`_node_base`). `_strip_node_only`
    drops `id` and `kind` and the writers re-add them, which would otherwise move them to the end
    and break the byte-identical round trip for any entry that doesn't author them last."""
    order = [k for k in node if k in entry]
    return {**{k: entry[k] for k in order}, **{k: v for k, v in entry.items() if k not in order}}


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


def _apply_revelations(out: dict, raw: dict, rows) -> None:
    """Patch `mechanics.revelations` from the Fragments tab. Untouched when the rows are what
    the template already holds (the byte-identical round trip). Otherwise each row patches a
    deep copy of its original entry (matched by `orig`), so an authored field the tab has no
    editor for survives; a new row starts empty. A blank title or `after` removes the key
    rather than writing an empty value, and a row with no id is dropped, as a nameless
    character is.

    A new block is declared with `triggered_reveal` (declare-to-bind: fragments with no engine
    would sit inert). An existing block keeps whatever engine it has. Emptying the list removes
    the block: an engine declared with no entries raises at load, and an absent module is
    simply absent (P-2)."""
    if rows is None or rows == _revelations_to_board(raw):
        return
    originals = {e.get("id"): e for e in _revelation_entries(raw)}
    entries = []
    for row in rows:
        fid = (row.get("id") or "").strip()
        if not fid:
            continue
        entry = copy.deepcopy(originals.get(row.get("orig"), {}))
        entry["id"] = fid
        entry["trigger"] = row.get("trigger", "")
        entry["content"] = row.get("content", "")
        after = [a for a in row.get("after") or [] if a]
        if after:
            entry["after"] = after
        else:
            entry.pop("after", None)
        # `title` is the schema's author-only field; `_title` is the board's earlier spelling,
        # still read, and replaced by `title` the first time an edited fragment is saved.
        entry.pop("_title", None)
        if (row.get("title") or "").strip():
            entry["title"] = row["title"].strip()
        else:
            entry.pop("title", None)
        entries.append(entry)
    mechanics = out.setdefault("mechanics", {})
    if not entries:
        mechanics.pop("revelations", None)
        return
    block = mechanics.get("revelations")
    if not isinstance(block, dict):
        block = mechanics["revelations"] = {"engine": "triggered_reveal"}
    block["entries"] = entries


def _set_or_drop(target: dict, key: str, value) -> None:
    """Write `value` under `key`, or remove the key when the value is blank - an optional field
    left empty is absent, not an empty string (P-2)."""
    if value in ("", None, []) or (isinstance(value, str) and not value.strip()):
        target.pop(key, None)
    else:
        target[key] = value


def _apply_meta(out: dict, raw: dict, meta) -> None:
    """`meta` from the World tab. `title` is required by the schema, so it is always written;
    the rest are dropped when blank. Untouched when unchanged."""
    if meta is None or meta == _meta_to_board(raw):
        return
    target = out.setdefault("meta", {})
    target["title"] = meta.get("title", "")
    for k in ("synopsis", "genre", "tone"):
        _set_or_drop(target, k, meta.get(k, ""))
    _set_or_drop(target, "content_rules", [r for r in meta.get("content_rules") or [] if r.strip()])


def _apply_world(out: dict, raw: dict, world) -> None:
    """`world` (bar characters) from the World tab, plus `plot.initial_scene.location`.

    Locations and factions patch deep copies of their original entries (matched by `orig`), so
    an authored field the tab has no editor for survives. A renamed location follows through
    everywhere a location id is written: every `connected_to`, the opening location, and
    `mechanics.gate.gates[].target` - a gate left naming the old id would stop gating anything
    (a gate on an unknown location fails open). A row with a blank id is dropped. An emptied
    list removes its key (P-2); `setting_summary` and `rules` are schema-required and always
    written."""
    if world is None or world == _world_to_board(raw):
        return
    target = out.setdefault("world", {})
    target["setting_summary"] = world.get("setting_summary", "")
    target["rules"] = [r for r in world.get("rules") or [] if r.strip()]

    orig_locations = (raw.get("world") or {}).get("locations") or {}
    renamed = {r["orig"]: r["id"].strip() for r in world.get("locations") or []
               if r.get("orig") and (r.get("id") or "").strip() and r["orig"] != r["id"].strip()}
    locations = {}
    for row in world.get("locations") or []:
        lid = (row.get("id") or "").strip()
        if not lid:
            continue
        entry = copy.deepcopy(orig_locations.get(row.get("orig"), {}))
        entry["name"] = row.get("name", "")
        entry["description"] = row.get("description", "")
        _set_or_drop(entry, "connected_to", [renamed.get(c, c) for c in row.get("connected_to") or [] if c])
        locations[lid] = entry
    _set_or_drop(target, "locations", locations or None)

    orig_factions = (raw.get("world") or {}).get("factions") or {}
    factions = {}
    for row in world.get("factions") or []:
        fid = (row.get("id") or "").strip()
        if not fid:
            continue
        entry = copy.deepcopy(orig_factions.get(row.get("orig"), {}))
        entry["name"] = row.get("name", "")
        for k in ("goals", "relationship_to_player"):
            _set_or_drop(entry, k, row.get(k, ""))
        factions[fid] = entry
    _set_or_drop(target, "factions", factions or None)

    start = (world.get("start_location") or "").strip()
    start = renamed.get(start, start)
    scene = (out.get("plot") or {}).get("initial_scene")
    if isinstance(scene, dict) and start:
        scene["location"] = start
    gate = (out.get("mechanics") or {}).get("gate")
    for g in (gate.get("gates") or []) if isinstance(gate, dict) else []:
        if isinstance(g, dict) and g.get("target") in renamed:
            g["target"] = renamed[g["target"]]


def _apply_lore(out: dict, raw: dict, lore) -> None:
    """`mechanics.lore` (CR-06) from the World tab, at its final path (D1): a new block declares
    `keyed_lore`, which this build does not register yet, so the story then fails
    `load_template()` loudly until the engine exists - correct, not a bug to route around.
    Entries patch their originals by `orig`; emptying the tab removes the block (an engine with
    no entries raises at load)."""
    if lore is None or lore == _lore_to_board(raw):
        return
    originals = {e.get("id"): e for e in _lore_to_board_entries(raw)}
    entries = []
    for row in lore.get("entries") or []:
        lid = (row.get("id") or "").strip()
        if not lid:
            continue
        entry = copy.deepcopy(originals.get(row.get("orig"), {}))
        entry["id"] = lid
        for k in LORE_FIELDS:
            _set_or_drop(entry, k, row.get(k))
        entry.setdefault("content", "")
        entries.append(entry)
    mechanics = out.setdefault("mechanics", {})
    if not entries:
        mechanics.pop("lore", None)
        return
    block = mechanics.get("lore")
    if not isinstance(block, dict):
        block = mechanics["lore"] = {"engine": "keyed_lore"}
    _set_or_drop(block, "max_active", lore.get("max_active"))
    block["entries"] = entries


def _apply_main_thread(out: dict, raw: dict, mt) -> None:
    """The acts strip. Untouched when unchanged. Acts are written in board order and renumbered
    1..n - `act_number` is how a save refers to an act, and saves are disposable during the
    overhaul (CLAUDE.md). Each patches a copy of its original (by `orig`), so `requires` survives.
    `title`, `description` and `acts` are schema-required and always written; `max_acts` is
    dropped when blank."""
    if mt is None or mt == _main_thread_to_board(raw):
        return
    target = out.setdefault("plot", {}).setdefault("main_thread", {})
    originals = list(((raw.get("plot") or {}).get("main_thread") or {}).get("acts") or [])
    target["title"] = mt.get("title", "")
    target["description"] = mt.get("description", "")
    _set_or_drop(target, "plot_notes", mt.get("plot_notes", ""))
    _set_or_drop(target, "max_acts", mt.get("max_acts"))
    acts = []
    for i, row in enumerate(mt.get("acts") or [], start=1):
        orig = row.get("orig")
        entry = copy.deepcopy(originals[orig]) if isinstance(orig, int) and 0 <= orig < len(originals) else {}
        entry["act_number"] = i
        entry["title"] = row.get("title", "")
        entry["description"] = row.get("description", "")
        _set_or_drop(entry, "completion_signals", [c for c in row.get("completion_signals") or [] if c.strip()])
        _set_or_drop(entry, "requires", row.get("requires") or None)
        acts.append(entry)
    target["acts"] = acts


# `mechanics.<key>` -> the engine a new block declares (D1: final paths, even though neither
# engine is built yet - a story authoring one fails load_template() loudly until it is).
CR11_BLOCKS = {"bonds": "scored_bonds", "side_threads": "episodic_threads"}


def _prune_blank(value):
    """`value` with every None, blank string, empty list and empty dict removed, recursively -
    so a field the author cleared is absent rather than stored empty (P-2). 0 and False stay:
    a seed of 0 and `default_recipe: false` are both real choices."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            v = _prune_blank(v)
            if v is None or v == "" or v == [] or v == {}:
                continue
            out[k] = v
        return out
    if isinstance(value, list):
        return [x for x in (_prune_blank(v) for v in value) if not (x is None or x == "" or x == {})]
    if isinstance(value, str):
        return value if value.strip() else ""
    return value


def _apply_cr11_block(out: dict, raw: dict, key: str, block) -> None:
    """`mechanics.bonds` / `mechanics.side_threads`. Untouched when the board hands back what
    the template holds (byte-identical round trip); None removes the block; anything else is
    written with blanks pruned and the engine declared first."""
    current = (raw.get("mechanics") or {}).get(key)
    if block == (copy.deepcopy(current) if isinstance(current, dict) else None):
        return
    mechanics = out.setdefault("mechanics", {})
    if block is None:
        mechanics.pop(key, None)
        return
    cleaned = _prune_blank(copy.deepcopy(block))
    cleaned.pop("engine", None)
    mechanics[key] = {"engine": CR11_BLOCKS[key], **cleaned}


def _apply_derived(out: dict, raw: dict, rules) -> None:
    """CR-04 `derived`. Untouched when unchanged; emptied removes the key (P-2). A rule keeps
    its `when` only when it has one (none means "always", the usual last rule), and a value row
    with no name is dropped. A rule's value may be an empty string on purpose, so values are
    not pruned."""
    current = raw.get("derived") if isinstance(raw.get("derived"), list) else []
    if rules == current:
        return
    cleaned = []
    for rule in rules or []:
        if not isinstance(rule, dict):
            continue
        entry = {k: copy.deepcopy(v) for k, v in rule.items() if k.startswith("_")}
        if rule.get("when"):
            entry["when"] = copy.deepcopy(rule["when"])
        entry["set"] = {k.strip(): v for k, v in (rule.get("set") or {}).items() if isinstance(k, str) and k.strip()}
        cleaned.append(entry)
    if cleaned:
        out["derived"] = cleaned
    else:
        out.pop("derived", None)


def _lore_to_board_entries(raw: dict) -> list:
    block = (raw.get("mechanics") or {}).get("lore")
    return [e for e in (block.get("entries") or [] if isinstance(block, dict) else []) if isinstance(e, dict)]


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

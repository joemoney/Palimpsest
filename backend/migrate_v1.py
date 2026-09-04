"""One-shot v1 -> v2 save converter (SCHEMA_V2_SPEC.md §2.4, §9 phase 2).

v1 saves were a full clone of template.json with runtime fields mutated in place - no
story/state split, no schema_version. This module converts one such flat dict into a v2
runtime-only save (see state_store.new_save_state for the target shape), given the v2
template it should resolve seeded content against.

Pure function, no I/O: state_store.load_state() is the only caller, and it owns reading
the v1 file, calling migrate(), and writing the result back. Defensive throughout (.get()
everywhere, never a bare subscript) because real saves on disk predate not just v2 but
several of v1's own incremental field additions (revealed_turn, entity_interaction_count,
npc_id-linked relationships, current_scene ever being written) - this has to degrade
gracefully for a save from any point in that history, not just the most recent v1 shape.
"""


def _migrate_flags(player: dict) -> dict:
    return {
        "active": player.get("flags_active", {}),
        "meta": player.get("flags_meta", {}),
        "archive": player.get("flags_archive", {}),
    }


def _migrate_characters(v1: dict) -> dict:
    """Merges v1's two disconnected registries - player.relationships (score, keyed by
    free-text name) and top-level characters (NPC records, keyed by a synthetic char_NNN id,
    linked to a relationship via npc_id) - into v2's single characters store, keyed by name.
    A v1 characters entry that isn't type == "npc" (i.e. the old "the Architect" pseudo-
    character some templates carried directly) is dropped here; that entity is now
    represented solely by mechanics.tracked_entity, which the new v2 template already
    carries independent of any save."""
    player = v1.get("player", {})
    v1_relationships = player.get("relationships", {})
    npc_records = {
        cid: c for cid, c in v1.get("characters", {}).items() if c.get("type") == "npc"
    }

    result = {}
    consumed_ids = set()

    for name, entry in v1_relationships.items():
        if isinstance(entry, dict):
            score = entry.get("score", 0)
            npc_id = entry.get("npc_id")
        else:
            # pre-npc_id shape: a bare int score (see the old state_store._migrate_relationships)
            score = entry
            npc_id = None
        record = npc_records.get(npc_id, {}) if npc_id else {}
        if npc_id in npc_records:
            consumed_ids.add(npc_id)

        char = {"relationship": score, "first_seen_turn": 0, "introduced": bool(record.get("introduced", False))}
        for field in ("description", "role", "relationship_to_player", "hook"):
            if record.get(field):
                char[field] = record[field]
        result[name] = char

    # an NPC record that was seeded/generated but never got a relationship_changes entry
    # yet still needs to survive migration, so its hook/description aren't lost.
    for cid, record in npc_records.items():
        if cid in consumed_ids:
            continue
        name = record.get("name")
        if not name or name in result:
            continue
        char = {"relationship": 0, "first_seen_turn": 0, "introduced": bool(record.get("introduced", False))}
        for field in ("description", "role", "relationship_to_player", "hook"):
            if record.get(field):
                char[field] = record[field]
        result[name] = char

    return result


def _migrate_subplots(v1_subplots: dict, template_subplots: dict) -> dict:
    """A subplot id present in the v2 template's seed pool resolves its title/description/
    etc. from there going forward (the runtime entry only needs the mutable fields); one
    that isn't (every subplot generated during play, which is most of them in a long-running
    save) carries its full content forward self-contained, since there's nothing left to
    resolve it against."""
    result = {}
    for sid, sp in v1_subplots.items():
        if sid in template_subplots:
            result[sid] = {
                "progress": sp.get("progress", 0),
                "status": sp.get("status", "not_started"),
                "active": bool(sp.get("active", False)),
            }
        else:
            result[sid] = {
                "progress": sp.get("progress", 0),
                "status": sp.get("status", "not_started"),
                "active": bool(sp.get("active", False)),
                "title": sp.get("title", sid),
                "description": sp.get("description", ""),
                "priority": sp.get("priority", "medium"),
                "ties_to_main_plot": sp.get("ties_to_main_plot", ""),
                "completion_threshold": sp.get("completion_threshold", 100),
                "span": sp.get("span", "single_act"),
            }
    return result


def _migrate_acts(v1_main_thread: dict, template_acts: list) -> tuple:
    """Splits v1's single acts[] list into a v2 act_completion overlay (for act_numbers the
    v2 template still authors - in practice just Act 1) and generated_acts (everything
    else, carried forward self-contained since it has no template counterpart)."""
    authored_numbers = {a["act_number"] for a in template_acts}
    act_completion = {}
    generated_acts = []
    for act in v1_main_thread.get("acts", []):
        number = act.get("act_number")
        if number in authored_numbers:
            act_completion[str(number)] = {
                "completed": bool(act.get("completed", False)),
                "optional": bool(act.get("optional", False)),
            }
        else:
            entry = {
                "act_number": number,
                "title": act.get("title", f"Act {number}"),
                "description": act.get("description", ""),
                "completion_signals": act.get("completion_signals", []),
                "completed": bool(act.get("completed", False)),
                "optional": bool(act.get("optional", False)),
            }
            if act.get("is_finale"):
                entry["is_finale"] = True
            generated_acts.append(entry)
    return act_completion, generated_acts


def _migrate_revelations_revealed(v1: dict) -> dict:
    """v1 tracked reveal state in place on each memory_fragment record (revealed bool,
    revealed_turn if the save postdates CR-03); v2 moves that to a separate runtime-only
    map (state.plot.revelations_revealed) since authored content - mechanics.revelations,
    the fragment's trigger/content - can no longer be mutated in place."""
    fragments = v1.get("player", {}).get("origin", {}).get("memory_fragments", [])
    return {
        f["id"]: {"turn": f.get("revealed_turn", 0)}
        for f in fragments
        if f.get("revealed") and f.get("id")
    }


def migrate(v1: dict, story_slug: str, template: dict) -> dict:
    """Converts one v1 save (a flat dict cloned from a pre-v2 template.json) into a v2
    runtime-only save. `template` is the v2 template's own raw (unfrozen) dict, used to
    decide which subplots/acts resolve against it vs. carry their own content."""
    player = v1.get("player", {})
    plot = v1.get("plot", {})
    main_thread = plot.get("main_thread", {})
    pacing = plot.get("pacing", {})
    history_log = v1.get("history_log", {})
    scene = plot.get("current_scene", {})
    endgame = plot.get("endgame", {})
    thread_steering = plot.get("thread_steering", {})

    act_completion, generated_acts = _migrate_acts(main_thread, template["plot"]["main_thread"]["acts"])

    return {
        "schema_version": 2,
        "story_slug": story_slug,
        "story_version": template.get("story_version"),

        "protagonist": {
            "name": player.get("name", ""),
            "traits": player.get("traits", []),
            "inventory": player.get("inventory", []),
            "stats": player.get("stats", {}),
            "creation_choices": player.get("creation_choices", {}),
            "flags": _migrate_flags(player),
        },

        "characters": _migrate_characters(v1),

        "scene": {
            "location": scene.get("location", ""),
            "summary": scene.get("summary", ""),
            "present": scene.get("present_npcs", []),
        },

        "plot": {
            "opening_played": bool(plot.get("opening_scene", {}).get("played", False)),
            "current_act": main_thread.get("current_act", 1),
            "generated_acts": generated_acts,
            "act_completion": act_completion,
            "act_history": main_thread.get("act_history", []),
            "emergent_directions": main_thread.get("emergent_directions", []),
            "subplots": _migrate_subplots(plot.get("subplots", {}), template["plot"]["subplots"]),
            "completed_subplots": plot.get("completed_subplots", []),
            "revelations_revealed": _migrate_revelations_revealed(v1),
            "entity_contact_count": plot.get("entity_interaction_count", 0),
            "endgame": {
                "requested": bool(endgame.get("requested", False)),
                "requested_turn": endgame.get("requested_turn"),
                "final_arc": endgame.get("final_arc"),
                "concluded": bool(endgame.get("concluded", False)),
                "cause": "player_request" if endgame.get("requested") else None,
            },
            "thread_steering": {
                "last_pivot_turn": thread_steering.get("last_pivot_turn", 0),
                "pivot_history": thread_steering.get("pivot_history", []),
                "emerging_themes": thread_steering.get("emerging_themes", []),
                "player_driven_goals": thread_steering.get("player_driven_goals", []),
                "pending_seeds": thread_steering.get("pending_seeds", []),
            },
        },

        "pacing": {
            "turn_count": pacing.get("turn_count", 0),
            "turns_since_nudge": pacing.get("turns_since_last_pacing_nudge", 0),
            "turns_since_act_check": pacing.get("turns_since_last_act_check", 0),
            "subplots_completed_this_act": pacing.get("subplots_completed_this_act", 0),
            "last_direction": pacing.get("last_pacing_direction", ""),
        },

        "history": {
            "recent_turns": history_log.get("recent_turns", []),
            "compressed_summary": history_log.get("compressed_summary", ""),
            "full_transcript": history_log.get("full_transcript", []),
        },

        # Dropped rather than migrated: a v1 pending_regenerate snapshot is a full deep
        # copy of the *pre-v2* state shape, which v2's regenerate_last_turn can't safely
        # replay. Losing the ability to regenerate exactly the one turn that was in flight
        # at the moment of migration is a one-time, low-stakes cost - not lossy for
        # anything else.
        "pending_regenerate": None,
    }

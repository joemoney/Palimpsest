#!/usr/bin/env python3
"""
Plot Management Tool for Mid-Adventure Steering
Allows dynamic modification of main plot, acts, and story direction during gameplay.
"""
import sys
from datetime import datetime

import state_store
import story_engine


def show_plot_overview(ctx):
    """Display current main plot structure and steering options."""
    main = story_engine._main_thread_view(ctx)
    plot_state = ctx["state"]["plot"]

    print("\n" + "=" * 70)
    print("MAIN PLOT OVERVIEW")
    print("=" * 70)
    print(f"Title: {main['title']}")
    print(f"\nDescription: {main['description']}")
    print(f"\nPlot Notes: {main.get('plot_notes', 'None')}")
    print()

    endgame = plot_state.get("endgame", {})
    if endgame.get("requested"):
        print("*** ENDGAME IN PROGRESS - no more acts or subplots will be auto-generated ***")
        if endgame.get("final_arc"):
            print(f"    Final arc: {endgame['final_arc'].get('title', '')}")
        print()

    print("-" * 70)
    print("ACTS")
    print("-" * 70)
    for act in story_engine._all_acts(ctx):
        status = "✓ COMPLETED" if act["completed"] else f"● ACT {act['act_number']}"
        if act['act_number'] == plot_state['current_act']:
            status += " (CURRENT)"
        optional = " [OPTIONAL]" if act.get("optional", False) else ""
        if act.get("is_finale"):
            optional += " [FINALE]"

        print(f"{status}{optional}: {act['title']}")
        print(f"  {act['description']}")

        signals = act.get("completion_signals", [])
        if signals:
            print(f"  Signals: {', '.join(signals)}")
        print()

    # Show emergent directions
    if plot_state.get("emergent_directions"):
        print("-" * 70)
        print("EMERGENT STORY DIRECTIONS")
        print("-" * 70)
        for i, direction in enumerate(plot_state["emergent_directions"], 1):
            print(f"{i}. {direction['title']}")
            print(f"   Noted at turn {direction.get('turn', '?')}: {direction['description']}")
            print()

    # Show steering history
    steering = plot_state.get("thread_steering", {})
    if steering.get("pivot_history"):
        print("-" * 70)
        print("PLOT PIVOT HISTORY")
        print("-" * 70)
        for pivot in steering["pivot_history"][-3:]:  # Show last 3 pivots
            print(f"Turn {pivot['turn']}: {pivot['reason']}")
        print()

    # Show discovered/seeded characters (authored ones from the template are shown too, via
    # the merged roster - see story_engine._character_record). Only place either kind is
    # displayed; a seeded character would otherwise be invisible for review outside the raw
    # save file.
    names = sorted(story_engine._all_character_names(ctx))
    if names:
        print("-" * 70)
        print("CHARACTERS")
        print("-" * 70)
        for name in names:
            record = story_engine._character_record(ctx, name)
            marker = " (authored)" if record["authored"] else (
                " (introduced)" if record["introduced"] else " (not yet introduced)"
            )
            print(f"{name}{marker}")
            if record.get("description"):
                print(f"  {record['description']}")
            if record.get("hook"):
                print(f"  Hook: {record['hook']}")
            if record["relationship"] is not None:
                print(f"  Relationship: {record['relationship']}")
            print()

    # Show pending steering seeds awaiting review (see stage_steering_seed) - nothing here
    # has been committed to the save yet.
    pending = steering.get("pending_seeds", [])
    if pending:
        print("-" * 70)
        print(f"PENDING SEEDS ({len(pending)} awaiting review)")
        print("-" * 70)
        for seed in pending:
            print(f"{seed['id']} [{seed['type']}] - from note: \"{seed['note']}\"")
        print("Run 'seed-list' for full details, 'seed-apply <id>' or 'seed-discard <id>'.")
        print()

    unlinked = list_unlinked_relationships(ctx)
    if unlinked:
        print("-" * 70)
        print("UNLINKED RELATIONSHIPS (tracked, but no full character record yet)")
        print("-" * 70)
        for name, score in unlinked:
            print(f"{name}: {score}")
        print("Run 'promote-relationship \"<name>\"' to turn one into a full character.")
        print()


def add_act(ctx, title, description, position=None, completion_signals=None, optional=False):
    """Add a new act to the main plot. Always lands in ctx["state"]["plot"]["generated_acts"]
    - a runtime-added act can never be authored content, so there's nowhere else for it to
    go. Renumbering on an explicit `position` only ever shifts *other* generated acts; a
    template-authored act's number can't change (it's frozen), so a position that collides
    with one just inserts alongside it rather than displacing it - acceptable in practice
    since both current stories only ever author Act 1, and a steering insertion realistically
    always targets somewhere beyond it."""
    existing_numbers = [a["act_number"] for a in story_engine._all_acts(ctx)]
    generated = ctx["state"]["plot"]["generated_acts"]

    if position is None:
        act_number = (max(existing_numbers) + 1) if existing_numbers else 1
    else:
        act_number = position
        for act in generated:
            if act["act_number"] >= position:
                act["act_number"] += 1

    new_act = {
        "act_number": act_number,
        "title": title,
        "description": description,
        "completion_signals": completion_signals or [],
        "completed": False,
        "optional": optional,
    }
    generated.append(new_act)
    generated.sort(key=lambda a: a["act_number"])

    print(f"Added Act {act_number}: {title}")
    if optional:
        print("  (Marked as optional)")


def modify_act(ctx, act_number, **kwargs):
    """Modify an existing act's properties. Only a *generated* act can be modified - a
    template-authored one (in practice just Act 1) is frozen content; use 'pivot' to
    redirect the main thread instead, or edit the template file directly."""
    authored_numbers = {a["act_number"] for a in ctx["story"]["plot"]["main_thread"]["acts"]}
    if act_number in authored_numbers:
        print(f"Error: Act {act_number} is authored content (from the story template) and "
              "can't be modified at runtime. Use 'pivot' to redirect the main thread, or "
              "edit the template file directly.")
        return

    act = next((a for a in ctx["state"]["plot"]["generated_acts"] if a["act_number"] == act_number), None)
    if not act:
        print(f"Error: Act {act_number} not found")
        return

    modified = []
    for key, value in kwargs.items():
        if key in act:
            act[key] = value
            modified.append(key)

    if modified:
        print(f"Modified Act {act_number}: {', '.join(modified)}")
    else:
        print(f"No changes made to Act {act_number}")


def pivot_main_plot(ctx, new_title, new_description, reason):
    """Pivot the main plot to a new direction. main_thread.title/description are authored,
    frozen content, so this writes a runtime override (see story_engine._main_thread_view)
    that every prompt-building/display path already resolves through, rather than mutating
    the template in place."""
    plot_state = ctx["state"]["plot"]
    steering = plot_state["thread_steering"]
    turn_count = ctx["state"]["pacing"]["turn_count"]
    old_title = story_engine._main_thread_view(ctx)["title"]

    pivot_record = {
        "turn": turn_count,
        "from_title": old_title,
        "to_title": new_title,
        "reason": reason,
        "timestamp": datetime.now().isoformat(),
    }
    steering["pivot_history"].append(pivot_record)
    steering["last_pivot_turn"] = turn_count

    plot_state["main_thread_override"] = {"title": new_title, "description": new_description}

    print(f"\nPlot pivoted from '{old_title}' to '{new_title}'")
    print(f"Reason: {reason}")
    print(f"Turn: {turn_count}")


def add_emergent_direction(ctx, title, description):
    """Note an emergent story direction for potential future development."""
    turn_count = ctx["state"]["pacing"]["turn_count"]
    direction = {"title": title, "description": description, "turn": turn_count, "promoted": False}
    ctx["state"]["plot"]["emergent_directions"].append(direction)
    print(f"Noted emergent direction: {title}")


def promote_emergent_to_act(ctx, emergent_index, position=None):
    """Promote an emergent direction into a full act."""
    directions = ctx["state"]["plot"]["emergent_directions"]
    if emergent_index >= len(directions):
        print("Error: Invalid emergent direction index")
        return

    direction = directions[emergent_index]
    add_act(ctx, direction["title"], direction["description"], position)
    direction["promoted"] = True
    print(f"Promoted emergent direction '{direction['title']}' to full act")


def add_player_goal(ctx, goal_description):
    """Record a player-driven goal that emerged during play."""
    steering = ctx["state"]["plot"]["thread_steering"]
    turn_count = ctx["state"]["pacing"]["turn_count"]
    steering["player_driven_goals"].append({"description": goal_description, "turn": turn_count, "active": True})
    print(f"Recorded player goal: {goal_description}")


def add_emerging_theme(ctx, theme):
    """Note a theme that's emerging in the story."""
    themes = ctx["state"]["plot"]["thread_steering"]["emerging_themes"]
    if theme not in themes:
        themes.append(theme)
        print(f"Noted emerging theme: {theme}")
    else:
        print(f"Theme already noted: {theme}")


# --- Freeform LLM-assisted steering seeds ---
# Unlike every command above (pure local edits), 'seed' makes one LLM call
# (story_engine.generate_steering_seed) to turn a freeform note into a draft addition -
# a new character, subplot, or plot direction - which the player reviews (and can edit via
# seed-apply's overrides) before anything lands in the save. This two-step stage/apply split
# is what makes review possible: a single combined "generate and commit" command would give
# the player no chance to reject or tweak a bad generation before it's already in the save.

_SEED_FIELDS = {
    "character": ("name", "description", "role", "relationship_to_player", "hook"),
    "subplot": ("title", "description", "priority", "ties_to_main_plot", "span"),
    "direction": ("title", "description"),
}


def _next_seed_id(pending_seeds):
    existing_numbers = [
        int(s["id"].rsplit("_", 1)[-1])
        for s in pending_seeds
        if s["id"].rsplit("_", 1)[-1].isdigit()
    ]
    next_number = (max(existing_numbers) + 1) if existing_numbers else 1
    return f"seed_{next_number:03d}"


def stage_steering_seed(ctx, note):
    """Generate a draft character/subplot/direction from a freeform note (see
    story_engine.generate_steering_seed) and hold it in plot.thread_steering.pending_seeds
    for review - nothing is committed to the save until apply_steering_seed confirms it.
    Returns the new seed's id, or None if generation failed (bad/empty LLM output - same
    "costs nothing, just try again" failure mode as generate_new_subplot)."""
    generated = story_engine.generate_steering_seed(ctx, note)
    if generated is None:
        print("Could not generate a seed from that note - try rephrasing it.")
        return None

    steering = ctx["state"]["plot"]["thread_steering"]
    pending = steering["pending_seeds"]
    seed_id = _next_seed_id(pending)
    pending.append({
        "id": seed_id,
        "note": note,
        "turn_added": ctx["state"]["pacing"]["turn_count"],
        "type": generated["type"],
        "draft": generated["draft"],
    })

    print(f"Staged {generated['type']} seed '{seed_id}' for review:")
    for key, value in generated["draft"].items():
        print(f"  {key}: {value}")
    print(f"Run 'seed-apply {seed_id}' to commit it (pass --<field> '<override>' to edit "
          f"a value first), or 'seed-discard {seed_id}' to drop it.")
    return seed_id


def apply_steering_seed(ctx, seed_id, **overrides):
    """Commit a staged seed (see stage_steering_seed) to the save, applying any field
    overrides the player supplied to edit it first. Dispatches by the seed's type into the
    same shapes the rest of the engine already produces/expects, so a seeded addition is
    indistinguishable from one the story generated on its own:
    - character -> new ctx["state"]["characters"] entry, introduced: false (surfaces via
      story_engine.generate_pacing_nudge until the player actually meets them - see
      update_progress_from_turn's auto-flip when they do).
    - subplot -> story_engine.insert_subplot, the same insertion generate_new_subplot uses.
    - direction -> an emergent_directions entry, the same shape add_emergent_direction
      already produces (and, since the pacing-nudge fix, actually read back now)."""
    steering = ctx["state"]["plot"]["thread_steering"]
    pending = steering["pending_seeds"]
    seed = next((s for s in pending if s["id"] == seed_id), None)
    if seed is None:
        print(f"Error: no pending seed '{seed_id}'")
        return None

    seed_type = seed["type"]
    fields = dict(seed["draft"])
    for key in _SEED_FIELDS.get(seed_type, ()):
        if overrides.get(key):
            fields[key] = overrides[key]

    if seed_type == "character":
        result = story_engine.insert_character(
            ctx, fields.get("name", ""),
            description=fields.get("description", ""),
            role=fields.get("role", ""),
            relationship_to_player=fields.get("relationship_to_player", ""),
            hook=fields.get("hook", ""),
            introduced=False,
            origin="seed",
            seed_note=seed["note"],
        )
        print(f"Added character '{fields.get('name', '')}' ({result})")
    elif seed_type == "subplot":
        span = fields.get("span") if fields.get("span") in ("single_act", "multi_act") else "single_act"
        result = story_engine.insert_subplot(
            ctx, fields.get("title", ""), fields.get("description", ""),
            priority=fields.get("priority", "medium"),
            ties_to_main_plot=fields.get("ties_to_main_plot", ""),
            span=span,
        )
        print(f"Added subplot '{fields.get('title', '')}' ({result}, span={span})")
    elif seed_type == "direction":
        ctx["state"]["plot"]["emergent_directions"].append({
            "title": fields.get("title", ""),
            "description": fields.get("description", ""),
            "turn": ctx["state"]["pacing"]["turn_count"],
            "promoted": False,
        })
        result = None
        print(f"Noted direction: {fields.get('title', '')}")
    else:
        print(f"Error: unknown seed type '{seed_type}'")
        return None

    pending.remove(seed)
    return result


def list_pending_seeds(ctx):
    """Print every pending seed's full draft (unlike show_plot_overview's one-line-each
    summary) so the player has enough detail to decide whether to seed-apply as-is, with
    overrides, or seed-discard."""
    pending = ctx["state"]["plot"]["thread_steering"].get("pending_seeds", [])
    if not pending:
        print("No pending seeds.")
        return
    for seed in pending:
        print(f"{seed['id']} [{seed['type']}] - from note: \"{seed['note']}\"")
        for key, value in seed["draft"].items():
            print(f"  {key}: {value}")
        print()


def discard_steering_seed(ctx, seed_id):
    """Drop a pending seed without applying it."""
    pending = ctx["state"]["plot"]["thread_steering"]["pending_seeds"]
    seed = next((s for s in pending if s["id"] == seed_id), None)
    if seed is None:
        print(f"Error: no pending seed '{seed_id}'")
        return
    pending.remove(seed)
    print(f"Discarded seed '{seed_id}'")


# --- Promoting a relationship-only name to a full character ---
# update_progress_from_turn deliberately never auto-creates a full character record for a
# generic/descriptive handle (e.g. "the advocate", "a guard") - only for a name the model
# actually gives someone (see story_engine.py's new_characters prompt instruction). A
# relationship that's still "unlinked" (has a score but no description/role/hook behind it,
# and isn't an authored character) can be promoted here instead, on request - same
# story_engine.generate_character_from_relationship + insert_character pipeline the
# automatic paths use, just triggered manually.

_RELATIONSHIP_FIELDS = ("description", "role", "relationship_to_player", "hook")


def list_unlinked_relationships(ctx):
    """Every tracked relationship that isn't yet backed by a full character record -
    candidates for promote_relationship_to_npc. An authored character (from the template)
    is never "unlinked" even before it's been given a description override at runtime, since
    its description already resolves from the template - only a discovered entry with no
    description of its own qualifies."""
    characters = ctx["state"]["characters"]
    authored_names = set(ctx["story"]["world"].get("characters", {}).keys())
    return [
        (name, entry.get("relationship", 0))
        for name, entry in characters.items()
        if name not in authored_names and not entry.get("description")
    ]


def promote_relationship_to_npc(ctx, name, **overrides):
    """Draft and commit a full character record for a relationship-only name (see
    generate_character_from_relationship), applying any field overrides the caller supplied.
    Returns the name on success, or None if it isn't a tracked relationship, is already a
    full character, or generation failed."""
    characters = ctx["state"]["characters"]
    entry = characters.get(name)
    if entry is None:
        print(f"Error: no tracked relationship named '{name}'")
        return None
    if entry.get("description"):
        print(f"'{name}' already has a full character record")
        return None

    draft = story_engine.generate_character_from_relationship(ctx, name)
    if draft is None:
        print(f"Could not draft a character for '{name}' - try again.")
        return None

    for key in _RELATIONSHIP_FIELDS:
        if overrides.get(key):
            draft[key] = overrides[key]

    entry["description"] = draft.get("description", "")
    entry["role"] = draft.get("role", "")
    entry["relationship_to_player"] = draft.get("relationship_to_player", "")
    entry["hook"] = draft.get("hook", "")
    entry["introduced"] = True
    entry["origin"] = "relationship"

    print(f"Promoted '{name}' to a full character")
    return name


def main():
    user_id, story_slug, parsed_argv = state_store.parse_user_story_args(sys.argv[1:])
    # keep a program-name placeholder at index 0 so every sys.argv[N] below still
    # lines up positionally, now with --user/--story already stripped out
    argv = [sys.argv[0]] + parsed_argv

    if len(argv) < 2:
        print("Plot Management Commands:")
        print("  (add --user <id> --story <slug> anywhere to target a specific save;")
        print("   defaults to the local single-player save)")
        print("\nViewing:")
        print("  python plot_manager.py overview")
        print("\nAdding:")
        print("  python plot_manager.py add-act '<title>' '<description>' [position] [--optional]")
        print("  python plot_manager.py add-emergent '<title>' '<description>'")
        print("  python plot_manager.py add-goal '<goal description>'")
        print("  python plot_manager.py add-theme '<theme>'")
        print("\nFreeform steering seeds (LLM infers character/subplot/direction from a note,")
        print("stages a draft for review before anything is committed - see 'seed-list'):")
        print("  python plot_manager.py seed '<freeform note>'")
        print("  python plot_manager.py seed-list")
        print("  python plot_manager.py seed-apply <seed_id> [--name/--title '<override>']")
        print("      [--description '<override>'] [--role '<override>'] [--relationship '<override>']")
        print("      [--hook '<override>'] [--priority '<override>'] [--ties '<override>']")
        print("      [--span 'single_act|multi_act']")
        print("  python plot_manager.py seed-discard <seed_id>")
        print("\nRelationships (promote a tracked but not-yet-formalized name to a real character -")
        print("see 'overview' for which relationships are still unlinked):")
        print("  python plot_manager.py list-unlinked")
        print("  python plot_manager.py promote-relationship '<name>' [--description '<override>']")
        print("      [--role '<override>'] [--relationship '<override>'] [--hook '<override>']")
        print("\nModifying:")
        print("  python plot_manager.py modify-act <act_number> --title '<new title>' --description '<new desc>'")
        print("  python plot_manager.py pivot '<new title>' '<new description>' '<reason>'")
        print("\nPromoting:")
        print("  python plot_manager.py promote-emergent <index> [position]")
        return

    ctx = state_store.load_state(user_id, story_slug)
    command = argv[1]

    if command == "overview":
        show_plot_overview(ctx)

    elif command == "add-act":
        if len(argv) < 4:
            print("Usage: python plot_manager.py add-act '<title>' '<description>' [position] [--optional]")
            return
        title = argv[2]
        description = argv[3]
        position = int(argv[4]) if len(argv) > 4 and argv[4].isdigit() else None
        optional = "--optional" in argv
        add_act(ctx, title, description, position, optional=optional)
        state_store.save_state(ctx, user_id, story_slug)

    elif command == "modify-act":
        if len(argv) < 3:
            print("Usage: python plot_manager.py modify-act <act_number> --title '<new>' --description '<new>'")
            return
        act_num = int(argv[2])
        kwargs = {}
        i = 3
        while i < len(argv):
            if argv[i] == "--title" and i+1 < len(argv):
                kwargs["title"] = argv[i+1]
                i += 2
            elif argv[i] == "--description" and i+1 < len(argv):
                kwargs["description"] = argv[i+1]
                i += 2
            else:
                i += 1
        modify_act(ctx, act_num, **kwargs)
        state_store.save_state(ctx, user_id, story_slug)

    elif command == "pivot":
        if len(argv) < 5:
            print("Usage: python plot_manager.py pivot '<new title>' '<new description>' '<reason>'")
            return
        new_title = argv[2]
        new_desc = argv[3]
        reason = argv[4]
        pivot_main_plot(ctx, new_title, new_desc, reason)
        state_store.save_state(ctx, user_id, story_slug)

    elif command == "add-emergent":
        if len(argv) < 4:
            print("Usage: python plot_manager.py add-emergent '<title>' '<description>'")
            return
        title = argv[2]
        desc = argv[3]
        add_emergent_direction(ctx, title, desc)
        state_store.save_state(ctx, user_id, story_slug)

    elif command == "promote-emergent":
        if len(argv) < 3:
            print("Usage: python plot_manager.py promote-emergent <index> [position]")
            return
        index = int(argv[2])
        position = int(argv[3]) if len(argv) > 3 else None
        promote_emergent_to_act(ctx, index, position)
        state_store.save_state(ctx, user_id, story_slug)

    elif command == "add-goal":
        if len(argv) < 3:
            print("Usage: python plot_manager.py add-goal '<goal description>'")
            return
        goal = argv[2]
        add_player_goal(ctx, goal)
        state_store.save_state(ctx, user_id, story_slug)

    elif command == "add-theme":
        if len(argv) < 3:
            print("Usage: python plot_manager.py add-theme '<theme>'")
            return
        theme = argv[2]
        add_emerging_theme(ctx, theme)
        state_store.save_state(ctx, user_id, story_slug)

    elif command == "seed":
        if len(argv) < 3:
            print("Usage: python plot_manager.py seed '<freeform note>'")
            return
        note = argv[2]
        stage_steering_seed(ctx, note)
        state_store.save_state(ctx, user_id, story_slug)

    elif command == "seed-list":
        list_pending_seeds(ctx)

    elif command == "seed-apply":
        if len(argv) < 3:
            print("Usage: python plot_manager.py seed-apply <seed_id> [--name/--title '<override>'] "
                  "[--description '<override>'] [--role '<override>'] [--relationship '<override>'] "
                  "[--hook '<override>'] [--priority '<override>'] [--ties '<override>'] "
                  "[--span 'single_act|multi_act']")
            return
        seed_id = argv[2]
        kwargs = {}
        i = 3
        flag_map = {
            "--name": "name", "--title": "title", "--description": "description",
            "--role": "role", "--relationship": "relationship_to_player",
            "--hook": "hook", "--priority": "priority", "--ties": "ties_to_main_plot",
            "--span": "span",
        }
        while i < len(argv):
            if argv[i] in flag_map and i + 1 < len(argv):
                kwargs[flag_map[argv[i]]] = argv[i + 1]
                i += 2
            else:
                i += 1
        apply_steering_seed(ctx, seed_id, **kwargs)
        state_store.save_state(ctx, user_id, story_slug)

    elif command == "seed-discard":
        if len(argv) < 3:
            print("Usage: python plot_manager.py seed-discard <seed_id>")
            return
        seed_id = argv[2]
        discard_steering_seed(ctx, seed_id)
        state_store.save_state(ctx, user_id, story_slug)

    elif command == "list-unlinked":
        unlinked = list_unlinked_relationships(ctx)
        if not unlinked:
            print("No unlinked relationships.")
        for name, score in unlinked:
            print(f"{name}: {score}")

    elif command == "promote-relationship":
        if len(argv) < 3:
            print("Usage: python plot_manager.py promote-relationship '<name>' "
                  "[--description '<override>'] [--role '<override>'] "
                  "[--relationship '<override>'] [--hook '<override>']")
            return
        name = argv[2]
        kwargs = {}
        i = 3
        flag_map = {
            "--description": "description", "--role": "role",
            "--relationship": "relationship_to_player", "--hook": "hook",
        }
        while i < len(argv):
            if argv[i] in flag_map and i + 1 < len(argv):
                kwargs[flag_map[argv[i]]] = argv[i + 1]
                i += 2
            else:
                i += 1
        promote_relationship_to_npc(ctx, name, **kwargs)
        state_store.save_state(ctx, user_id, story_slug)

    else:
        print(f"Unknown command: {command}")
        print("Run 'python plot_manager.py' for usage")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Plot Management Tool for Mid-Adventure Steering
Allows dynamic modification of main plot, acts, and story direction during gameplay.
"""
import sys
from datetime import datetime

import state_store
import story_engine


def show_plot_overview(state):
    """Display current main plot structure and steering options."""
    plot = state["plot"]
    main = plot["main_thread"]
    
    print("\n" + "=" * 70)
    print("MAIN PLOT OVERVIEW")
    print("=" * 70)
    print(f"Title: {main['title']}")
    print(f"Primary Focus: {main['is_primary_focus']}")
    print(f"Can Pivot: {main['can_pivot']}")
    print(f"\nDescription: {main['description']}")
    print(f"\nPlot Notes: {main.get('plot_notes', 'None')}")
    print()
    
    endgame = state["plot"].get("endgame", {})
    if endgame.get("requested"):
        print("*** ENDGAME IN PROGRESS - no more acts or subplots will be auto-generated ***")
        if endgame.get("final_arc"):
            print(f"    Final arc: {endgame['final_arc'].get('title', '')}")
        print()

    print("-" * 70)
    print("ACTS")
    print("-" * 70)
    for act in main["acts"]:
        status = "✓ COMPLETED" if act["completed"] else f"● ACT {act['act_number']}"
        if act['act_number'] == main['current_act']:
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
    
    # Show alternate threads if any
    if plot.get("alternate_threads"):
        print("-" * 70)
        print("ALTERNATE PLOT THREADS")
        print("-" * 70)
        for thread_id, thread in plot["alternate_threads"].items():
            active = "●" if thread.get("active", False) else "○"
            print(f"{active} {thread['title']}")
            print(f"  {thread['description']}")
            print()
    
    # Show emergent directions
    if main.get("emergent_directions"):
        print("-" * 70)
        print("EMERGENT STORY DIRECTIONS")
        print("-" * 70)
        for i, direction in enumerate(main["emergent_directions"], 1):
            print(f"{i}. {direction['title']}")
            print(f"   Noted at turn {direction.get('turn', '?')}: {direction['description']}")
            print()
    
    # Show steering history
    steering = plot.get("thread_steering", {})
    if steering.get("pivot_history"):
        print("-" * 70)
        print("PLOT PIVOT HISTORY")
        print("-" * 70)
        for pivot in steering["pivot_history"][-3:]:  # Show last 3 pivots
            print(f"Turn {pivot['turn']}: {pivot['reason']}")
        print()

    # Show characters (the pre-authored "Architect"-style entries as well as any seeded via
    # 'seed'/'seed-apply' - see apply_steering_seed). Only place either kind is displayed;
    # a seeded character would otherwise be invisible for review outside the raw save file.
    characters = state.get("characters", {})
    if characters:
        print("-" * 70)
        print("CHARACTERS")
        print("-" * 70)
        for char_id, char in characters.items():
            marker = ""
            if char.get("type") == "npc":
                marker = " (introduced)" if char.get("introduced") else " (not yet introduced)"
            print(f"{char.get('name', char_id)} [{char_id}]{marker}")
            if char.get("description"):
                print(f"  {char['description']}")
            if char.get("hook"):
                print(f"  Hook: {char['hook']}")
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


def add_act(state, title, description, position=None, completion_signals=None, optional=False):
    """Add a new act to the main plot."""
    main = state["plot"]["main_thread"]
    acts = main["acts"]

    # Determine act number
    if position is None:
        # Add to end
        act_number = len(acts) + 1
    else:
        # Insert at position, renumber subsequent acts
        act_number = position
        for act in acts:
            if act["act_number"] >= position:
                act["act_number"] += 1

    if completion_signals is None:
        completion_signals = []

    new_act = {
        "act_number": act_number,
        "title": title,
        "description": description,
        "completion_signals": completion_signals,
        "completed": False,
        "optional": optional
    }
    
    if position is None:
        acts.append(new_act)
    else:
        acts.insert(position - 1, new_act)
        acts.sort(key=lambda x: x["act_number"])
    
    print(f"Added Act {act_number}: {title}")
    if optional:
        print("  (Marked as optional)")


def modify_act(state, act_number, **kwargs):
    """Modify an existing act's properties."""
    main = state["plot"]["main_thread"]
    
    act = None
    for a in main["acts"]:
        if a["act_number"] == act_number:
            act = a
            break
    
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


def pivot_main_plot(state, new_title, new_description, reason):
    """Pivot the main plot to a new direction."""
    main = state["plot"]["main_thread"]
    steering = state["plot"].setdefault("thread_steering", {})
    turn_count = state["plot"]["pacing"]["turn_count"]
    
    # Record the pivot
    pivot_record = {
        "turn": turn_count,
        "from_title": main["title"],
        "to_title": new_title,
        "reason": reason,
        "timestamp": datetime.now().isoformat()
    }
    
    if "pivot_history" not in steering:
        steering["pivot_history"] = []
    steering["pivot_history"].append(pivot_record)
    steering["last_pivot_turn"] = turn_count
    
    # Update main thread
    old_title = main["title"]
    main["title"] = new_title
    main["description"] = new_description
    
    print(f"\nPlot pivoted from '{old_title}' to '{new_title}'")
    print(f"Reason: {reason}")
    print(f"Turn: {turn_count}")


def add_emergent_direction(state, title, description):
    """Note an emergent story direction for potential future development."""
    main = state["plot"]["main_thread"]
    turn_count = state["plot"]["pacing"]["turn_count"]
    
    if "emergent_directions" not in main:
        main["emergent_directions"] = []
    
    direction = {
        "title": title,
        "description": description,
        "turn": turn_count,
        "promoted": False
    }
    
    main["emergent_directions"].append(direction)
    print(f"Noted emergent direction: {title}")


def promote_emergent_to_act(state, emergent_index, position=None):
    """Promote an emergent direction into a full act."""
    main = state["plot"]["main_thread"]
    
    if "emergent_directions" not in main or emergent_index >= len(main["emergent_directions"]):
        print("Error: Invalid emergent direction index")
        return
    
    direction = main["emergent_directions"][emergent_index]
    
    # Create act from emergent direction
    add_act(state, direction["title"], direction["description"], position)
    
    # Mark as promoted
    direction["promoted"] = True
    
    print(f"Promoted emergent direction '{direction['title']}' to full act")


def create_alternate_thread(state, thread_id, title, description):
    """Create an alternate plot thread that can run parallel to main."""
    plot = state["plot"]
    
    if "alternate_threads" not in plot:
        plot["alternate_threads"] = {}
    
    thread = {
        "id": thread_id,
        "title": title,
        "description": description,
        "active": False,
        "current_stage": 1,
        "stages": []
    }
    
    plot["alternate_threads"][thread_id] = thread
    print(f"Created alternate thread: {title}")


def toggle_thread_focus(state, thread_id=None):
    """Switch primary focus between main thread and an alternate."""
    plot = state["plot"]
    main = plot["main_thread"]
    
    if thread_id is None:
        # Switch back to main
        main["is_primary_focus"] = True
        for alt_id, alt in plot.get("alternate_threads", {}).items():
            alt["active"] = False
        print("Switched primary focus to main thread")
    else:
        # Switch to alternate
        if thread_id not in plot.get("alternate_threads", {}):
            print(f"Error: Thread '{thread_id}' not found")
            return
        
        main["is_primary_focus"] = False
        plot["alternate_threads"][thread_id]["active"] = True
        print(f"Switched primary focus to alternate thread: {plot['alternate_threads'][thread_id]['title']}")


def add_player_goal(state, goal_description):
    """Record a player-driven goal that emerged during play."""
    steering = state["plot"].setdefault("thread_steering", {})
    turn_count = state["plot"]["pacing"]["turn_count"]
    
    if "player_driven_goals" not in steering:
        steering["player_driven_goals"] = []
    
    goal = {
        "description": goal_description,
        "turn": turn_count,
        "active": True
    }
    
    steering["player_driven_goals"].append(goal)
    print(f"Recorded player goal: {goal_description}")


def add_emerging_theme(state, theme):
    """Note a theme that's emerging in the story."""
    steering = state["plot"].setdefault("thread_steering", {})
    
    if "emerging_themes" not in steering:
        steering["emerging_themes"] = []
    
    if theme not in steering["emerging_themes"]:
        steering["emerging_themes"].append(theme)
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
    "subplot": ("title", "description", "priority", "ties_to_main_plot"),
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


def stage_steering_seed(state, note):
    """Generate a draft character/subplot/direction from a freeform note (see
    story_engine.generate_steering_seed) and hold it in plot.thread_steering.pending_seeds
    for review - nothing is committed to the save until apply_steering_seed confirms it.
    Returns the new seed's id, or None if generation failed (bad/empty LLM output - same
    "costs nothing, just try again" failure mode as generate_new_subplot)."""
    generated = story_engine.generate_steering_seed(state, note)
    if generated is None:
        print("Could not generate a seed from that note - try rephrasing it.")
        return None

    steering = state["plot"].setdefault("thread_steering", {})
    pending = steering.setdefault("pending_seeds", [])
    seed_id = _next_seed_id(pending)
    pending.append({
        "id": seed_id,
        "note": note,
        "turn_added": state["plot"]["pacing"]["turn_count"],
        "type": generated["type"],
        "draft": generated["draft"],
    })

    print(f"Staged {generated['type']} seed '{seed_id}' for review:")
    for key, value in generated["draft"].items():
        print(f"  {key}: {value}")
    print(f"Run 'seed-apply {seed_id}' to commit it (pass --<field> '<override>' to edit "
          f"a value first), or 'seed-discard {seed_id}' to drop it.")
    return seed_id


def apply_steering_seed(state, seed_id, **overrides):
    """Commit a staged seed (see stage_steering_seed) to the save, applying any field
    overrides the player supplied to edit it first. Dispatches by the seed's type into the
    same shapes the rest of the engine already produces/expects, so a seeded addition is
    indistinguishable from one the story generated on its own:
    - character -> new `characters` entry, introduced: false (surfaces via
      story_engine.generate_pacing_nudge until the player actually meets them - see
      update_progress_from_turn's auto-flip when they do).
    - subplot -> story_engine.insert_subplot, the same insertion generate_new_subplot uses.
    - direction -> an emergent_directions entry, the same shape add_emergent_direction
      already produces (and, since the pacing-nudge fix, actually read back now)."""
    steering = state["plot"].setdefault("thread_steering", {})
    pending = steering.setdefault("pending_seeds", [])
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
        characters = state.setdefault("characters", {})
        char_id = f"char_{len(characters) + 1:03d}"
        while char_id in characters:
            char_id = f"char_{int(char_id.rsplit('_', 1)[-1]) + 1:03d}"
        characters[char_id] = {
            "type": "npc",
            "name": fields.get("name", ""),
            "description": fields.get("description", ""),
            "role": fields.get("role", ""),
            "relationship_to_player": fields.get("relationship_to_player", ""),
            "hook": fields.get("hook", ""),
            "introduced": False,
            "seed_note": seed["note"],
        }
        result_id = char_id
        print(f"Added character '{fields.get('name', '')}' ({char_id})")
    elif seed_type == "subplot":
        result_id = story_engine.insert_subplot(
            state, fields.get("title", ""), fields.get("description", ""),
            priority=fields.get("priority", "medium"),
            ties_to_main_plot=fields.get("ties_to_main_plot", ""),
        )
        print(f"Added subplot '{fields.get('title', '')}' ({result_id})")
    elif seed_type == "direction":
        directions = state["plot"]["main_thread"].setdefault("emergent_directions", [])
        directions.append({
            "title": fields.get("title", ""),
            "description": fields.get("description", ""),
            "turn": state["plot"]["pacing"]["turn_count"],
            "promoted": False,
        })
        result_id = None
        print(f"Noted direction: {fields.get('title', '')}")
    else:
        print(f"Error: unknown seed type '{seed_type}'")
        return None

    pending.remove(seed)
    return result_id


def list_pending_seeds(state):
    """Print every pending seed's full draft (unlike show_plot_overview's one-line-each
    summary) so the player has enough detail to decide whether to seed-apply as-is, with
    overrides, or seed-discard."""
    pending = state["plot"].get("thread_steering", {}).get("pending_seeds", [])
    if not pending:
        print("No pending seeds.")
        return
    for seed in pending:
        print(f"{seed['id']} [{seed['type']}] - from note: \"{seed['note']}\"")
        for key, value in seed["draft"].items():
            print(f"  {key}: {value}")
        print()


def discard_steering_seed(state, seed_id):
    """Drop a pending seed without applying it."""
    steering = state["plot"].setdefault("thread_steering", {})
    pending = steering.setdefault("pending_seeds", [])
    seed = next((s for s in pending if s["id"] == seed_id), None)
    if seed is None:
        print(f"Error: no pending seed '{seed_id}'")
        return
    pending.remove(seed)
    print(f"Discarded seed '{seed_id}'")


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
        print("  python plot_manager.py seed-discard <seed_id>")
        print("\nModifying:")
        print("  python plot_manager.py modify-act <act_number> --title '<new title>' --description '<new desc>'")
        print("  python plot_manager.py pivot '<new title>' '<new description>' '<reason>'")
        print("\nPromoting:")
        print("  python plot_manager.py promote-emergent <index> [position]")
        print("\nAlternate Threads:")
        print("  python plot_manager.py create-alt '<thread_id>' '<title>' '<description>'")
        print("  python plot_manager.py focus [thread_id]")
        return

    state = state_store.load_state(user_id, story_slug)
    command = argv[1]
    
    if command == "overview":
        show_plot_overview(state)
    
    elif command == "add-act":
        if len(argv) < 4:
            print("Usage: python plot_manager.py add-act '<title>' '<description>' [position] [--optional]")
            return
        title = argv[2]
        description = argv[3]
        position = int(argv[4]) if len(argv) > 4 and argv[4].isdigit() else None
        optional = "--optional" in argv
        add_act(state, title, description, position, optional=optional)
        state_store.save_state(state, user_id, story_slug)
    
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
        modify_act(state, act_num, **kwargs)
        state_store.save_state(state, user_id, story_slug)
    
    elif command == "pivot":
        if len(argv) < 5:
            print("Usage: python plot_manager.py pivot '<new title>' '<new description>' '<reason>'")
            return
        new_title = argv[2]
        new_desc = argv[3]
        reason = argv[4]
        pivot_main_plot(state, new_title, new_desc, reason)
        state_store.save_state(state, user_id, story_slug)
    
    elif command == "add-emergent":
        if len(argv) < 4:
            print("Usage: python plot_manager.py add-emergent '<title>' '<description>'")
            return
        title = argv[2]
        desc = argv[3]
        add_emergent_direction(state, title, desc)
        state_store.save_state(state, user_id, story_slug)
    
    elif command == "promote-emergent":
        if len(argv) < 3:
            print("Usage: python plot_manager.py promote-emergent <index> [position]")
            return
        index = int(argv[2])
        position = int(argv[3]) if len(argv) > 3 else None
        promote_emergent_to_act(state, index, position)
        state_store.save_state(state, user_id, story_slug)
    
    elif command == "create-alt":
        if len(argv) < 5:
            print("Usage: python plot_manager.py create-alt '<thread_id>' '<title>' '<description>'")
            return
        thread_id = argv[2]
        title = argv[3]
        desc = argv[4]
        create_alternate_thread(state, thread_id, title, desc)
        state_store.save_state(state, user_id, story_slug)
    
    elif command == "focus":
        thread_id = argv[2] if len(argv) > 2 else None
        toggle_thread_focus(state, thread_id)
        state_store.save_state(state, user_id, story_slug)
    
    elif command == "add-goal":
        if len(argv) < 3:
            print("Usage: python plot_manager.py add-goal '<goal description>'")
            return
        goal = argv[2]
        add_player_goal(state, goal)
        state_store.save_state(state, user_id, story_slug)
    
    elif command == "add-theme":
        if len(argv) < 3:
            print("Usage: python plot_manager.py add-theme '<theme>'")
            return
        theme = argv[2]
        add_emerging_theme(state, theme)
        state_store.save_state(state, user_id, story_slug)

    elif command == "seed":
        if len(argv) < 3:
            print("Usage: python plot_manager.py seed '<freeform note>'")
            return
        note = argv[2]
        stage_steering_seed(state, note)
        state_store.save_state(state, user_id, story_slug)

    elif command == "seed-list":
        list_pending_seeds(state)

    elif command == "seed-apply":
        if len(argv) < 3:
            print("Usage: python plot_manager.py seed-apply <seed_id> [--name/--title '<override>'] "
                  "[--description '<override>'] [--role '<override>'] [--relationship '<override>'] "
                  "[--hook '<override>'] [--priority '<override>'] [--ties '<override>']")
            return
        seed_id = argv[2]
        kwargs = {}
        i = 3
        flag_map = {
            "--name": "name", "--title": "title", "--description": "description",
            "--role": "role", "--relationship": "relationship_to_player",
            "--hook": "hook", "--priority": "priority", "--ties": "ties_to_main_plot",
        }
        while i < len(argv):
            if argv[i] in flag_map and i + 1 < len(argv):
                kwargs[flag_map[argv[i]]] = argv[i + 1]
                i += 2
            else:
                i += 1
        apply_steering_seed(state, seed_id, **kwargs)
        state_store.save_state(state, user_id, story_slug)

    elif command == "seed-discard":
        if len(argv) < 3:
            print("Usage: python plot_manager.py seed-discard <seed_id>")
            return
        seed_id = argv[2]
        discard_steering_seed(state, seed_id)
        state_store.save_state(state, user_id, story_slug)

    else:
        print(f"Unknown command: {command}")
        print("Run 'python plot_manager.py' for usage")


if __name__ == "__main__":
    main()

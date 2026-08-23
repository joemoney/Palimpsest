#!/usr/bin/env python3
"""
Plot Management Tool for Mid-Adventure Steering
Allows dynamic modification of main plot, acts, and story direction during gameplay.
"""
import sys
from datetime import datetime

import state_store


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
    
    else:
        print(f"Unknown command: {command}")
        print("Run 'python plot_manager.py' for usage")


if __name__ == "__main__":
    main()

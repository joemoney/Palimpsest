#!/usr/bin/env python3
"""
Subplot and Pacing Management Tool
Manually inspect and adjust subplot progress, main plot advancement, and pacing state.
"""
import sys

import state_store
import story_engine


def show_status(state):
    """Display current plot and subplot status."""
    plot = state["plot"]
    main = plot["main_thread"]
    
    print("\n" + "=" * 60)
    print("MAIN PLOT STATUS")
    print("=" * 60)
    print(f"Current Act: {main['current_act']} - {main['acts'][main['current_act']-1]['title']}")
    print(f"Description: {main['acts'][main['current_act']-1]['description']}")
    print()
    
    # Acts advance based on the pacing director's qualitative judgment, not fixed
    # numeric thresholds - these are just informational signals it considers.
    signals = main['acts'][main['current_act']-1].get('completion_signals', [])
    if signals:
        print(f"Signals this act was built around: {', '.join(signals)}")
    print(f"Subplots completed this act: {plot['pacing']['subplots_completed_this_act']}")
    revealed = sum(1 for f in state['player']['origin']['memory_fragments'] if f['revealed'])
    print(f"Memory fragments revealed: {revealed}")
    print(f"Architect encounters: {plot['entity_interaction_count']}")

    endgame = plot.get("endgame", {})
    if endgame.get("requested"):
        print(f"\n*** ENDGAME IN PROGRESS: {endgame.get('final_arc', {}).get('title', '')} ***")
    
    print()
    print("=" * 60)
    print("SUBPLOTS")
    print("=" * 60)
    
    for sid, subplot in plot["subplots"].items():
        status_icon = "✓" if subplot["status"] == "completed" else "●" if subplot["active"] else "○"
        priority = subplot["priority"].upper()
        progress_bar = "█" * int(subplot["progress"] / 10) + "░" * (10 - int(subplot["progress"] / 10))
        
        print(f"{status_icon} [{priority:6}] {subplot['title']}")
        print(f"   Status: {subplot['status']} | Progress: [{progress_bar}] {subplot['progress']}/{subplot['completion_threshold']}")
        print(f"   {subplot['description']}")
        print()
    
    print("=" * 60)
    print("PACING")
    print("=" * 60)
    print(f"Turn count: {plot['pacing']['turn_count']}")
    print(f"Turns since pacing nudge: {plot['pacing']['turns_since_last_pacing_nudge']} / {plot['pacing']['pacing_nudge_frequency']}")
    print(f"Max parallel subplots: {plot['pacing']['max_parallel_subplots']}")
    print(f"Ready for main plot advancement: {plot['pacing']['ready_for_main_plot_advancement']}")
    if plot['pacing']['last_pacing_direction']:
        print(f"Last pacing direction: {plot['pacing']['last_pacing_direction']}")
    print()


def update_subplot_progress(state, subplot_id, progress_delta):
    """Increase or decrease subplot progress."""
    if subplot_id not in state["plot"]["subplots"]:
        print(f"Error: Subplot '{subplot_id}' not found")
        return
    
    subplot = state["plot"]["subplots"][subplot_id]
    old_progress = subplot["progress"]
    subplot["progress"] = max(0, min(subplot["completion_threshold"], old_progress + progress_delta))
    
    print(f"Updated '{subplot['title']}' progress: {old_progress} → {subplot['progress']}")
    
    # Auto-complete if threshold reached
    if subplot["progress"] >= subplot["completion_threshold"] and subplot["status"] != "completed":
        subplot["status"] = "completed"
        subplot["active"] = False
        if subplot_id not in state["plot"]["completed_subplots"]:
            state["plot"]["completed_subplots"].append(subplot_id)
            state["plot"]["pacing"]["subplots_completed_this_act"] += 1
        print(f"  → Subplot '{subplot['title']}' marked as COMPLETED!")

        new_id = story_engine.generate_new_subplot(state)
        if new_id:
            print(f"  → Generated replacement subplot: '{state['plot']['subplots'][new_id]['title']}' ({new_id})")


def activate_subplot(state, subplot_id):
    """Activate an inactive subplot."""
    if subplot_id not in state["plot"]["subplots"]:
        print(f"Error: Subplot '{subplot_id}' not found")
        return
    
    subplot = state["plot"]["subplots"][subplot_id]
    if subplot["active"]:
        print(f"Subplot '{subplot['title']}' is already active")
        return
    
    # Count currently active
    active_count = sum(1 for sp in state["plot"]["subplots"].values() if sp["active"])
    max_parallel = state["plot"]["pacing"]["max_parallel_subplots"]
    
    if active_count >= max_parallel:
        print(f"Warning: Already at max parallel subplots ({max_parallel})")
        print("Activating anyway...")
    
    subplot["active"] = True
    subplot["status"] = "active"
    print(f"Activated subplot: '{subplot['title']}'")


def advance_act(state):
    """Manually force-complete the current act. If no next act exists yet, it's normally
    left for the automatic pacing director (story_engine.check_and_advance_act) to
    generate one during play - this just marks the current act done and clears the way."""
    main = state["plot"]["main_thread"]
    current = main["current_act"]

    if state["plot"].get("endgame", {}).get("requested"):
        print("Story is in its endgame - acts no longer advance.")
        return

    # Mark current act as completed
    main["acts"][current - 1]["completed"] = True

    # Reset subplot completion counter for the next act
    state["plot"]["pacing"]["subplots_completed_this_act"] = 0
    state["plot"]["pacing"]["ready_for_main_plot_advancement"] = False

    if current >= len(main["acts"]):
        print(f"Act {current} marked completed. No next act exists yet - "
              f"one will be generated automatically during play.")
        return

    main["current_act"] = current + 1
    print(f"Advanced to Act {main['current_act']}: {main['acts'][current]['title']}")


def reveal_memory_fragment(state, fragment_id):
    """Reveal a memory fragment."""
    for frag in state["player"]["origin"]["memory_fragments"]:
        if frag["id"] == fragment_id:
            if frag["revealed"]:
                print(f"Fragment '{fragment_id}' is already revealed")
            else:
                frag["revealed"] = True
                print(f"Revealed memory fragment: {fragment_id}")
                print(f"Content: {frag['content']}")
            return
    print(f"Error: Fragment '{fragment_id}' not found")


def main():
    user_id, story_slug, parsed_argv = state_store.parse_user_story_args(sys.argv[1:])
    # keep a program-name placeholder at index 0 so every sys.argv[N] below still
    # lines up positionally, now with --user/--story already stripped out
    argv = [sys.argv[0]] + parsed_argv

    if len(argv) < 2:
        print("Usage:")
        print("  (add --user <id> --story <slug> anywhere to target a specific save;")
        print("   defaults to the local single-player save)")
        print("  python subplot_manager.py status")
        print("  python subplot_manager.py progress <subplot_id> <+/- amount>")
        print("  python subplot_manager.py activate <subplot_id>")
        print("  python subplot_manager.py advance-act")
        print("  python subplot_manager.py reveal <fragment_id>")
        return

    state = state_store.load_state(user_id, story_slug)
    command = argv[1]

    if command == "status":
        show_status(state)

    elif command == "progress":
        if len(argv) < 4:
            print("Usage: python subplot_manager.py progress <subplot_id> <+/- amount>")
            return
        subplot_id = argv[2]
        delta = int(argv[3])
        update_subplot_progress(state, subplot_id, delta)
        state_store.save_state(state, user_id, story_slug)

    elif command == "activate":
        if len(argv) < 3:
            print("Usage: python subplot_manager.py activate <subplot_id>")
            return
        subplot_id = argv[2]
        activate_subplot(state, subplot_id)
        state_store.save_state(state, user_id, story_slug)

    elif command == "advance-act":
        advance_act(state)
        state_store.save_state(state, user_id, story_slug)

    elif command == "reveal":
        if len(argv) < 3:
            print("Usage: python subplot_manager.py reveal <fragment_id>")
            return
        fragment_id = argv[2]
        reveal_memory_fragment(state, fragment_id)
        state_store.save_state(state, user_id, story_slug)

    else:
        print(f"Unknown command: {command}")


if __name__ == "__main__":
    main()

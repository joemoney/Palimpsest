#!/usr/bin/env python3
"""
Subplot and Pacing Management Tool
Manually inspect and adjust subplot progress, main plot advancement, and pacing state.
"""
import sys

import state_store
import story_engine

# modify_subplot's whitelist of directly-editable descriptive fields - deliberately not
# progress/status/active/completion_threshold, which have dedicated commands
# (update_subplot_progress, activate_subplot) with their own completion/regeneration side
# effects a blind key overwrite here would bypass. A fixed list rather than "whatever keys
# already exist on the runtime entry" - a *seeded* subplot's runtime entry only carries
# progress/status/active until first modified here, so "already present" would reject every
# legitimate edit to one.
_MODIFIABLE_SUBPLOT_FIELDS = ("title", "description", "priority", "ties_to_main_plot")


def show_status(ctx):
    """Display current plot and subplot status."""
    plot_state = ctx["state"]["plot"]
    pacing_state = ctx["state"]["pacing"]
    pacing_story = ctx["story"]["plot"]["pacing"]
    current_act = story_engine._current_act(ctx)

    print("\n" + "=" * 60)
    print("MAIN PLOT STATUS")
    print("=" * 60)
    if current_act:
        print(f"Current Act: {current_act['act_number']} - {current_act['title']}")
        print(f"Description: {current_act['description']}")
        print()
        # Acts advance based on the pacing director's qualitative judgment, not fixed
        # numeric thresholds - these are just informational signals it considers.
        if current_act.get("completion_signals"):
            print(f"Signals this act was built around: {', '.join(current_act['completion_signals'])}")
    print(f"Subplots completed this act: {pacing_state['subplots_completed_this_act']}")
    revelations = ctx["story"].get("mechanics", {}).get("revelations", [])
    revealed = len(plot_state["revelations_revealed"])
    if revelations:
        print(f"Memory fragments revealed: {revealed}/{len(revelations)}")
    tracked_entity = ctx["story"].get("mechanics", {}).get("tracked_entity")
    if tracked_entity:
        print(f"{tracked_entity['name']} encounters: {plot_state['entity_contact_count']}")

    endgame = plot_state.get("endgame", {})
    if endgame.get("requested"):
        print(f"\n*** ENDGAME IN PROGRESS: {endgame.get('final_arc', {}).get('title', '')} ***")

    print()
    print("=" * 60)
    print("SUBPLOTS")
    print("=" * 60)

    for sid, subplot in story_engine._all_subplots(ctx).items():
        status_icon = "✓" if subplot["status"] == "completed" else "●" if subplot["active"] else "○"
        priority = subplot["priority"].upper()
        # Scaled against this subplot's own completion_threshold, not a hardcoded /10 assuming
        # 100 - a "multi_act" subplot (see story_engine.insert_subplot) has a higher threshold
        # (MULTI_ACT_SUBPLOT_THRESHOLD), so the old fixed-scale math would overfill the bar.
        filled = int(10 * subplot["progress"] / subplot["completion_threshold"]) if subplot["completion_threshold"] else 0
        progress_bar = "█" * filled + "░" * max(0, 10 - filled)
        span_tag = " [multi-act]" if subplot.get("span") == "multi_act" else ""

        print(f"{status_icon} [{priority:6}] {subplot['title']}{span_tag}")
        print(f"   {sid} | Status: {subplot['status']} | Progress: [{progress_bar}] {subplot['progress']}/{subplot['completion_threshold']}")
        print(f"   {subplot['description']}")
        print()

    print("=" * 60)
    print("PACING")
    print("=" * 60)
    print(f"Turn count: {pacing_state['turn_count']}")
    print(f"Turns since pacing nudge: {pacing_state['turns_since_nudge']} / {pacing_story['nudge_frequency']}")
    print(f"Max parallel subplots: {pacing_story['max_parallel_subplots']}")
    if pacing_state['last_direction']:
        print(f"Last pacing direction: {pacing_state['last_direction']}")
    print()


def update_subplot_progress(ctx, subplot_id, progress_delta):
    """Increase or decrease subplot progress."""
    subplots_state = ctx["state"]["plot"]["subplots"]
    if subplot_id not in subplots_state:
        print(f"Error: Subplot '{subplot_id}' not found")
        return

    view = story_engine._subplot_view(ctx, subplot_id)
    old_progress = view["progress"]
    new_progress = max(0, min(view["completion_threshold"], old_progress + progress_delta))
    subplots_state[subplot_id]["progress"] = new_progress

    print(f"Updated '{view['title']}' progress: {old_progress} → {new_progress}")

    # Auto-complete if threshold reached
    if new_progress >= view["completion_threshold"] and view["status"] != "completed":
        subplots_state[subplot_id]["status"] = "completed"
        subplots_state[subplot_id]["active"] = False
        completed = ctx["state"]["plot"]["completed_subplots"]
        if subplot_id not in completed:
            completed.append(subplot_id)
            ctx["state"]["pacing"]["subplots_completed_this_act"] += 1
        print(f"  → Subplot '{view['title']}' marked as COMPLETED!")

        new_id = story_engine.generate_new_subplot(ctx)
        if new_id:
            new_title = story_engine._subplot_view(ctx, new_id)["title"]
            print(f"  → Generated replacement subplot: '{new_title}' ({new_id})")


def activate_subplot(ctx, subplot_id):
    """Activate an inactive subplot."""
    subplots_state = ctx["state"]["plot"]["subplots"]
    if subplot_id not in subplots_state:
        print(f"Error: Subplot '{subplot_id}' not found")
        return

    view = story_engine._subplot_view(ctx, subplot_id)
    if view["active"]:
        print(f"Subplot '{view['title']}' is already active")
        return

    all_subplots = story_engine._all_subplots(ctx)
    active_count = sum(1 for sp in all_subplots.values() if sp["active"])
    max_parallel = ctx["story"]["plot"]["pacing"]["max_parallel_subplots"]

    if active_count >= max_parallel:
        print(f"Warning: Already at max parallel subplots ({max_parallel})")
        print("Activating anyway...")

    subplots_state[subplot_id]["active"] = True
    subplots_state[subplot_id]["status"] = "active"
    print(f"Activated subplot: '{view['title']}'")


def modify_subplot(ctx, subplot_id, **kwargs):
    """Modify an existing subplot's descriptive properties (title, description, priority,
    ties_to_main_plot) - mirrors plot_manager.py's modify_act. Writes the override directly
    onto the runtime entry, which the merge-view (story_engine._subplot_view) already
    prefers over the template's seed value once present - works the same whether the
    subplot was seeded or generated."""
    if subplot_id not in ctx["state"]["plot"]["subplots"]:
        print(f"Error: Subplot '{subplot_id}' not found")
        return

    subplot = ctx["state"]["plot"]["subplots"][subplot_id]
    modified = []
    for key, value in kwargs.items():
        if key in _MODIFIABLE_SUBPLOT_FIELDS:
            subplot[key] = value
            modified.append(key)

    if modified:
        print(f"Modified subplot '{subplot_id}': {', '.join(modified)}")
    else:
        print(f"No changes made to subplot '{subplot_id}'")


def advance_act(ctx):
    """Manually force-complete the current act. If no next act exists yet, it's normally
    left for the automatic pacing director (story_engine.check_and_advance_act) to
    generate one during play - this just marks the current act done and clears the way."""
    if ctx["state"]["plot"].get("endgame", {}).get("requested"):
        print("Story is in its endgame - acts no longer advance.")
        return

    current = story_engine._current_act(ctx)
    if not current:
        print("Error: current act not found")
        return

    story_engine._mark_act_completed(ctx, current["act_number"])
    ctx["state"]["pacing"]["subplots_completed_this_act"] = 0

    next_act = next(
        (a for a in story_engine._all_acts(ctx) if a["act_number"] == current["act_number"] + 1),
        None,
    )
    if next_act is None:
        print(f"Act {current['act_number']} marked completed. No next act exists yet - "
              f"one will be generated automatically during play.")
        return

    ctx["state"]["plot"]["current_act"] = next_act["act_number"]
    print(f"Advanced to Act {next_act['act_number']}: {next_act['title']}")


def reveal_memory_fragment(ctx, fragment_id):
    """Reveal a memory fragment (mechanics.revelations entry)."""
    revelations = ctx["story"].get("mechanics", {}).get("revelations", [])
    fragment = next((r for r in revelations if r["id"] == fragment_id), None)
    if fragment is None:
        print(f"Error: Fragment '{fragment_id}' not found")
        return
    revealed = ctx["state"]["plot"]["revelations_revealed"]
    if fragment_id in revealed:
        print(f"Fragment '{fragment_id}' is already revealed")
        return
    revealed[fragment_id] = {"turn": ctx["state"]["pacing"]["turn_count"]}
    print(f"Revealed memory fragment: {fragment_id}")
    print(f"Content: {fragment['content']}")


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
        print("  python subplot_manager.py modify-subplot <subplot_id> --title '<new>' "
              "--description '<new>' --priority '<high|medium|low>' --ties '<new>'")
        print("  python subplot_manager.py advance-act")
        print("  python subplot_manager.py reveal <fragment_id>")
        return

    ctx = state_store.load_state(user_id, story_slug)
    command = argv[1]

    if command == "status":
        show_status(ctx)

    elif command == "progress":
        if len(argv) < 4:
            print("Usage: python subplot_manager.py progress <subplot_id> <+/- amount>")
            return
        subplot_id = argv[2]
        delta = int(argv[3])
        update_subplot_progress(ctx, subplot_id, delta)
        state_store.save_state(ctx, user_id, story_slug)

    elif command == "activate":
        if len(argv) < 3:
            print("Usage: python subplot_manager.py activate <subplot_id>")
            return
        subplot_id = argv[2]
        activate_subplot(ctx, subplot_id)
        state_store.save_state(ctx, user_id, story_slug)

    elif command == "modify-subplot":
        if len(argv) < 3:
            print("Usage: python subplot_manager.py modify-subplot <subplot_id> --title '<new>' "
                  "--description '<new>' --priority '<high|medium|low>' --ties '<new>'")
            return
        subplot_id = argv[2]
        kwargs = {}
        i = 3
        while i < len(argv):
            if argv[i] == "--title" and i + 1 < len(argv):
                kwargs["title"] = argv[i + 1]
                i += 2
            elif argv[i] == "--description" and i + 1 < len(argv):
                kwargs["description"] = argv[i + 1]
                i += 2
            elif argv[i] == "--priority" and i + 1 < len(argv):
                kwargs["priority"] = argv[i + 1]
                i += 2
            elif argv[i] == "--ties" and i + 1 < len(argv):
                kwargs["ties_to_main_plot"] = argv[i + 1]
                i += 2
            else:
                i += 1
        modify_subplot(ctx, subplot_id, **kwargs)
        state_store.save_state(ctx, user_id, story_slug)

    elif command == "advance-act":
        advance_act(ctx)
        state_store.save_state(ctx, user_id, story_slug)

    elif command == "reveal":
        if len(argv) < 3:
            print("Usage: python subplot_manager.py reveal <fragment_id>")
            return
        fragment_id = argv[2]
        reveal_memory_fragment(ctx, fragment_id)
        state_store.save_state(ctx, user_id, story_slug)

    else:
        print(f"Unknown command: {command}")


if __name__ == "__main__":
    main()

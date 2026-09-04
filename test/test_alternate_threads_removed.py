"""Regression test for CR-10: plot.alternate_threads was entirely write-only (created and
focus-switched via plot_manager.py/the web UI, but never read by any prompt) - a UI
affordance that silently did nothing. This asserts the feature is actually gone rather than
just unused: the commands report as unknown, the fields aren't in fresh templates, the CLI's
own advertised command list stops mentioning them, and a v1 save carrying the now-dead keys
migrates cleanly (they're simply dropped, not carried forward).

Run directly: python3 test/test_alternate_threads_removed.py
"""
import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import load_story_engine  # noqa: E402

se = load_story_engine()
import plot_manager as pm  # noqa: E402
import migrate_v1  # noqa: E402

assert not hasattr(pm, "create_alternate_thread")
assert not hasattr(pm, "toggle_thread_focus")
print("OK: create_alternate_thread/toggle_thread_focus no longer exist in plot_manager")

ctx = se.state_store.load_state("altthreadstest", se.state_store.DEFAULT_STORY_SLUG)
assert "alternate_threads" not in ctx["state"]["plot"]
assert "is_primary_focus" not in ctx["story"]["plot"]["main_thread"]
assert "can_pivot" not in ctx["story"]["plot"]["main_thread"]
print("OK: alternate_threads/is_primary_focus/can_pivot absent from a fresh save/template")


def run_cli(*args):
    buf = io.StringIO()
    sys.argv = ["plot_manager.py", "--user", "local-cli", "--story",
                se.state_store.DEFAULT_STORY_SLUG] + list(args)
    with contextlib.redirect_stdout(buf):
        pm.main()
    return buf.getvalue()


out = run_cli("create-alt", "thread_x", "Title", "Desc")
assert "Unknown command" in out, out
out = run_cli("focus", "thread_x")
assert "Unknown command" in out, out
print("OK: create-alt/focus report as unknown commands")

usage = run_cli()
assert "create-alt" not in usage
assert "focus" not in usage
print("OK: usage output no longer advertises create-alt/focus")

assert "create-alt" not in se.STEER_WARNING
assert "'focus'" not in se.STEER_WARNING
print("OK: STEER_WARNING no longer advertises the removed commands")

# --- a v1 save carrying the now-dead keys migrates cleanly - they're simply dropped ---
template = se.state_store.load_template_raw(se.state_store.DEFAULT_STORY_SLUG)
v1_save = {
    "meta": {"title": "t"},
    "player": {"name": "n", "traits": [], "inventory": [], "stats": {},
               "flags_active": {}, "flags_meta": {}, "flags_archive": {},
               "origin": {"memory_fragments": []}},
    "characters": {},
    "plot": {
        "main_thread": {"title": "t", "description": "d", "is_primary_focus": True, "can_pivot": True,
                         "current_act": 1, "acts": [
                             {"act_number": 1, "title": "Act 1", "description": "d", "completed": False, "optional": False}
                         ], "act_history": [], "emergent_directions": []},
        "alternate_threads": {"thread_x": {"id": "thread_x", "title": "t", "description": "d", "active": False}},
        "thread_steering": {"last_pivot_turn": 0, "pivot_history": [], "emerging_themes": [], "player_driven_goals": []},
        "subplots": {}, "completed_subplots": [], "entity_interaction_count": 0,
        "endgame": {"requested": False, "requested_turn": None, "final_arc": None, "concluded": False},
        "pacing": {"turn_count": 0, "turns_since_last_pacing_nudge": 0, "pacing_nudge_frequency": 8,
                   "turns_since_last_act_check": 0, "act_check_frequency": 12, "max_parallel_subplots": 3,
                   "subplots_completed_this_act": 0, "ready_for_main_plot_advancement": False,
                   "last_pacing_direction": ""},
        "current_scene": {"location": "", "summary": "", "present_npcs": []},
        "opening_scene": {"played": False, "narration_before_name": "", "narration_after_name": ""},
    },
    "history_log": {"recent_turns": [], "compressed_summary": "", "full_transcript": []},
}
migrated = migrate_v1.migrate(v1_save, se.state_store.DEFAULT_STORY_SLUG, template)
assert "alternate_threads" not in migrated["plot"]
assert "is_primary_focus" not in str(migrated)  # nowhere in the migrated output at all
prompt_ctx = {"story": se.state_store.load_template(se.state_store.DEFAULT_STORY_SLUG), "state": migrated}
prompt = se.build_system_prompt(prompt_ctx)
assert prompt  # renders without raising despite the dropped legacy keys
print("OK: a v1 save carrying the old alternate_threads/is_primary_focus/can_pivot keys "
      "migrates cleanly, dropping them entirely")

print("\nALL CHECKS PASSED: test_alternate_threads_removed")

"""Regression test for story_engine.handle_steer_command(): the in-session 'steer'
command should print the destructive-action warning and forward the typed arguments
- plus the acting user/story - to plot_manager.py's CLI via subprocess, with proper
shell-style quote parsing. subprocess.run is mocked so this never actually shells
out or touches real data.

Run directly: python3 test/test_steer_command.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import load_story_engine  # noqa: E402

se = load_story_engine()

calls = []


def fake_run(cmd, *a, **k):
    calls.append(cmd)

    class FakeResult:
        returncode = 0
    return FakeResult()


se.subprocess.run = fake_run

# Absolute path, not a bare "plot_manager.py" - handle_steer_command builds it from
# story_engine.py's own __file__ so the subprocess call works regardless of cwd (both
# live together under backend/, wherever that ends up).
plot_manager_path = os.path.join(os.path.dirname(os.path.abspath(se.__file__)), "plot_manager.py")
PREFIX = [sys.executable, plot_manager_path, "--user", se.state_store.DEFAULT_USER_ID,
          "--story", se.state_store.DEFAULT_STORY_SLUG]

# --- warning must be shown every time, not just once ---
import io
import contextlib

buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    se.handle_steer_command("overview")
printed = buf.getvalue()
assert "STEERING MODE" in printed, "expected the destructive-action warning to print"
assert "break story coherence" in printed or "coherence" in printed.lower()
print("OK: steering warning is printed")

# --- simple single-word command forwards correctly, with default user/story ---
assert calls[-1] == PREFIX + ["overview"], calls[-1]
print("OK: 'steer overview' forwards to plot_manager.py overview with default user/story")

# --- quoted multi-arg commands are parsed shell-style, matching plot_manager.py's own docs ---
se.handle_steer_command("add-goal 'Player wants to rescue trapped AI'")
assert calls[-1] == PREFIX + ["add-goal", "Player wants to rescue trapped AI"], calls[-1]
print("OK: quoted arguments are parsed correctly (shlex)")

se.handle_steer_command("pivot 'New Main Goal' 'Updated description' 'Why we pivoted'")
assert calls[-1] == PREFIX + ["pivot", "New Main Goal", "Updated description", "Why we pivoted"], calls[-1]
print("OK: multi-argument pivot command forwards all three quoted args")

# --- bare 'steer' with no arguments still runs plot_manager.py (shows its own usage) ---
se.handle_steer_command("")
assert calls[-1] == PREFIX, calls[-1]
print("OK: empty steer command runs plot_manager.py with no args (usage text)")

# --- a non-default user/story gets threaded through to the subprocess call ---
se.handle_steer_command("overview", user_id="alice", story_slug="another_story")
assert calls[-1] == [
    sys.executable, plot_manager_path, "--user", "alice", "--story", "another_story", "overview",
], calls[-1]
print("OK: explicit user_id/story_slug are forwarded instead of the defaults")

print("\nALL CHECKS PASSED: test_steer_command")

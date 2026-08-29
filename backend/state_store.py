"""Storage layer for multi-user, multi-story play.

Two kinds of content, deliberately kept apart:
- stories/<slug>/template.json - authored seed content, committed to git.
  Adding a new story is a content change (drop in a new template.json), not
  a code change.
- data/ - runtime-only (gitignored): accounts.db (SQLite, just for account
  credentials - the one place an atomic uniqueness check actually matters)
  and saves/<user_id>/<story_slug>.json (one live save per user per story,
  plain JSON files - each has exactly one owner and needs no cross-user
  queries, so a database buys nothing here).
"""
import json
import os
import re
import sqlite3
import time
import uuid

import filelock
from werkzeug.security import check_password_hash, generate_password_hash

STORIES_DIR = "stories"
DATA_DIR = "data"
SAVES_DIR = os.path.join(DATA_DIR, "saves")
ACCOUNTS_DB_PATH = os.path.join(DATA_DIR, "accounts.db")

DEFAULT_USER_ID = "local-cli"
# "example" (not "new_babel") so a fresh clone of the public repo - which doesn't include
# private-submodule stories like new_babel until `git submodule update --init` - still boots
# straight into a runnable story with zero extra setup.
DEFAULT_STORY_SLUG = "example"

_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _validate_slug(value: str, label: str) -> str:
    if not value or not _SLUG_RE.match(value):
        raise ValueError(f"invalid {label}: {value!r} (only letters, digits, '_', '-' allowed)")
    return value


# ---------------------------------------------------------------------------
# Story catalog (templates)
# ---------------------------------------------------------------------------

def list_stories() -> list:
    """Every story template available to start, read straight off disk - the
    templates/ directory listing *is* the catalog, no separate index to keep
    in sync."""
    stories = []
    if not os.path.isdir(STORIES_DIR):
        return stories
    for slug in sorted(os.listdir(STORIES_DIR)):
        template_path = os.path.join(STORIES_DIR, slug, "template.json")
        if os.path.isfile(template_path):
            with open(template_path, "r") as f:
                data = json.load(f)
            stories.append({"slug": slug, **data.get("meta", {})})
    return stories


def load_template(story_slug: str) -> dict:
    """A fresh copy of a story's seed content."""
    _validate_slug(story_slug, "story_slug")
    template_path = os.path.join(STORIES_DIR, story_slug, "template.json")
    with open(template_path, "r") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Per-user save state
# ---------------------------------------------------------------------------

def _save_path(user_id: str, story_slug: str) -> str:
    _validate_slug(user_id, "user_id")
    _validate_slug(story_slug, "story_slug")
    return os.path.join(SAVES_DIR, user_id, f"{story_slug}.json")


def _lock(user_id: str, story_slug: str) -> filelock.FileLock:
    path = _save_path(user_id, story_slug) + ".lock"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return filelock.FileLock(path)


def load_state(user_id: str = DEFAULT_USER_ID, story_slug: str = DEFAULT_STORY_SLUG) -> dict:
    """Loads a user's save for a story, cloning it fresh from the template on
    first play. Locked so a concurrent request for the same save can't race
    the clone-on-first-play step, even across gunicorn worker processes."""
    path = _save_path(user_id, story_slug)
    with _lock(user_id, story_slug):
        if os.path.isfile(path):
            with open(path, "r") as f:
                return json.load(f)
        state = load_template(story_slug)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(state, f, indent=2)
        return state


def save_state(state: dict, user_id: str = DEFAULT_USER_ID, story_slug: str = DEFAULT_STORY_SLUG):
    path = _save_path(user_id, story_slug)
    with _lock(user_id, story_slug):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(state, f, indent=2)


# ---------------------------------------------------------------------------
# Turn-in-progress status beacon (busy-indicator polling - app.py's /api/status route)
# ---------------------------------------------------------------------------
# Deliberately separate from the save file/lock above: this is a best-effort, ephemeral
# "what's the engine doing right now" hint for the UI, written multiple times per turn
# (once per LLM call - see story_engine.py's _timed()) and read by a poll from a different
# request entirely. Sharing the save file's lock would serialize the poll behind whatever
# long-running turn it's trying to report on, defeating the purpose. A stale or momentarily
# missing read is harmless (the UI just keeps its last label for one more poll interval),
# so this skips locking - os.replace's atomicity is enough to avoid a torn read of the file
# itself.

def _status_path(user_id: str, story_slug: str) -> str:
    return _save_path(user_id, story_slug) + ".status"


def write_turn_status(user_id: str, story_slug: str, label_key: str):
    """label_key is the raw _timed() label (e.g. "narration"), not the display word -
    display mapping (story_engine.STATUS_LABELS) and P50 lookup (p50_duration below) both
    key off this same raw label, so app.py's /api/status route does both off one value.
    started_at (wall-clock, not story_engine's monotonic timer - this crosses a process/
    request boundary via the poll, so it needs to be comparable to another process's
    time.time() call) lets that route estimate how far into the step the request is."""
    path = _status_path(user_id, story_slug)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + f".tmp{os.getpid()}"
    with open(tmp_path, "w") as f:
        json.dump({"label_key": label_key, "started_at": time.time()}, f)
    os.replace(tmp_path, path)


def clear_turn_status(user_id: str, story_slug: str):
    try:
        os.remove(_status_path(user_id, story_slug))
    except FileNotFoundError:
        pass


# clear_turn_status() lives in story_engine.py's take_turn/regenerate_last_turn `finally`
# blocks, so it only runs if that Python frame gets to unwind. It doesn't if the thread
# running it is killed out from under it - historically gunicorn's own --timeout (Dockerfile
# CMD, 220s) hard-killing a worker that ran long enough was the main way that happened (a
# request that chains multiple slow _timed() calls in one turn could exceed it even though
# any one call is individually bounded - see Dockerfile's comment on --timeout). app.py's
# take_turn/regenerate_turn now run that whole call chain on a background thread instead of
# inline in the request (see app.py's _start_turn_job), so gunicorn's own --timeout no
# longer applies to it at all - but the container/process can still die out from under a
# turn in other ways (a restart, an unhandled exception in the background thread, an OOM
# kill), so this backstop stays. Left unhandled, a beacon from a turn like that never gets
# cleared, which - since app.py's take_turn/regenerate_turn treat "a beacon exists" as "a
# turn is already running" - would otherwise soft-lock that save out of every future turn
# forever, not just the one that actually failed. 240s is deliberately not tied to any
# single call's own timeout constant - it's a backstop for "the process/thread is simply
# gone," not a latency budget.
TURN_STATUS_STALE_SECONDS = 240


def read_turn_status(user_id: str, story_slug: str) -> dict | None:
    try:
        with open(_status_path(user_id, story_slug), "r") as f:
            status = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    if time.time() - status.get("started_at", 0) > TURN_STATUS_STALE_SECONDS:
        clear_turn_status(user_id, story_slug)
        return None
    return status


# ---------------------------------------------------------------------------
# Turn-result handoff (app.py's /api/turn and /api/regenerate kick off
# take_turn/regenerate_last_turn on a background thread and return immediately - a slow
# turn holding one HTTP request open for minutes was getting killed by the Cloudflare
# tunnel's own edge timeout, well before gunicorn or story_engine's own fail-safes would
# ever give up. The client instead polls the turn-status beacon above and, once it goes
# idle again, fetches the outcome from here via GET /api/turn/result.)
# ---------------------------------------------------------------------------
# Bounded to exactly one entry, overwritten every turn - same shape as the save file's own
# history_log.pending_regenerate (see CLAUDE.md) - and consumed on read (one-shot) so a
# stray duplicate fetch can't re-append the same scene twice. Best-effort/unlocked, same
# tradeoff as the status beacon above: this is a handoff between one background thread and
# the one poll that's actually waiting on it, not durable state anything else reads.

def _result_path(user_id: str, story_slug: str) -> str:
    return _save_path(user_id, story_slug) + ".result"


def write_turn_result(user_id: str, story_slug: str, ok: bool, error: str | None = None):
    path = _result_path(user_id, story_slug)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + f".tmp{os.getpid()}"
    with open(tmp_path, "w") as f:
        json.dump({"ok": ok, "error": error}, f)
    os.replace(tmp_path, path)


def read_and_clear_turn_result(user_id: str, story_slug: str) -> dict | None:
    path = _result_path(user_id, story_slug)
    try:
        with open(path, "r") as f:
            result = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    return result


# ---------------------------------------------------------------------------
# Per-step latency stats (busy-indicator progress bar - app.py's /api/status route)
# ---------------------------------------------------------------------------
# A rolling sample window per _timed() label, used only to estimate how full a step's
# progress bar should be (elapsed / p50) while it's still running - NOT the durable
# performance record (that's scripts/perf_dashboard.py, which reads docker logs directly
# from the host and is deliberately never wired into this web process - see its
# docstring). This file is small, in-process-readable, and global across users/stories
# (latency is a property of the label/model doing the work, not of any one player's
# story), which is exactly what a live progress estimate needs and perf_dashboard.py's
# docker-logs approach can't give a running request.
#
# Read-modify-write with no locking, same tradeoff as the status beacon above: two
# concurrent turns (different users, different gunicorn worker processes) racing this
# file can lose one one's sample, but os.replace keeps the file itself always valid JSON,
# and losing an occasional sample only slightly skews an estimate - never worth a lock on
# every single LLM call.
PERF_STATS_PATH = os.path.join(DATA_DIR, "perf_stats.json")
PERF_STATS_WINDOW = 50


def record_call_duration(label: str, seconds: float):
    try:
        try:
            with open(PERF_STATS_PATH, "r") as f:
                stats = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            stats = {}
        samples = stats.setdefault(label, [])
        samples.append(seconds)
        del samples[:-PERF_STATS_WINDOW]
        os.makedirs(os.path.dirname(PERF_STATS_PATH), exist_ok=True)
        tmp_path = PERF_STATS_PATH + f".tmp{os.getpid()}"
        with open(tmp_path, "w") as f:
            json.dump(stats, f)
        os.replace(tmp_path, PERF_STATS_PATH)
    except OSError:
        pass  # best-effort - never let stats bookkeeping break a turn


def p50_duration(label: str) -> float | None:
    """Median (not mean) call duration for this label - the mean gets dragged around by
    the rare very-slow outlier (see scripts/perf_dashboard.py's own p50/mean columns),
    where the median tracks the typical-case call. Chosen over p90 deliberately: p90 is
    the safer "won't finish early" choice, but fills the bar too slowly for a typical call
    - median fills at the pace most calls actually complete at, at the cost of the bar
    more often hitting 100% and sitting there briefly on a slower-than-typical call (capped
    at 99% by app.py's /api/status route for exactly that reason). None until enough
    samples exist for this label - callers should fall back to a label with no progress
    bar (just the spinner) in that case, not a fabricated estimate."""
    try:
        with open(PERF_STATS_PATH, "r") as f:
            samples = json.load(f).get(label, [])
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    if not samples:
        return None
    ordered = sorted(samples)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


# ---------------------------------------------------------------------------
# Accounts (SQLite - the one place an atomic uniqueness check matters)
# ---------------------------------------------------------------------------

def _accounts_db() -> sqlite3.Connection:
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(ACCOUNTS_DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    return conn


def create_account(username: str, password: str) -> str:
    """Backend-only account provisioning - there's no self-service signup in
    the web UI. Raises ValueError if the username is already taken."""
    user_id = str(uuid.uuid4())
    conn = _accounts_db()
    try:
        conn.execute(
            "INSERT INTO users (id, username, password_hash) VALUES (?, ?, ?)",
            (user_id, username, generate_password_hash(password)),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        raise ValueError(f"username {username!r} is already taken")
    finally:
        conn.close()
    return user_id


def verify_login(username: str, password: str):
    """Returns the user_id on success, None on a bad username or password."""
    conn = _accounts_db()
    try:
        row = conn.execute(
            "SELECT id, password_hash FROM users WHERE username = ?", (username,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    user_id, password_hash = row
    if not check_password_hash(password_hash, password):
        return None
    return user_id


def parse_user_story_args(argv: list):
    """Pulls optional --user <id> / --story <slug> flags out of a CLI argv list
    (program name already stripped), returning (user_id, story_slug, remaining_argv).
    Shared by plot_manager.py and subplot_manager.py so `steer`-forwarded commands
    from story_engine.py's session target the right save file."""
    user_id = DEFAULT_USER_ID
    story_slug = DEFAULT_STORY_SLUG
    remaining = []
    i = 0
    while i < len(argv):
        if argv[i] == "--user" and i + 1 < len(argv):
            user_id = argv[i + 1]
            i += 2
        elif argv[i] == "--story" and i + 1 < len(argv):
            story_slug = argv[i + 1]
            i += 2
        else:
            remaining.append(argv[i])
            i += 1
    return user_id, story_slug, remaining


def _cli():
    import sys

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python state_store.py create-account <username> <password>")
        return

    command = sys.argv[1]
    if command == "create-account":
        if len(sys.argv) < 4:
            print("Usage: python state_store.py create-account <username> <password>")
            return
        username, password = sys.argv[2], sys.argv[3]
        try:
            user_id = create_account(username, password)
        except ValueError as e:
            print(f"Error: {e}")
            return
        print(f"Created account {username!r} (user_id={user_id})")
    else:
        print(f"Unknown command: {command}")


if __name__ == "__main__":
    _cli()

"""Storage layer for multi-user, multi-story play.

Schema v2 story/state split (see docs/SCHEMA_V2_SPEC.md): two documents, loaded together,
exposed as a plain two-key dict -

    ctx = state_store.load_state(user_id, story_slug)
    ctx["story"]   # authored content from stories/<slug>/template.json - FrozenDict, raises on write
    ctx["state"]   # runtime deltas from data/saves/<user>/<slug>.json - plain mutable dict

`stories/<slug>/template.json` is re-read fresh on every load, so a template edit reaches
every existing save automatically - `save_state()` only ever persists `ctx["state"]`.
`data/` is runtime-only (gitignored): accounts.db (SQLite, just for account credentials -
the one place an atomic uniqueness check actually matters) and saves/<user_id>/<story_slug>.json.
"""
import json
import os
import re
import sqlite3
import time
import uuid

import filelock
from werkzeug.security import check_password_hash, generate_password_hash

import migrate_v1
from frozen_dict import assert_unmutated, freeze, thaw

STORIES_DIR = "stories"
DATA_DIR = "data"
SAVES_DIR = os.path.join(DATA_DIR, "saves")
ACCOUNTS_DB_PATH = os.path.join(DATA_DIR, "accounts.db")

DEFAULT_USER_ID = "local-cli"
# "example" (not "new_babel") so a fresh clone of the public repo - which doesn't include
# private-submodule stories like new_babel until `git submodule update --init` - still boots
# straight into a runnable story with zero extra setup.
DEFAULT_STORY_SLUG = "example"

CURRENT_SCHEMA_VERSION = 2

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


def load_template_raw(story_slug: str) -> dict:
    """A fresh, plain-dict (unfrozen) copy of a story's authored content, straight off
    disk. Most callers want load_template() instead (frozen, ready to use as ctx["story"]);
    this exists for the handful of callers that need a mutable copy - the migrator building
    a new save from scratch, an admin/debug script, etc."""
    _validate_slug(story_slug, "story_slug")
    template_path = os.path.join(STORIES_DIR, story_slug, "template.json")
    with open(template_path, "r") as f:
        return json.load(f)


def load_template(story_slug: str) -> dict:
    """A story's authored content, frozen (see frozen_dict.freeze) - this is what
    ctx["story"] is set to. Re-read fresh from disk on every call, deliberately never
    cached: this is what makes a template edit reach every existing save without any
    explicit migration step."""
    return freeze(load_template_raw(story_slug))


# ---------------------------------------------------------------------------
# Fresh-save construction (first play) and reconciliation (every later load)
# ---------------------------------------------------------------------------

def new_save_state(story: dict, story_slug: str) -> dict:
    """Builds a fresh runtime state dict for a brand-new save, seeded from story's authored
    pools (subplots, opening scene, initial scene) - see SCHEMA_V2_SPEC.md §4. Everything
    here is runtime-owned from the moment it's created; nothing under the returned dict is
    ever read back out of `story` again except through the merge-view helpers in
    story_engine.py (e.g. resolving a seeded subplot's title from the template)."""
    subplots = {}
    for sid, seed in story["plot"]["subplots"].items():
        subplots[sid] = {
            "progress": 0,
            "status": "active" if seed.get("starts_active") else "not_started",
            "active": bool(seed.get("starts_active")),
        }

    acts = story["plot"]["main_thread"]["acts"]
    initial_scene = story["plot"].get("initial_scene", {})

    return {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "story_slug": story_slug,
        "story_version": story.get("story_version"),

        "protagonist": {
            "name": "",
            "traits": list(story["protagonist"].get("traits", [])),
            "inventory": list(story["protagonist"].get("starting_inventory", [])),
            "stats": {},
            "creation_choices": {},
            "flags": {"active": {}, "meta": {}, "archive": {}},
        },

        "characters": {},

        "scene": {
            "location": initial_scene.get("location", ""),
            "summary": initial_scene.get("summary", ""),
            "present": [],
        },

        "plot": {
            "opening_played": False,
            "current_act": acts[0]["act_number"] if acts else 1,
            "generated_acts": [],
            # One {"completed", "optional"} entry per template-authored act, keyed by
            # act_number as a string (JSON object keys are always strings) - acts[].completed
            # and .optional move to runtime (SCHEMA_V2_SPEC.md §3.8) since the template acts
            # themselves are frozen. An act generated later carries both fields directly on
            # its own (mutable) generated_acts entry instead of needing this overlay.
            "act_completion": {
                str(act["act_number"]): {"completed": False, "optional": False} for act in acts
            },
            "act_history": [],
            "emergent_directions": [],
            "subplots": subplots,
            "completed_subplots": [],
            "revelations_revealed": {},
            "entity_contact_count": 0,
            "endgame": {
                "requested": False, "requested_turn": None,
                "final_arc": None, "concluded": False, "cause": None,
            },
            "thread_steering": {
                "last_pivot_turn": 0, "pivot_history": [], "emerging_themes": [],
                "player_driven_goals": [], "pending_seeds": [],
            },
        },

        "pacing": {
            "turn_count": 0,
            "turns_since_nudge": 0,
            "turns_since_act_check": 0,
            "subplots_completed_this_act": 0,
            "last_direction": "",
        },

        "history": {
            "recent_turns": [],
            "compressed_summary": "",
            "full_transcript": [],
        },

        "pending_regenerate": None,
    }


def _reconcile(state: dict, story: dict) -> dict:
    """Runs on every load of an existing save, per SCHEMA_V2_SPEC.md §2.3 - a template
    edit can invalidate a runtime reference (a removed location, a removed revelation),
    and the rule is always to degrade gracefully, never to fail the load:

    - scene.location not in story.world.locations -> left as-is, rendered raw
      (story_engine._location_name already falls back to the raw id for an unknown one).
    - a runtime subplot id absent from the template -> no action; it's a generated
      subplot, expected to have no template counterpart.
    - a seeded subplot removed from the template -> no action here; the runtime copy
      stays in place (it's in-flight) and story_engine's merge-view falls back to a
      placeholder title/description if the template entry is genuinely gone.
    - a revelation id absent from the template -> drop the runtime reveal record
      silently, since there's nothing left to have revealed.
    - a stat name absent from character_creation -> no action; the value is kept.
    """
    valid_revelation_ids = {r["id"] for r in story.get("mechanics", {}).get("revelations", [])}
    revealed = state["plot"].get("revelations_revealed", {})
    for rev_id in list(revealed):
        if rev_id not in valid_revelation_ids:
            del revealed[rev_id]
    return state


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
    """Loads a user's save for a story, cloning a fresh runtime state from the story's
    authored pools on first play. Returns {"story": ctx, "state": ctx} - see module
    docstring. Locked so a concurrent request for the same save can't race the
    clone-on-first-play step, even across gunicorn worker processes."""
    story = load_template(story_slug)
    path = _save_path(user_id, story_slug)
    with _lock(user_id, story_slug):
        if os.path.isfile(path):
            with open(path, "r") as f:
                raw_state = json.load(f)
            if raw_state.get("schema_version", 1) < CURRENT_SCHEMA_VERSION:
                raw_state = migrate_v1.migrate(raw_state, story_slug, load_template_raw(story_slug))
                with open(path, "w") as f:
                    json.dump(raw_state, f, indent=2)
            state = _reconcile(raw_state, story)
        else:
            state = new_save_state(story, story_slug)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                json.dump(state, f, indent=2)
        return {"story": story, "state": state}


def save_state(ctx: dict, user_id: str = DEFAULT_USER_ID, story_slug: str = DEFAULT_STORY_SLUG):
    """Persists ctx["state"] only - ctx["story"] is never written back, since it's
    authored content re-read fresh from the template on every load. Asserts nothing under
    ctx["story"] was mutated during the request, independent of FrozenDict already raising
    at the point of mutation (see frozen_dict.assert_unmutated's docstring) - compares
    against a fresh read of the template file itself (the actual source of truth) rather
    than a snapshot stashed at load time, so ctx stays exactly the two-key shape
    SCHEMA_V2_SPEC.md §2.2 calls for."""
    assert_unmutated(load_template_raw(story_slug), ctx["story"], context=f"{user_id}/{story_slug}")
    path = _save_path(user_id, story_slug)
    with _lock(user_id, story_slug):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(ctx["state"], f, indent=2)


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
# pending_regenerate - and consumed on read (one-shot) so a stray duplicate fetch can't
# re-append the same scene twice. Best-effort/unlocked, same tradeoff as the status beacon
# above: this is a handoff between one background thread and the one poll that's actually
# waiting on it, not durable state anything else reads.

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

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

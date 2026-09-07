"""Shared helpers for the Portal PreToolUse hooks.

The hooks are the only enforced layer. Scripts and skills are advisory:
if the model ignores a skill, the hook still refuses the expensive read.
"""
import json
import os
import sys

DEFAULT_THRESHOLD = 350

# Never delegate these, even when large.
EXEMPT_SUFFIXES = ("CLAUDE.md", "SKILL.md")
EXEMPT_PARTS = (os.sep + ".claude" + os.sep,)


def threshold() -> int:
    try:
        return int(os.environ.get("PORTAL_LINE_THRESHOLD", DEFAULT_THRESHOLD))
    except ValueError:
        return DEFAULT_THRESHOLD


def is_worker() -> bool:
    """True inside a delegated worker subprocess.

    Without this guard a `claude -p` worker inherits these same hooks and
    blocks its own reads, which recurses until the timeout fires.
    """
    return os.environ.get("PORTAL_WORKER") == "1"


def read_event() -> dict:
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


def is_exempt(path: str) -> bool:
    p = os.path.abspath(path)
    if any(p.endswith(s) for s in EXEMPT_SUFFIXES):
        return True
    return any(part in p for part in EXEMPT_PARTS)


def count_lines(path: str):
    """Line count, or None for binary / unreadable files."""
    try:
        with open(path, "rb") as f:
            head = f.read(8192)
            if b"\x00" in head:
                return None
            n = head.count(b"\n")
            while True:
                chunk = f.read(1 << 20)
                if not chunk:
                    break
                n += chunk.count(b"\n")
        return n + 1
    except OSError:
        return None


def script_path(name: str) -> str:
    """Path to a portal script, as the caller should type it.

    Relative when the install sits inside the project (`.claude/scripts/...`),
    absolute when it is a user-wide install under ~/.claude. Derived from this
    file's own location so the block message is correct either way.
    """
    hooks_dir = os.path.dirname(os.path.abspath(__file__))
    full = os.path.join(os.path.dirname(hooks_dir), "scripts", name)
    try:
        rel = os.path.relpath(full, os.getcwd())
        if not rel.startswith(".."):
            return rel
    except ValueError:
        pass
    return full


def allow():
    """Say nothing, let the call through."""
    sys.exit(0)


def deny(reason: str):
    """Block the call and hand the model a reason that names the alternative.

    Two wire formats. `json` is the documented PreToolUse decision object.
    `exit2` writes to stderr and exits 2, which is fed back verbatim.
    Set PORTAL_HOOK_STYLE=exit2 if the block message is not reaching the model.
    """
    if os.environ.get("PORTAL_HOOK_STYLE") == "exit2":
        print(reason, file=sys.stderr)
        sys.exit(2)
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)

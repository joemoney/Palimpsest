"""Mode resolution and the single worker call. Shared by every script.

A mode is configuration, not infrastructure: a markdown file with a
frontmatter block (model, temperature, max_tokens, timeout) and a body that
becomes the worker's system prompt. Nothing is kept alive between calls.

Resolution order, first match wins:
  1. ~/.claude/portal/modes/<name>.md        your own version
  2. $CLAUDE_PROJECT_DIR/.claude/portal/modes/<name>.md   your team's
  3. $PORTAL_COMPANY_MODES/<name>.md         the company-wide one
  4. the copy bundled next to this file
So forking a shared worker and changing its model takes effect without
touching the script that calls it.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

DEFAULTS = {
    "model": "claude-haiku-4-5",
    "temperature": 0.2,
    "max_tokens": 2000,
    "timeout": 30,          # the hard cap on one delegation
}

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"


# --------------------------------------------------------------------- modes

def _search_path():
    project = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
    candidates = [
        os.path.expanduser("~/.claude/portal/modes"),
        os.path.join(project, ".claude", "portal", "modes"),
        os.environ.get("PORTAL_COMPANY_MODES", ""),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "modes"),
    ]
    seen, out = set(), []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def load_mode(name: str) -> dict:
    for directory in _search_path():
        path = os.path.join(directory, name + ".md")
        if os.path.isfile(path):
            return _parse_mode(path, name)
    sys.exit(f"portal: mode '{name}' not found in {_search_path()}")


def _parse_mode(path: str, name: str) -> dict:
    text = open(path, encoding="utf-8").read()
    mode = dict(DEFAULTS, name=name, source=path)
    body = text
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if m:
        body = m.group(2)
        for line in m.group(1).splitlines():
            if ":" not in line or line.strip().startswith("#"):
                continue
            key, _, value = line.partition(":")
            key, value = key.strip(), value.strip().strip("'\"")
            if key in ("temperature",):
                mode[key] = float(value)
            elif key in ("max_tokens", "timeout"):
                mode[key] = int(value)
            elif key:
                mode[key] = value
    mode["instructions"] = body.strip()
    return mode


# -------------------------------------------------------------------- worker

def run(mode: dict, prompt: str) -> str:
    """One shot. No history, no session, nothing kept."""
    backend = os.environ.get("PORTAL_BACKEND", "cli")
    if backend == "api":
        return _run_api(mode, prompt)
    return _run_cli(mode, prompt)


def _worker_env():
    env = dict(os.environ)
    env["PORTAL_WORKER"] = "1"   # stops the hooks recursing into the worker
    return env


def _run_cli(mode: dict, prompt: str) -> str:
    """Reuse the local Claude Code auth. No API key to hand around.

    Runs in a scratch directory with every tool denied, so the worker is a
    single completion rather than a second agent loop. temperature is not
    settable on this path; use PORTAL_BACKEND=api if you need it honoured.
    """
    cmd = [
        "claude", "-p", prompt,
        "--model", str(mode["model"]),
        "--output-format", "text",
        "--append-system-prompt", mode["instructions"],
        "--disallowedTools",
        "Bash,Edit,Write,NotebookEdit,Read,Glob,Grep,Task,WebFetch,WebSearch",
    ]
    with tempfile.TemporaryDirectory() as scratch:
        try:
            proc = subprocess.run(
                cmd, cwd=scratch, env=_worker_env(),
                capture_output=True, text=True, timeout=mode["timeout"],
            )
        except FileNotFoundError:
            sys.exit("portal: `claude` not on PATH. Install it, or set "
                     "PORTAL_BACKEND=api with ANTHROPIC_API_KEY.")
        except subprocess.TimeoutExpired:
            sys.exit(f"portal: worker exceeded the {mode['timeout']}s cap. "
                     "Split the job into smaller pieces.")
    if proc.returncode != 0:
        sys.exit(f"portal: worker failed ({proc.returncode}): "
                 f"{proc.stderr.strip()[:400]}")
    return proc.stdout.strip()


def _run_api(mode: dict, prompt: str) -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        sys.exit("portal: PORTAL_BACKEND=api needs ANTHROPIC_API_KEY.")
    payload = json.dumps({
        "model": mode["model"],
        "max_tokens": mode["max_tokens"],
        "temperature": mode["temperature"],
        "system": mode["instructions"],
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(API_URL, data=payload, headers={
        "content-type": "application/json",
        "x-api-key": key,
        "anthropic-version": API_VERSION,
    })
    try:
        with urllib.request.urlopen(req, timeout=mode["timeout"]) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"portal: API error {e.code}: {e.read()[:400].decode(errors='replace')}")
    except Exception as e:
        sys.exit(f"portal: API call failed: {e}")

    text = "".join(b.get("text", "") for b in data.get("content", [])
                   if b.get("type") == "text").strip()
    usage = data.get("usage", {})
    if usage:
        print(f"portal: {mode['name']} used {usage.get('input_tokens', '?')} in / "
              f"{usage.get('output_tokens', '?')} out at the worker rate.",
              file=sys.stderr)
    return text


def strip_fences(text: str) -> str:
    """Belt and braces: the mode forbids fences, this removes them anyway."""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z0-9_+-]*\n?", "", t)
        t = re.sub(r"\n?```\s*$", "", t)
    return t.strip("\n")

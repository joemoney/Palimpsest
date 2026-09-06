"""Read/write access to the beat-labelling worksheets under data/, for the web labelling UI.

The worksheet markdown file stays the single source of truth - the web page is a view over
it, not a second store. `scripts/gate_02.py` parses these same files with its own tolerant
regex, and `scripts/make_label_sheet.py` writes them, so anything here must round-trip that
exact format: a header, then `## Turn N` blocks each ending in a fenced BEAT/INTENSITY/NOTE
triple. Writes replace only the three fields inside a block's fence and leave every other
byte - prose excerpts, header wording, separators - untouched.

The beat vocabulary and intensity scale are read back out of the worksheet's own header
rather than re-read from the vocabulary JSON. The worksheet is what the human is labelling
against, so the radio buttons must offer exactly the options it names, even if the vocab
file on disk has since been edited.
"""

import os
import re
import tempfile

import filelock

# Worksheets contain story prose (see make_label_sheet.py's docstring on why they stay in
# gitignored data/), and the sheet name arrives from a URL, so it is restricted to a plain
# slug rather than joined as a path.
SHEET_NAME_RE = re.compile(r"^[a-z0-9_]+$")
LABELS_DIR = "data"

# The whole labelling feature is OFF unless LABEL_SHEETS_USER names a user id. It is an
# operator measurement tool, not a product feature: without this gate every logged-in user can
# read and patch the worksheets (they are a single global artifact, not per-user), and any
# player far enough into a live-sheet story gets labelling controls under their choices and
# silently appends their own turns to someone else's measurement run.
def enabled_for(user_id: str) -> bool:
    allowed = os.environ.get("LABEL_SHEETS_USER", "").strip()
    return bool(allowed) and user_id == allowed


# Which worksheet the play page labels into, per story. A story absent from this map simply
# gets no labelling row under its choices - the mechanic is a measurement tool for a gate
# run, not a permanent part of playing, so it is opt-in per story and per round of labelling.
# The value is a sheet name, so a second round against a rewritten vocabulary points here at
# a NEW sheet rather than appending to the old one under different definitions.
LIVE_SHEETS = {"example": "example_v3"}

# Recorded in the sheet so sync_from_save knows which turns belong to it. A round of
# labelling that starts partway through a playthrough (turn 31 of 60, say) must not
# retroactively pull in turns already labelled under an older vocabulary.
_START_TURN_RE = re.compile(r"^<!-- sheet_start_turn: (\d+) -->$", flags=re.M)

HEAD_WORDS = 60
TAIL_WORDS = 150

_TURN_SPLIT_RE = re.compile(r"^## Turn (\d+)$", flags=re.M)
_BEAT_DEF_RE = re.compile(r"^- \*\*`([^`]+)`\*\* [—-] (.+)$", flags=re.M)
_INTENSITY_DEF_RE = re.compile(r"^- \*\*(\d)\*\* [—-] (.+)$", flags=re.M)
_FIELD_RE = {
    "beat": re.compile(r"^BEAT:.*$", flags=re.M),
    "intensity": re.compile(r"^INTENSITY:.*$", flags=re.M),
    "note": re.compile(r"^NOTE:.*$", flags=re.M),
}
_FENCE_RE = re.compile(r"```\nBEAT:.*?\n```", flags=re.S)
_TIE_BREAK_RE = re.compile(r"^\*\*Tie-break\.\*\* (.+)$", flags=re.M)


def sheet_path(sheet: str) -> str:
    if not SHEET_NAME_RE.match(sheet or ""):
        raise ValueError(f"invalid sheet name: {sheet!r}")
    return os.path.join(LABELS_DIR, f"labels_{sheet}.md")


def list_sheets() -> list:
    if not os.path.isdir(LABELS_DIR):
        return []
    names = []
    for entry in sorted(os.listdir(LABELS_DIR)):
        m = re.match(r"^labels_([a-z0-9_]+)\.md$", entry)
        if m:
            names.append(m.group(1))
    return names


def _field(block: str, name: str) -> str:
    m = _FIELD_RE[name].search(block)
    return m.group(0).split(":", 1)[1].strip() if m else ""


def load(sheet: str) -> dict:
    """Parses a worksheet into {beats, intensity, scenes}. `scenes` carries each turn's
    excerpts plus whatever labels are already filled in, so the page renders as a resume of
    work in progress rather than always blank."""
    with open(sheet_path(sheet)) as f:
        text = f.read()

    parts = _TURN_SPLIT_RE.split(text)
    header = parts[0]

    scenes = []
    for turn_raw, block in zip(parts[1::2], parts[2::2]):
        def grab(label, pattern):
            m = re.search(pattern, block, flags=re.M)
            return m.group(1).strip() if m else ""
        scenes.append({
            "turn": int(turn_raw),
            "action": grab("action", r"^\*\*Action:\*\* (.*)$"),
            "opens": grab("opens", r"^\*\*Opens:\*\* (.*)$"),
            "closes": grab("closes", r"^\*\*Closes:\*\* (.*)$"),
            "beat": _field(block, "beat").lower(),
            "intensity": _field(block, "intensity"),
            "note": _field(block, "note"),
        })

    tie_break = _TIE_BREAK_RE.search(header)
    return {
        "sheet": sheet,
        "tie_break": tie_break.group(1).strip() if tie_break else "",
        "beats": [{"name": n, "definition": d} for n, d in _BEAT_DEF_RE.findall(header)],
        "intensity": [{"value": v, "description": d} for v, d in _INTENSITY_DEF_RE.findall(header)],
        "scenes": scenes,
    }


def save_scene(sheet: str, turn: int, beat: str = None, intensity: str = None, note: str = None) -> dict:
    """Writes one scene's fields back into the worksheet, then returns the fresh counts the
    page's progress line shows. Only the named fields are touched - the page posts a single
    changed radio at a time, and a blanket rewrite would clobber the other two.

    Read-modify-write of the whole file, so the read and the write must sit inside ONE lock,
    not merely each be atomic on its own: labelling fires a burst of independent POSTs that
    gunicorn spreads across worker *processes*, and two overlapping saves without this both
    read the same text and the later write silently discards the earlier one's field. That is
    not hypothetical - it ate a real label during the first sheet. Same per-file `filelock`
    approach state_store.py uses on saves, and for the same cross-process reason.
    """
    path = sheet_path(sheet)
    with filelock.FileLock(path + ".lock"):
        return _save_scene_locked(path, sheet, turn, beat, intensity, note)


def _save_scene_locked(path, sheet, turn, beat, intensity, note):
    with open(path) as f:
        text = f.read()

    updates = {"beat": beat, "intensity": intensity, "note": note}
    found = False

    def rewrite_block(block: str) -> str:
        def rewrite_fence(m):
            fence = m.group(0)
            for name, value in updates.items():
                if value is None:
                    continue
                fence = _FIELD_RE[name].sub(f"{name.upper()}:{' ' * (11 - len(name))}{value}", fence, count=1)
            return fence
        return _FENCE_RE.sub(rewrite_fence, block, count=1)

    parts = _TURN_SPLIT_RE.split(text)
    out = [parts[0]]
    for turn_raw, block in zip(parts[1::2], parts[2::2]):
        if int(turn_raw) == turn:
            block = rewrite_block(block)
            found = True
        out.append(f"## Turn {turn_raw}")
        out.append(block)

    if not found:
        raise ValueError(f"no such turn in {sheet}: {turn}")

    _atomic_write(path, "".join(out))
    return progress(load(sheet))


def _atomic_write(path: str, text: str) -> None:
    """Sibling temp file + rename, so an interrupted write leaves the previous worksheet
    intact rather than a half-written one."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)),
                               prefix=".labels-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def progress(data: dict) -> dict:
    scenes = data["scenes"]
    done = sum(1 for s in scenes if s["beat"] and s["intensity"])
    return {"done": done, "total": len(scenes), "remaining": len(scenes) - done}


# --- Worksheet authoring (shared with scripts/make_label_sheet.py) ------------------------
#
# Format lives here rather than in the script because the web UI now writes worksheets too,
# and a scene block written by one path has to be readable by the other and by
# scripts/gate_02.py. One definition of the format, three callers.


def strip_options(narration: str) -> str:
    """Stored turns keep the model's whole reply, OPTIONS block included. Those are the
    choices offered, not part of the scene, and left in they swamp the tail excerpt - which
    is the half the boundary rule actually depends on."""
    return narration.split("\nOPTIONS:")[0].rstrip()


def excerpt(narration: str) -> tuple:
    """Opening and closing of a scene. The closing matters most: the boundary rule asks for
    the scene's terminal state, and scenes routinely turn over in their final paragraph."""
    words = strip_options(narration).split()
    if len(words) <= HEAD_WORDS + TAIL_WORDS:
        return " ".join(words), ""
    return " ".join(words[:HEAD_WORDS]), " ".join(words[-TAIL_WORDS:])


def render_header(sheet: str, beats: list, intensity: list, count=None,
                  start_turn: int = 1, tie_break: str = "") -> str:
    """`beats`/`intensity` are (name, text) pairs, taken from the vocabulary being tested.
    A live sheet passes count=None: scenes arrive as they are played, so the sheet cannot
    state a total up front."""
    beat_lines = "\n".join(f"- **`{name}`** — {defn}" for name, defn in beats)
    level_lines = "\n".join(f"- **{n}** — {desc}" for n, desc in intensity)
    scope = f"{count} scenes." if count is not None else (
        f"Scenes are appended as you play, starting from turn {start_turn}.")
    tie = f"\n\n**Tie-break.** {tie_break}" if tie_break else ""
    return f"""# Beat labelling worksheet — `{sheet}`

<!-- sheet_start_turn: {start_turn} -->

{scope} Fill in `BEAT:` and `INTENSITY:` under each.

**Don't overthink individual calls.** Disagreements are data - they show which beat pairs are
ambiguous. First instinct is usually the right label.

## The beats

{beat_lines}

Every scene gets exactly one.{tie}

## Intensity, 1–3

{level_lines}

Score every scene, including the quiet ones — counters accumulate intensity rather than scene
count, so a quiet scene still carries a weight.

## The boundary rule

Some scenes open in one mode and turn in their final paragraph. **Classify by the scene's
terminal state**: what is true when the scene stops, since that is what carries into the next
turn and what a corrective directive would have to act on.

---

"""


def render_scene(turn: int, action: str, narration: str) -> str:
    head, tail = excerpt(narration)
    block = f"## Turn {turn}\n\n**Action:** {action}\n\n**Opens:** {head}…\n\n"
    if tail:
        block += f"**Closes:** …{tail}\n\n"
    return block + "```\nBEAT:       \nINTENSITY:  \nNOTE:       \n```\n\n---\n\n"


def create(sheet: str, beats: list, intensity: list, start_turn: int = 1,
           tie_break: str = "", count=None) -> str:
    """Writes a new, empty worksheet. Refuses to clobber an existing one - a worksheet is
    hand-entered data that cannot be regenerated."""
    path = sheet_path(sheet)
    if os.path.exists(path):
        raise FileExistsError(path)
    with open(path, "w") as f:
        f.write(render_header(sheet, beats, intensity, count=count,
                              start_turn=start_turn, tie_break=tie_break))
    return path


def start_turn(sheet: str) -> int:
    with open(sheet_path(sheet)) as f:
        m = _START_TURN_RE.search(f.read())
    return int(m.group(1)) if m else 1


def sync_from_save(sheet: str, user_id: str, story_slug: str) -> int:
    """Appends any played turn that belongs to this sheet and isn't in it yet, and returns
    how many were added. Idempotent and lock-guarded, so it is safe to call on every page
    render and after every turn - which is the point: the labelling row under the choices
    can only offer the scene you just read if the row exists by the time the page swaps.
    """
    import story_engine  # deferred: keeps the CLI worksheet script off the LLM import path

    path = sheet_path(sheet)
    with filelock.FileLock(path + ".lock"):
        with open(path) as f:
            text = f.read()
        have = {int(t) for t in _TURN_SPLIT_RE.findall(text)}
        first = int(_START_TURN_RE.search(text).group(1)) if _START_TURN_RE.search(text) else 1

        ctx = story_engine.state_store.load_state(user_id, story_slug)
        played = story_engine.all_turns(ctx)[1:]  # index 0 is the opening scene

        added = 0
        for i, entry in enumerate(played, start=1):
            if i < first or i in have:
                continue
            action, narration = story_engine.split_turn_entry(entry)
            text += render_scene(i, action, narration)
            added += 1

        if added:
            _atomic_write(path, text)
    return added

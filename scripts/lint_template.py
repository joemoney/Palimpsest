"""Validate a story template from the command line - the storyboard's Validate, without the board.

The CLI twin AUTHORING_TOOL_PHASES.md (S4) plans for `author_lint`: the same checks the board's
Validate runs (schema L01, every condition's referents L10, structure, cast, fragments, world,
endings, stat tiers, bonds, side threads, derived values), plus three things an author editing the
JSON by hand needs and the board never has to say:

  - whether the story loads for play (`mechanics.validate`), and which authored modules this build
    has no engine for yet;
  - whether the board would open it without changing anything (a shape the board doesn't round-trip
    is still valid JSON, but the next board Save will rewrite it);
  - whether the file is in canonical formatting (2-space indent, real unicode, trailing newline).

Usage:
    python3 scripts/lint_template.py <slug | path/to/template.json> [--json] [--write]

  --write   (slug only) rewrite the file through state_store.write_template - canonical formatting
            and a story_version bump, exactly as a board Save - and resync the README synopsis.
            Writes even with lint errors, as the board does: errors keep a story out of the
            player's list, they never stop the author saving.
  --json    print the report as JSON.

Exit status: 0 no errors, 1 lint errors, 2 the file could not be read or parsed.
"""
import contextlib
import io
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "backend"))
os.chdir(REPO)  # STORIES_DIR / DATA_DIR are relative to the repo root (CLAUDE.md)

import author_lint  # noqa: E402
import author_model  # noqa: E402
import mechanics  # noqa: E402
import state_store  # noqa: E402


def _read(target):
    """`(slug or None, path, text, raw)`."""
    if target.endswith(".json") or os.sep in target:
        path = os.path.abspath(target)
        slug = None
    else:
        slug = target
        path = os.path.join(state_store._story_dir(slug), "template.json")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    return slug, path, text, json.loads(text)


def report(raw, text):
    issues = author_lint.lint(raw, author_model.to_board_model(raw))
    load_warnings = io.StringIO()
    try:
        with contextlib.redirect_stdout(load_warnings):
            mechanics.validate(raw)
        loads, refusal = True, None
    except (mechanics.UnknownEngineError, ValueError, KeyError) as e:
        loads, refusal = False, str(e)
    _, left_out = author_model.playable_projection(raw, set(mechanics.registered_engines()))
    skip = ("schema_version", "_storyboard")
    board = author_model.from_board_model(raw, author_model.to_board_model(raw))
    changed = sorted(k for k in set(board) | set(raw) if k not in skip and board.get(k) != raw.get(k))
    return {
        "errors": [i for i in issues if i["severity"] == "error"],
        "warnings": [i for i in issues if i["severity"] == "warning"],
        "loads_for_play": loads,
        "load_refusal": refusal,
        "load_warnings": [l for l in load_warnings.getvalue().splitlines() if l.strip()],
        "not_built": [f"mechanics.{slot} ({engine})" for slot, engine in left_out],
        "board_would_rewrite": changed,
        "canonical_format": text == state_store._dumps_template(raw),
    }


def _print(r):
    for label, key in (("ERROR", "errors"), ("warning", "warnings")):
        for i in r[key]:
            print(f"{label:7} {i['id']:13} {i['message']}")
    print()
    print(f"{len(r['errors'])} error(s), {len(r['warnings'])} warning(s)."
          + ("" if r["errors"] else " Players can see this story once it loads for play."))
    print("Loads for play: " + ("yes" if r["loads_for_play"] else f"no - {r['load_refusal']}"))
    for line in r["load_warnings"]:
        print("  " + line)
    if r["not_built"]:
        print("Authored but not built in this engine yet: " + ", ".join(r["not_built"]))
    if r["board_would_rewrite"]:
        print("The board would rewrite these top-level sections on its next Save: "
              + ", ".join(r["board_would_rewrite"]))
    if not r["canonical_format"]:
        print("Not in canonical formatting - run with --write to fix.")


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    flags = {a for a in argv if a.startswith("--")}
    if len(args) != 1 or flags - {"--json", "--write"}:
        print(__doc__)
        return 2
    path = args[0]
    try:
        slug, path, text, raw = _read(args[0])
    except FileNotFoundError as e:
        print(f"Cannot read: {e}")
        return 2
    except json.JSONDecodeError as e:
        print(f"Not valid JSON: {path}: line {e.lineno}, column {e.colno}: {e.msg}")
        return 2
    if "--write" in flags:
        if slug is None:
            print("--write needs a story slug, not a path: only state_store.write_template writes a template.")
            return 2
        version = state_store.write_template(slug, raw)
        readme = os.path.join(os.path.dirname(path), "README.md")
        if os.path.isfile(readme):
            import readme_sync
            with open(readme, encoding="utf-8") as f:
                old = f.read()
            new = readme_sync.sync(old, (raw.get("meta") or {}).get("synopsis", ""))
            if new != old:
                with open(readme, "w", encoding="utf-8") as f:
                    f.write(new)
        slug, path, text, raw = _read(slug)
        if "--json" not in flags:
            print(f"Wrote {path} as story_version {version}.\n")
    r = report(raw, text)
    if "--json" in flags:
        print(json.dumps(r, indent=2, ensure_ascii=False))
    else:
        _print(r)
    return 1 if r["errors"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

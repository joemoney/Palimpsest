"""scripts/lint_template.py (the command-line Validate) and docs/Agent_Authoring_Manual.md's worked
example. The example is what an agent copies, so it must keep linting with no errors, loading for
play, and round-tripping through the board unchanged as the schema and lint evolve.

Needs jsonschema>=4.18 for the schema check (L01); skips (exit 0) without it, like
test_author_routes.py.

Run directly: python3 test/test_lint_template_cli.py
"""
import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "backend"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _llm_stubs  # noqa: E402,F401
import jsonschema  # noqa: E402
if not hasattr(jsonschema, "Draft202012Validator"):
    print("SKIPPED: jsonschema has no Draft202012Validator - needs jsonschema>=4.18.")
    sys.exit(0)
import state_store  # noqa: E402

spec = importlib.util.spec_from_file_location("lint_template", os.path.join(REPO, "scripts", "lint_template.py"))
cli = importlib.util.module_from_spec(spec)
cwd = os.getcwd()
spec.loader.exec_module(cli)
os.chdir(cwd)

manual = open(os.path.join(REPO, "docs", "Agent_Authoring_Manual.md"), encoding="utf-8").read()
example = manual.split("## 11. Worked example")[1].split("```json\n")[1].split("\n```")[0]
raw = json.loads(example)

tmp = tempfile.mkdtemp()
path = os.path.join(tmp, "template.json")
with open(path, "w", encoding="utf-8") as f:
    f.write(example + "\n")


def run(*args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(list(args))
    return code, out.getvalue()


code, text = run(path, "--json")
r = json.loads(text)
assert code == 0 and r["errors"] == [], r["errors"]
assert r["loads_for_play"] and not r["not_built"], r
assert r["board_would_rewrite"] == [], "the example must be written in the board's own shapes"
assert r["canonical_format"], "the example must be in canonical formatting"
assert [w["id"] for w in r["warnings"]] == ["structural"], "the manual names exactly one intentional warning"
print("OK: the manual's worked example lints clean, loads for play, round-trips and is canonical")

bad = json.loads(example)
bad["mechanics"]["endings"]["entries"] = [e for e in bad["mechanics"]["endings"]["entries"] if e.get("viable_while")]
bad["derived"] = [{"set": {"x": "y"}}]
with open(path, "w", encoding="utf-8") as f:
    json.dump(bad, f, indent=4)
code, text = run(path)
assert code == 1, code
assert "L08" in text and "Loads for play: no" in text and "canonical formatting" in text, text
with open(path, "w", encoding="utf-8") as f:
    f.write('{"a": 1,\n "b": }')
code, text = run(path)
assert code == 2 and "line 2, column" in text, text
with open(path, "w", encoding="utf-8") as f:
    f.write(example + "\n")
code, text = run(path, "--write")
assert code == 2 and "needs a story slug" in text, text
print("OK: the CLI reports errors (exit 1), refusals and formatting, and unreadable JSON (exit 2)")

for slug in ("example",):
    code, text = run(slug, "--json")
    assert code in (0, 1) and "errors" in json.loads(text)
print("OK: the CLI reads a story by slug")

print("\nALL CHECKS PASSED: test_lint_template_cli")

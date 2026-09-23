"""README synopsis sync (Authoring_Tool_Spec.md sect2/sect12.5; AUTHORING_TOOL_PHASES.md Phase
S1, step 6): keeps a story's README.md "## Synopsis" section identical to its template's
`meta.synopsis`, so the two can never drift the way the hand-maintained note in every README
this repo had before this module used to warn about ("keep the two in sync if you edit
either").

Pure string transforms, no I/O - the caller (app.py's save routes) reads and writes the file.
Only the marked section is ever touched; every other word in the README survives untouched.

Creating a README from nothing for a story that has none yet is broader than
AUTHORING_TOOL_PHASES.md originally scoped S1's sync to ("only rewrite a marked section of an
existing README.md... never create one") - `minimal_readme` exists for exactly that, used here
on explicit request for a one-time backfill, not wired into the automatic per-save sync (which
still only touches a README that already exists, per the original scoping - see app.py).
"""
import re
import textwrap

SECTION_HEADING = "## Synopsis"
_SYNC_NOTE = (
    "This section is generated from `meta.synopsis` in `template.json` by the authoring "
    "tool's save flow - edit the template, not this file, to change it."
)
_SECTION_RE = re.compile(rf"^{re.escape(SECTION_HEADING)}\n.*?(?=^## |\Z)", re.MULTILINE | re.DOTALL)


def _wrap(text: str) -> str:
    paragraphs = text.strip().split("\n\n")
    return "\n\n".join(
        textwrap.fill(p, width=79, break_long_words=False, break_on_hyphens=False)
        for p in paragraphs
    )


def render_section(synopsis: str) -> str:
    """The "## Synopsis" section body, including its own trailing blank line - ready to be
    inserted directly before whatever heading follows it, or at end of file."""
    body = _wrap(synopsis) if (synopsis or "").strip() else "*No synopsis authored yet.*"
    note = textwrap.fill(_SYNC_NOTE, width=79, break_long_words=False, break_on_hyphens=False)
    return f"{SECTION_HEADING}\n\n{body}\n\n{note}\n\n"


def sync(readme_text: str, synopsis: str) -> str:
    """`readme_text` with its "## Synopsis" section (heading through to the next "## "
    heading, or end of file) replaced by a freshly rendered one. A README with no such
    section gets one appended - covers a README that predates this module without silently
    dropping the sync for it."""
    new_section = render_section(synopsis)
    if _SECTION_RE.search(readme_text):
        return _SECTION_RE.sub(new_section.replace("\\", "\\\\"), readme_text, count=1)
    sep = "" if not readme_text.strip() else ("\n\n" if not readme_text.endswith("\n\n") else "")
    return readme_text.rstrip("\n") + ("\n" if readme_text.strip() else "") + sep + new_section


def minimal_readme(title: str, slug: str, synopsis: str) -> str:
    """A bare-bones README for a story that has none yet: a title line and the synced
    section, nothing else invented - the design-rationale prose in example/new_babel's
    READMEs is hand-authored and out of scope for a generator to fabricate."""
    return f"# {title or slug}\n\n{render_section(synopsis)}"

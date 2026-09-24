"""AI assist for the storyboard (Authoring_Tool_Spec.md §7, formally Phase S6 - built here
early and narrowly, on request, ahead of the full spec). v1 is exactly one recipe: given a
destination ending's `arc`/`criteria`/`hint`, propose 2-4 waypoints for it (`derive` mode,
the "Ending -> waypoints" recipe, which the spec calls out as "the most valuable mode").

Calls Gemini directly (GOOGLE_API_KEY/GEMINI_MODEL - the same pair story_engine.py's Gemini
fail-safe uses), not OpenRouter. Originally used its own OpenRouter key
(`OPENROUTER_API_KEY_TOOL_ASSIST`) specifically to avoid sharing budget/rate limits with the
gameplay tiers; switched after that key's free-tier OpenRouter workspace guardrail 404'd on
the default model, and the guardrail-allowed free models it left were themselves getting
429'd by their own shared upstream pool - a free-tier OpenRouter key wasn't reliable enough
for a feature with no fail-safe of its own. Still never touches
`story_engine.py`'s `call_llm`/`call_llm_json` or the gameplay tiers' prompt-building - a
storyboard suggestion has nothing to do with a live turn - but it does now share Gemini
account quota with story_engine's own fail-safe path; accepted trade-off, not an oversight.

No fail-safe here at all: a failed suggestion just shows an error in the assist panel,
never anything a player-facing turn depends on.

Canon is never included in the assembled context - the spec's default, without the "use
canon" opt-in checkbox this narrow version doesn't build. That's the input-side half of the
leak protection §7 describes; there's deliberately no output-side leak check here, since
there's no canon in the prompt for a suggestion to have leaked from.
"""
import json
import os

from dotenv import load_dotenv

# Must run before `import google.generativeai as genai` below - the SDK self-configures at
# import time, reading GOOGLE_API_KEY from the environment right then and caching the result
# (None, if .env hasn't been loaded yet) in a module-level singleton for the rest of the
# process's life. See story_engine.py's own load_dotenv()-before-genai ordering and the
# incident that established it.
load_dotenv()

import google.generativeai as genai  # noqa: E402
from google.api_core.exceptions import GoogleAPIError  # noqa: E402

GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "").strip()
# Independent of story_engine.py's GEMINI_MODEL only in that it's a separate env var to
# override - defaults to reading the same one, since this is meant to be "the operator's own
# Gemini key/model", not a second model to configure.
ASSIST_MODEL = os.environ.get("AUTHOR_ASSIST_MODEL") or os.environ.get("GEMINI_MODEL", "gemini-3.7-flash")
_TIMEOUT = 60
_MAX_WAYPOINTS = 4

if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)


class AssistError(Exception):
    """A suggestion request failed - no API key configured, the upstream call failed, or the
    model's response didn't parse as the expected shape. Always user-facing (the assist
    fragment shows str(e)); never raised past the route that calls this module."""


def _call_json(prompt: str) -> dict:
    if not GOOGLE_API_KEY:
        raise AssistError("GOOGLE_API_KEY is not set - AI assist is off until it is.")
    try:
        gemini = genai.GenerativeModel(
            ASSIST_MODEL,
            generation_config={"response_mime_type": "application/json", "max_output_tokens": 1024},
        )
        response = gemini.generate_content(prompt, request_options={"timeout": _TIMEOUT})
    except GoogleAPIError as e:
        raise AssistError(f"Assist request failed: {e}")

    # Same empty-response guard as story_engine._call_llm_google - e.g. a prompt-blocked
    # response has no candidates and response.text raises/returns nothing usable.
    try:
        content = response.text
    except ValueError:
        content = None
    if not content:
        raise AssistError("Assist call returned empty content.")
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise AssistError(f"Assist call returned unparseable JSON: {e}")


_WAYPOINT_PROMPT = """You are helping an author design waypoints for a destination ending in an interactive fiction engine.

A waypoint is something the story sets up on the page before this ending can happen - written
as an EVENT, never its meaning. "A buyer at Tally who pays too well" is a waypoint; "evidence
of the bloodline" is not, because that states what it means, which is the author's secret and
never the narrator's line. Waypoints steer the story toward this ending without ever naming it
or confirming what the protagonist has only guessed.

Story context (JSON):
{context}

Propose {n} NEW waypoints (do not repeat any id in existing_waypoint_ids) that would plausibly
lead into this ending's arc, consistent with its criteria and the story's tone. For each:
- "id": a short lowercase_snake_case id, unique against existing_waypoint_ids
- "plant": the event itself, one clause, at most 20 words, stating what happens and never what it means
- "detect": one sentence a story judge could use to recognise this waypoint has happened on the page

Respond with JSON only, no other text: {{"waypoints": [{{"id": "...", "plant": "...", "detect": "..."}}, ...]}}
"""


def suggest_ending_waypoints(raw: dict, ending_node: dict) -> list:
    """`ending_node` is a destination ending as the board model represents it (see
    author_model._ending_node) - `arc`/`criteria`/`hint`/`waypoints` pass through from the
    template entry untouched, so this reads them straight off the node the client sent,
    reflecting the author's live unsaved edits rather than whatever's still on disk."""
    meta = raw.get("meta", {}) or {}
    narration = raw.get("narration", {}) or {}
    world = raw.get("world", {}) or {}
    arc = ending_node.get("arc") or {}
    existing_ids = [w.get("id") for w in ending_node.get("waypoints") or [] if w.get("id")]

    context = {
        "genre": meta.get("genre", ""),
        "tone": meta.get("tone", ""),
        "style": narration.get("style", []),
        "setting_summary": world.get("setting_summary", ""),
        "ending_name": ending_node.get("title") or ending_node.get("name", ""),
        "ending_arc_title": arc.get("title", ""),
        "ending_arc_description": arc.get("description", ""),
        "ending_criteria": ending_node.get("criteria", ""),
        "ending_hint": ending_node.get("hint", ""),
        "existing_waypoint_ids": existing_ids,
    }
    prompt = _WAYPOINT_PROMPT.format(
        context=json.dumps(context, ensure_ascii=False), n=_MAX_WAYPOINTS,
    )
    result = _call_json(prompt)
    suggested = result.get("waypoints")
    if not isinstance(suggested, list) or not suggested:
        raise AssistError("Assist call returned no waypoints.")

    seen = set(existing_ids)
    # The cap applies to valid results, not to how much of the raw list gets looked at - an
    # early duplicate or malformed entry must not waste a slot that a later valid one could
    # have filled.
    out = []
    for w in suggested:
        if len(out) >= _MAX_WAYPOINTS:
            break
        if not isinstance(w, dict):
            continue
        wid = str(w.get("id") or "").strip()
        plant = str(w.get("plant") or "").strip()
        detect = str(w.get("detect") or "").strip()
        if not wid or not plant or wid in seen:
            continue
        seen.add(wid)
        out.append({"id": wid, "plant": plant, "detect": detect})
    if not out:
        raise AssistError("Assist call returned only invalid or duplicate waypoints.")
    return out

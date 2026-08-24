import copy
import json
import os
import re
import shlex
import subprocess
import sys
from dotenv import load_dotenv
import google.generativeai as genai

import state_store
from state_store import DEFAULT_STORY_SLUG, DEFAULT_USER_ID

load_dotenv()

RECENT_TURN_LIMIT = 10
SUMMARY_MAX_WORDS = 2000
SUBPLOT_TITLE_HISTORY_LIMIT = 15
FLAGS_ACTIVE_LIMIT = 25
RELATIONSHIPS_LIMIT = 20
SCENE_WORD_MIN = 470
SCENE_WORD_MAX = 500
END_STORY_PHRASES = {"end story", "end the story", "conclude the story", "wrap up the story"}
STEER_WARNING = (
    "*** STEERING MODE: this rewrites the plot directly, bypassing narration.\n"
    "    It can easily contradict what's already happened or break story coherence\n"
    "    if the command isn't well thought out. Use plot_manager.py's commands\n"
    "    ('overview', 'add-act', 'pivot', 'add-emergent', 'promote-emergent',\n"
    "    'create-alt', 'focus', 'add-goal', 'add-theme'). ***"
)

# Configure Google Gemini
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.7-flash")

if not GOOGLE_API_KEY:
    raise ValueError("GOOGLE_API_KEY not found in .env file")

genai.configure(api_key=GOOGLE_API_KEY)


class LLMUnavailableError(Exception):
    """Raised when the LLM API call fails for a reason outside our control - rate limit,
    quota exhausted, transient outage - rather than a bug in our own code. Callers (app.py's
    global error handler) should show the user a friendly retry message instead of a raw
    500, since no state was mutated when this fires (call_llm always runs before any state
    is saved for the turn)."""


def call_llm(prompt: str) -> str:
    # Imported lazily so importing story_engine doesn't require the real google-api-core
    # package - the offline test suite stubs google.generativeai but not this transitive
    # dependency, and every test that exercises call_llm's error path monkeypatches it
    # entirely, never reaching this import.
    from google.api_core.exceptions import GoogleAPIError

    model = genai.GenerativeModel(GEMINI_MODEL)
    try:
        response = model.generate_content(prompt)
    except GoogleAPIError as e:
        raise LLMUnavailableError(str(e)) from e
    return response.text


def call_llm_json(prompt: str) -> dict:
    """Call the LLM expecting a single JSON object back, tolerating markdown code fences."""
    raw = call_llm(prompt).strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines)
    return json.loads(raw)


def check_subplot_status(state: dict) -> dict:
    """Check and update subplot completion status."""
    subplots = state["plot"]["subplots"]
    completed_this_check = []

    for subplot_id, subplot in subplots.items():
        if subplot["active"] and subplot["progress"] >= subplot["completion_threshold"]:
            subplot["status"] = "completed"
            subplot["active"] = False
            completed_this_check.append(subplot_id)

            # Move to completed list
            if subplot_id not in state["plot"]["completed_subplots"]:
                state["plot"]["completed_subplots"].append(subplot_id)
                state["plot"]["pacing"]["subplots_completed_this_act"] += 1

    return {"completed": completed_this_check, "total_completed": len(state["plot"]["completed_subplots"])}


def update_progress_from_turn(state: dict, player_action: str, ai_response: str) -> dict:
    """Separate LLM pass (kept apart from narration) that extracts a state diff from the
    turn just narrated: subplot progress, flags, revealed memory fragments, entity contact,
    inventory changes, and relationship-score changes."""
    subplots = state["plot"]["subplots"]
    active_subplots = {sid: sp["title"] for sid, sp in subplots.items() if sp["active"]}
    unrevealed_fragments = {
        frag["id"]: frag["trigger"]
        for frag in state["player"]["origin"]["memory_fragments"]
        if not frag["revealed"]
    }
    relationships = state["player"].setdefault("relationships", {})

    prompt = f"""Given this turn of an interactive story, report what changed in the world state.

ACTIVE SUBPLOTS: {json.dumps(active_subplots)}
UNREVEALED MEMORY FRAGMENT TRIGGERS: {json.dumps(unrevealed_fragments)}
CURRENT FLAGS: {json.dumps(state["player"]["flags_active"])}
CURRENT INVENTORY: {json.dumps(state["player"]["inventory"])}
CURRENT RELATIONSHIPS (name: score from -100 hostile to +100 devoted, 0 neutral/unknown): {json.dumps(relationships)}

PLAYER ACTION: {player_action}
NARRATION: {ai_response}

Respond with ONLY a JSON object, no other text, in this exact shape:
{{
  "subplot_progress": {{"<subplot_id>": <integer 0-100, progress made this turn>}},
  "flags_set": {{"<flag_name>": {{"value": true, "pinned": <true if this is a foundational fact
    that should never be forgotten, e.g. a core revelation or identity; false if it's situational
    and safe to eventually forget once it's no longer recent>}}}},
  "memory_fragments_revealed": ["<fragment_id>", "..."],
  "entity_interaction": <true if the Architect appeared or acted this turn, else false>,
  "items_gained": ["<short item description>", "..."],
  "items_lost": ["<item description, matching an existing inventory entry exactly>", "..."],
  "relationship_changes": {{"<character name>": <integer delta this turn, typically -10 to +10,
    positive for trust/warmth built, negative for damage done - only named characters the player
    actually interacted with or was meaningfully affected by this turn>}}
}}
Only include subplot ids, flags, fragment ids, items, and character names that actually changed
this turn. Use {{}}/[] for nothing changed."""

    try:
        diff = call_llm_json(prompt)
    except (json.JSONDecodeError, ValueError):
        return {}

    for subplot_id, delta in diff.get("subplot_progress", {}).items():
        if subplot_id in subplots and subplots[subplot_id]["active"]:
            subplot = subplots[subplot_id]
            subplot["progress"] = max(0, min(subplot["completion_threshold"], subplot["progress"] + int(delta)))

    turn_count = state["plot"]["pacing"]["turn_count"]
    for flag_name, flag_info in diff.get("flags_set", {}).items():
        if isinstance(flag_info, dict):
            value = flag_info.get("value", True)
            pinned = bool(flag_info.get("pinned", False))
        else:
            # tolerate a bare boolean if the model doesn't follow the nested shape
            value = flag_info
            pinned = False
        state["player"]["flags_active"][flag_name] = value
        state["player"]["flags_meta"][flag_name] = {"turn_set": turn_count, "pinned": pinned}

    revealed_ids = set(diff.get("memory_fragments_revealed", []))
    for frag in state["player"]["origin"]["memory_fragments"]:
        if frag["id"] in revealed_ids:
            frag["revealed"] = True

    if diff.get("entity_interaction"):
        state["plot"]["entity_interaction_count"] += 1

    inventory = state["player"]["inventory"]
    for item in diff.get("items_gained", []):
        if item:
            inventory.append(item)
    for item in diff.get("items_lost", []):
        if item in inventory:
            inventory.remove(item)

    for char_name, delta in diff.get("relationship_changes", {}).items():
        if not char_name:
            continue
        relationships[char_name] = max(-100, min(100, relationships.get(char_name, 0) + int(delta)))
    # Bounded like flags_active: if a story accumulates more named relationships than this,
    # drop the least narratively significant ones first (closest to neutral), not the oldest -
    # a strongly-loved or strongly-hated character should never be the one that gets evicted.
    if len(relationships) > RELATIONSHIPS_LIMIT:
        for name in sorted(relationships, key=lambda n: abs(relationships[n]))[:len(relationships) - RELATIONSHIPS_LIMIT]:
            del relationships[name]

    return diff


def archive_stale_flags(state: dict):
    """Keep flags_active bounded without an LLM call: once a flag's setting turn falls
    outside the recent-turns window, it's retired to flags_archive - by then its
    consequences have already had a chance to pass through the compressed_summary
    rollover, so nothing narratively important is silently lost. Pinned flags (foundational
    facts) are exempt. A hard cap on flags_active is a fallback in case pins pile up."""
    player = state["player"]
    turn_count = state["plot"]["pacing"]["turn_count"]
    stale_cutoff = turn_count - RECENT_TURN_LIMIT

    for flag_name in list(player["flags_active"].keys()):
        meta = player["flags_meta"].get(flag_name, {})
        if meta.get("pinned"):
            continue
        if meta.get("turn_set", turn_count) <= stale_cutoff:
            player["flags_archive"][flag_name] = player["flags_active"].pop(flag_name)
            player["flags_meta"].pop(flag_name, None)

    if len(player["flags_active"]) > FLAGS_ACTIVE_LIMIT:
        evictable = sorted(
            (name for name in player["flags_active"] if not player["flags_meta"].get(name, {}).get("pinned")),
            key=lambda name: player["flags_meta"].get(name, {}).get("turn_set", 0),
        )
        for name in evictable:
            if len(player["flags_active"]) <= FLAGS_ACTIVE_LIMIT:
                break
            player["flags_archive"][name] = player["flags_active"].pop(name)
            player["flags_meta"].pop(name, None)


def generate_new_subplot(state: dict):
    """Invent and insert a new subplot to keep the pool topped up. No-op once the story
    is in its ending sequence, or if the pool is already full."""
    plot = state["plot"]
    if plot["endgame"]["requested"]:
        return None

    subplots = plot["subplots"]
    live_count = sum(1 for sp in subplots.values() if sp["status"] != "completed")
    if live_count >= plot["pacing"]["max_parallel_subplots"]:
        return None

    # Live subplots are naturally bounded (max_parallel_subplots); completed ones
    # accumulate for the whole game, so only keep the most recent ones for dedup
    # context instead of sending every title ever generated.
    live_titles = [sp["title"] for sp in subplots.values() if sp["status"] != "completed"]
    recent_completed_ids = plot["completed_subplots"][-SUBPLOT_TITLE_HISTORY_LIMIT:]
    recent_completed_titles = [subplots[sid]["title"] for sid in recent_completed_ids if sid in subplots]
    existing_titles = live_titles + recent_completed_titles
    summary = state["history_log"]["compressed_summary"] or "The story has just begun."
    main_thread = plot["main_thread"]
    current_act = main_thread["acts"][main_thread["current_act"] - 1]

    prompt = f"""Invent a new subplot for an ongoing interactive story.

WORLD RULES:
{chr(10).join(f"- {r}" for r in state["world"]["rules"])}

MAIN THREAD: {main_thread['title']} - {main_thread['description']}
CURRENT ACT: {current_act['title']} - {current_act['description']}
STORY SO FAR: {summary}
EXISTING SUBPLOT TITLES (do not repeat): {', '.join(existing_titles) or 'none'}
EMERGING THEMES: {', '.join(plot.get('thread_steering', {}).get('emerging_themes', [])) or 'none noted'}

Respond with ONLY a JSON object, no other text:
{{
  "title": "<short subplot title>",
  "description": "<1-2 sentence description>",
  "priority": "<high|medium|low>",
  "ties_to_main_plot": "<how this connects to the main thread>"
}}"""

    try:
        generated = call_llm_json(prompt)
        title = generated["title"]
        description = generated["description"]
    except (json.JSONDecodeError, KeyError, ValueError):
        return None

    existing_numbers = [
        int(sid.rsplit("_", 1)[-1])
        for sid in subplots
        if sid.rsplit("_", 1)[-1].isdigit()
    ]
    next_number = (max(existing_numbers) + 1) if existing_numbers else 1
    new_id = f"subplot_{next_number:03d}"

    active_count = sum(1 for sp in subplots.values() if sp["active"])
    make_active = active_count < plot["pacing"]["max_parallel_subplots"]

    subplots[new_id] = {
        "id": new_id,
        "title": title,
        "description": description,
        "priority": generated.get("priority", "medium"),
        "status": "active" if make_active else "not_started",
        "progress": 0,
        "completion_threshold": 100,
        "ties_to_main_plot": generated.get("ties_to_main_plot", ""),
        "active": make_active,
    }
    return new_id


def is_end_story_command(action: str) -> bool:
    return action.strip().lower() in END_STORY_PHRASES


def handle_end_story_request(state: dict) -> dict:
    """Commit the story to a finale: no more new subplots or acts, just resolution."""
    plot = state["plot"]
    endgame = plot["endgame"]
    if endgame["requested"]:
        return endgame["final_arc"]

    active_subplots = [sp["title"] for sp in plot["subplots"].values() if sp["active"]]
    main_thread = plot["main_thread"]
    summary = state["history_log"]["compressed_summary"] or "The story has just begun."

    prompt = f"""The player has asked to conclude this interactive story. Design a closing arc that
resolves it satisfyingly, tying together the threads already in motion.

MAIN THREAD: {main_thread['title']} - {main_thread['description']}
STORY SO FAR: {summary}
ACTIVE SUBPLOTS TO RESOLVE: {', '.join(active_subplots) or 'none'}

Respond with ONLY a JSON object, no other text:
{{
  "title": "<title for the closing arc>",
  "description": "<2-3 sentences describing how the story should resolve>"
}}"""

    try:
        generated = call_llm_json(prompt)
    except (json.JSONDecodeError, ValueError):
        generated = {}

    final_arc = {
        "title": generated.get("title", "The Reckoning"),
        "description": generated.get(
            "description",
            "Bring the story's open threads to a close as gracefully as the current momentum allows.",
        ),
    }

    endgame["requested"] = True
    endgame["requested_turn"] = plot["pacing"]["turn_count"]
    endgame["final_arc"] = final_arc

    main_thread["acts"].append({
        "act_number": len(main_thread["acts"]) + 1,
        "title": final_arc["title"],
        "description": final_arc["description"],
        "completion_signals": [],
        "completed": False,
        "optional": False,
        "is_finale": True,
    })
    main_thread["current_act"] = len(main_thread["acts"])

    return final_arc


def check_and_advance_act(state: dict):
    """At a pacing checkpoint, ask the director whether the current act has narratively
    resolved and, if so, generate the next one. No-op once the story is ending."""
    plot = state["plot"]
    if plot["endgame"]["requested"]:
        return None

    pacing = plot["pacing"]
    if pacing["subplots_completed_this_act"] < 1:
        return None

    main_thread = plot["main_thread"]
    current_act = next(
        (act for act in main_thread["acts"] if act["act_number"] == main_thread["current_act"]),
        None,
    )
    if not current_act:
        return None

    summary = state["history_log"]["compressed_summary"] or "The story has just begun."
    recent = "\n".join(state["history_log"]["recent_turns"][-RECENT_TURN_LIMIT:])
    # completed_subplots accumulates for the whole game; subplots_completed_this_act
    # tells us how many of the most recent entries belong to the current act, so slice
    # to just those instead of feeding in every subplot ever completed.
    recent_completed_ids = plot["completed_subplots"][-pacing["subplots_completed_this_act"]:]
    completed_titles = [
        plot["subplots"][sid]["title"]
        for sid in recent_completed_ids
        if sid in plot["subplots"]
    ]
    revealed_fragments = sum(1 for f in state["player"]["origin"]["memory_fragments"] if f["revealed"])

    prompt = f"""You are the pacing director for an ongoing interactive story. Judge whether the
current act feels narratively resolved, based on what's actually happened - not a checklist.

CURRENT ACT: {current_act['title']} - {current_act['description']}
SIGNALS THIS ACT WAS BUILT AROUND: {', '.join(current_act.get('completion_signals', [])) or 'none'}
SUBPLOTS COMPLETED THIS ACT: {', '.join(completed_titles) or 'none'}
MEMORY FRAGMENTS REVEALED: {revealed_fragments}
ARCHITECT ENCOUNTERS: {plot['entity_interaction_count']}
STORY SO FAR: {summary}
RECENT EXCHANGES:
{recent}

Respond with ONLY a JSON object, no other text:
{{
  "ready": <true|false>,
  "reason": "<one sentence>",
  "next_act_title": "<title, only if ready>",
  "next_act_description": "<1-2 sentences, only if ready>"
}}"""

    try:
        verdict = call_llm_json(prompt)
    except (json.JSONDecodeError, ValueError):
        return None

    if not verdict.get("ready"):
        return None

    current_act["completed"] = True
    new_act_number = len(main_thread["acts"]) + 1
    main_thread["acts"].append({
        "act_number": new_act_number,
        "title": verdict.get("next_act_title") or f"Act {new_act_number}",
        "description": verdict.get("next_act_description", ""),
        "completion_signals": [],
        "completed": False,
        "optional": False,
    })
    main_thread["act_history"].append({
        "from_act": current_act["act_number"],
        "to_act": new_act_number,
        "reason": verdict.get("reason", ""),
        "turn": pacing["turn_count"],
    })
    main_thread["current_act"] = new_act_number
    pacing["subplots_completed_this_act"] = 0

    return new_act_number


def generate_pacing_nudge(state: dict) -> str:
    """Generate a meta-instruction to nudge the story toward active goals."""
    pacing = state["plot"]["pacing"]
    main_thread = state["plot"]["main_thread"]
    subplots = state["plot"]["subplots"]

    # Get active subplots
    active_subplots = [(sid, sp) for sid, sp in subplots.items() if sp["active"]]

    nudge_parts = []

    # Main plot nudge
    current_act = next((act for act in main_thread["acts"] if act["act_number"] == main_thread["current_act"]), None)
    if current_act:
        nudge_parts.append(f"PACING: Currently in Act {current_act['act_number']} - {current_act['description']}")

    # Subplot nudges
    if active_subplots:
        # Find the highest priority active subplot
        priority_map = {"high": 3, "medium": 2, "low": 1}
        active_subplots_sorted = sorted(active_subplots, key=lambda x: priority_map.get(x[1]["priority"], 0), reverse=True)

        primary_subplot = active_subplots_sorted[0][1]
        nudge_parts.append(f"ACTIVE SUBPLOT: '{primary_subplot['title']}' - {primary_subplot['description']}")

        if len(active_subplots) > 1:
            other_titles = [sp["title"] for _, sp in active_subplots_sorted[1:3]]
            nudge_parts.append(f"BACKGROUND SUBPLOTS: {', '.join(other_titles)}")

    # Check if we need to introduce new subplots
    max_parallel = pacing["max_parallel_subplots"]
    if len(active_subplots) < max_parallel:
        inactive_subplots = [(sid, sp) for sid, sp in subplots.items() if sp["status"] == "not_started"]
        if inactive_subplots:
            nudge_parts.append(f"SUBPLOT OPPORTUNITY: Consider introducing hooks for '{inactive_subplots[0][1]['title']}' when appropriate.")

    pacing["last_pacing_direction"] = " | ".join(nudge_parts)
    return "\n".join(nudge_parts)


def build_system_prompt(state: dict) -> str:
    meta = state["meta"]
    scene = state["plot"]["current_scene"]
    player = state["player"]
    pacing = state["plot"]["pacing"]
    endgame = state["plot"]["endgame"]

    rules_str = "\n".join(f"- {r}" for r in state["world"]["rules"])
    summary = state["history_log"]["compressed_summary"] or "The story has just begun."
    recent = "\n".join(state["history_log"]["recent_turns"][-RECENT_TURN_LIMIT:])

    if endgame["requested"]:
        active_titles = [sp["title"] for sp in state["plot"]["subplots"].values() if sp["active"]]
        final_arc = endgame["final_arc"] or {}
        pacing_instruction = f"""

ENDGAME: The player has asked to conclude the story. Narrate toward a satisfying, conclusive
ending for: "{final_arc.get('title', '')}" - {final_arc.get('description', '')}
Resolve these open threads and do not introduce any new subplots, factions, or plot threads: {', '.join(active_titles) or 'none remaining'}.
When the story reaches a natural conclusion, end the narration with the exact line "THE END" on
its own line. Do not include an "OPTIONS:" block or numbered choices.
"""
        instruction_footer = (
            f"Continue the story based on the player's next action, moving it toward its "
            f"conclusion. Narrate the scene itself in {SCENE_WORD_MIN}-{SCENE_WORD_MAX} words."
        )
    else:
        pacing_instruction = ""
        if pacing["turns_since_last_pacing_nudge"] >= pacing["pacing_nudge_frequency"]:
            pacing_instruction = f"\n\n{generate_pacing_nudge(state)}\n"
            pacing["turns_since_last_pacing_nudge"] = 0
        instruction_footer = (
            f"Continue the story based on the player's next action. Narrate the scene itself in "
            f"{SCENE_WORD_MIN}-{SCENE_WORD_MAX} words. End your narration with a "
            "blank line, then the exact heading \"OPTIONS:\" on its own line, followed by "
            "exactly 3 numbered options (1. / 2. / 3.), one per line, each in the exact format "
            "<short third-person action label> || <1-2 sentence first-person prose rendition of "
            "taking that action, in the player's voice>. Keep the action label under 15 words, "
            "distinct, and plausible. No extra commentary after the list."
        )

    return f"""You are the narrator of an interactive story.

TITLE: {meta['title']} | GENRE: {meta['genre']} | TONE: {meta['tone']}
CONTENT RULES: {', '.join(meta['content_rules'])}

WORLD RULES (must not be broken):
{rules_str}

STORY SO FAR: {summary}

RECENT EXCHANGES:
{recent}
{pacing_instruction}
CURRENT SCENE ({scene['location']}): {scene['summary']}
PLAYER: {player['name']} | Traits: {', '.join(player['traits'])} | Inventory: {', '.join(player['inventory']) or 'nothing'} | Relationships: {player.get('relationships', {})} | Flags: {player['flags_active']}

Stay strictly within the established world, tone, and rules above.
{instruction_footer}
"""


_OPTIONS_HEADING_RE = re.compile(r"OPTIONS\s*:\s*\n?", re.IGNORECASE)
_OPTION_LINE_RE = re.compile(r"^\s*\d+[.)]\s*(.+?)\s*\|\|\s*(.+)$", re.MULTILINE)


def parse_narration_and_options(text: str):
    """Splits a narration response from its trailing "OPTIONS:" block (see the
    instruction_footer format required in build_system_prompt) into
    (narration_without_options, [option_1, option_2, option_3]), where each option is
    {"action": <short third-person label, shown on the choice button>, "prose": <first-person
    prose rendition, shown as a preview and - if this option is picked - submitted as the
    player's actual action, so what lands in the novel is the prose, not the menu label>}.

    Falls back to (text, []) whenever a well-formed 3-option "action || prose" block isn't
    found - the fixed opening scene, an endgame turn (which is told not to produce one), or a
    model that ignored the format. Callers should treat an empty list as "no buttons, free-text
    action only", not an error."""
    match = _OPTIONS_HEADING_RE.search(text)
    if not match:
        return text.strip(), []
    narration = text[:match.start()].rstrip()
    options = [
        {"action": action.strip(), "prose": prose.strip()}
        for action, prose in _OPTION_LINE_RE.findall(text[match.end():])
        if action.strip() and prose.strip()
    ][:3]
    if len(options) < 3:
        return text.strip(), []
    return narration, options


def update_state_after_turn(
    state: dict,
    player_action: str,
    ai_response: str,
    user_id: str = DEFAULT_USER_ID,
    story_slug: str = DEFAULT_STORY_SLUG,
):
    turn_text = f"Player: {player_action}\nNarrator: {ai_response}"
    state["history_log"]["recent_turns"].append(turn_text)

    # Update pacing counters
    state["plot"]["pacing"]["turn_count"] += 1
    state["plot"]["pacing"]["turns_since_last_pacing_nudge"] += 1

    # Separate state-update pass: subplot progress, flags, memory fragments, entity contact
    update_progress_from_turn(state, player_action, ai_response)

    # Retire non-pinned flags that have aged out of the recent-turns window
    archive_stale_flags(state)

    # Check subplot completion status, then keep the pool topped up
    status = check_subplot_status(state)
    for _ in status["completed"]:
        generate_new_subplot(state)

    # See if the current act has narratively resolved and needs a successor
    check_and_advance_act(state)

    # Roll oldest turns into compressed summary once over the limit
    if len(state["history_log"]["recent_turns"]) > RECENT_TURN_LIMIT:
        overflow = state["history_log"]["recent_turns"][:-RECENT_TURN_LIMIT]
        state["history_log"]["recent_turns"] = state["history_log"]["recent_turns"][-RECENT_TURN_LIMIT:]

        # Verbatim, unbounded, disk-only archive of every turn's full text once it's about
        # to be lossy-compressed away - never read back into a prompt, so it costs nothing
        # in LLM context no matter how long the game runs.
        state["history_log"].setdefault("full_transcript", []).extend(overflow)

        summary_prompt = f"""Update the running story summary below by folding in these new events.
Preserve key facts, decisions, consequences, and anything that might matter later (open
threads, foreshadowing, unresolved stakes). The result must stay under {SUMMARY_MAX_WORDS}
words total, so drop lower-priority detail as needed rather than just appending.

CURRENT SUMMARY: {state["history_log"]["compressed_summary"] or "(none yet)"}

NEW EVENTS:
{chr(10).join(overflow)}

Respond with ONLY the updated summary text, under {SUMMARY_MAX_WORDS} words, no preamble."""
        updated_summary = call_llm(summary_prompt)
        state["history_log"]["compressed_summary"] = updated_summary.strip()

    state_store.save_state(state, user_id, story_slug)


def apply_opening_name(state: dict, raw_name: str) -> str:
    """Pure state mutation, no I/O: applies a (possibly blank) name to the opening
    scene - sets player.name, substitutes it into narration_after_name, marks the
    opening played, and logs the synthetic turn for LLM continuity. Returns the
    after-name narration text. Shared by the CLI opening (run_opening_scene, which
    wraps this with print/input) and the web /play route (which wraps it with a
    render/form instead)."""
    name = (raw_name or "").strip() or "Subject Zero"
    state["player"]["name"] = name

    opening = state["plot"]["opening_scene"]
    after_name = opening["narration_after_name"].replace("{player_name}", name)
    opening["played"] = True
    full_opening_text = f"{opening['narration_before_name']}\n\n{after_name}"
    state["history_log"]["recent_turns"].append(f"Narrator: {full_opening_text}")
    return after_name


def run_opening_scene(user_id: str = DEFAULT_USER_ID, story_slug: str = DEFAULT_STORY_SLUG):
    """Plays the fixed, hand-authored opening (plot.opening_scene in the story's
    template) - the one constant beat every playthrough starts from, everything after
    branches from here. Captures the protagonist's name diegetically, in-fiction, as
    part of the intake scene itself, rather than a raw pre-game prompt. No-ops if
    already played, so resuming a save doesn't replay the intro."""
    state = state_store.load_state(user_id, story_slug)
    if state["plot"]["opening_scene"]["played"]:
        return

    print(state["plot"]["opening_scene"]["narration_before_name"])
    raw_name = input("\n> ")
    after_name = apply_opening_name(state, raw_name)
    print(f"\n{after_name}")

    state_store.save_state(state, user_id, story_slug)


def handle_steer_command(
    steer_args: str,
    user_id: str = DEFAULT_USER_ID,
    story_slug: str = DEFAULT_STORY_SLUG,
):
    """Forwards a 'steer ...' command typed at the game prompt to plot_manager.py's
    existing CLI, so the player can directly reshape the plot (pivot, add an act, note
    an emergent direction, etc.) without leaving the session. Reuses plot_manager.py
    as-is rather than duplicating its command parsing - it already loads/saves the same
    save file (via --user/--story), so the next turn picks up whatever changed
    immediately. CLI-only: there's no web equivalent (see CLAUDE.md)."""
    print(STEER_WARNING)
    args = shlex.split(steer_args) if steer_args.strip() else []
    # Absolute path, not a bare "plot_manager.py" - unlike a plain `import`, subprocess argv
    # resolution isn't looked up via sys.path, so this must stay correct regardless of the
    # caller's cwd (both plot_manager.py and story_engine.py live in backend/ together).
    plot_manager_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plot_manager.py")
    subprocess.run(
        [sys.executable, plot_manager_path, "--user", user_id, "--story", story_slug] + args
    )


def _generate_and_apply_turn(
    state: dict,
    player_action: str,
    pre_turn_snapshot: dict,
    user_id: str,
    story_slug: str,
) -> bool:
    """Shared by take_turn and regenerate_last_turn: calls the LLM for player_action against
    the given state, applies the resulting state-update pass, and stashes pre_turn_snapshot
    (a deep copy of state from just before this turn) so a later regenerate_last_turn call can
    restore to exactly this point and re-roll. Returns True once the story has concluded."""
    prompt = build_system_prompt(state) + f"\n\nPlayer action: {player_action}\n\nNarrator:"
    ai_response = call_llm(prompt)
    print(ai_response)

    update_state_after_turn(state, player_action, ai_response, user_id, story_slug)

    # Bounded to exactly one level (this turn only, overwriting whatever was pending before) -
    # regenerating re-rolls the latest scene, it isn't a multi-step undo stack.
    state["history_log"]["pending_regenerate"] = {
        "state": pre_turn_snapshot,
        "player_action": player_action,
    }

    if state["plot"]["endgame"]["requested"] and "THE END" in ai_response:
        state["plot"]["endgame"]["concluded"] = True

    state_store.save_state(state, user_id, story_slug)
    return state["plot"]["endgame"]["concluded"]


def take_turn(
    player_action: str,
    user_id: str = DEFAULT_USER_ID,
    story_slug: str = DEFAULT_STORY_SLUG,
) -> bool:
    """Runs one turn of the story. Returns True once the story has concluded (THE END)."""
    state = state_store.load_state(user_id, story_slug)

    if is_end_story_command(player_action) and not state["plot"]["endgame"]["requested"]:
        final_arc = handle_end_story_request(state)
        state_store.save_state(state, user_id, story_slug)
        print(f"\n[The story is moving toward its conclusion: {final_arc['title']}]\n")

    pre_turn_snapshot = copy.deepcopy(state)
    pre_turn_snapshot["history_log"].pop("pending_regenerate", None)
    return _generate_and_apply_turn(state, player_action, pre_turn_snapshot, user_id, story_slug)


def regenerate_last_turn(
    user_id: str = DEFAULT_USER_ID,
    story_slug: str = DEFAULT_STORY_SLUG,
) -> bool:
    """Re-rolls the most recent turn in place: restores state to exactly before that turn (so
    its subplot progress, flags, pacing, and appended recent_turns entry are all undone), then
    re-runs the same player_action through a fresh LLM call. Does not replay/re-detect the
    end-story command - that's only evaluated once, on the original take_turn call.

    Returns False with no effect if there's nothing to regenerate (no turn taken yet, e.g. the
    fixed opening scene, or a save from before this feature existed)."""
    state = state_store.load_state(user_id, story_slug)
    pending = state["history_log"].get("pending_regenerate")
    if not pending:
        return False

    restored_state = pending["state"]
    player_action = pending["player_action"]
    pre_turn_snapshot = copy.deepcopy(restored_state)
    return _generate_and_apply_turn(restored_state, player_action, pre_turn_snapshot, user_id, story_slug)


if __name__ == "__main__":
    cli_user_id, cli_story_slug, _ = state_store.parse_user_story_args(sys.argv[1:])

    run_opening_scene(cli_user_id, cli_story_slug)
    print(
        '\nSpecial commands, typed at the prompt like a normal action:\n'
        '  "quit" / "exit"    - leave the session\n'
        '  "end story"        - begin wrapping the narrative up for good\n'
        '  "steer ..."        - directly reshape the plot via plot_manager.py (e.g. '
        '"steer overview", "steer add-goal \'...\'") - can break story coherence if misused, see warning'
    )
    while True:
        action = input("\n> ")
        if action.lower() in ("quit", "exit"):
            break
        if action.lower() == "steer" or action.lower().startswith("steer "):
            handle_steer_command(action[len("steer"):].strip(), cli_user_id, cli_story_slug)
            continue
        if take_turn(action, cli_user_id, cli_story_slug):
            break

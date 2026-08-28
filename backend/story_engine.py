import concurrent.futures
import copy
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
from dotenv import load_dotenv
import google.generativeai as genai
import requests

import state_store
from state_store import DEFAULT_STORY_SLUG, DEFAULT_USER_ID

load_dotenv()

RECENT_TURN_LIMIT = 10
SUMMARY_MAX_WORDS = 2000
SUBPLOT_TITLE_HISTORY_LIMIT = 15
FLAGS_ACTIVE_LIMIT = 25
RELATIONSHIPS_LIMIT = 20
# Stats (player.stats) are a story-authored, per-class mechanic (see character_classes/
# apply_class_selection below) - unlike relationships/flags there's no single fixed scale
# across stories (one story might use 0-10 attributes, another a 0-100 meter like "health"),
# so only a floor is enforced generically; each story's own class definitions imply their
# own effective ceiling, same trust level as the already-unbounded traits/inventory lists.
STAT_FLOOR = 0
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

# --- LLM provider configuration ---
# Each tier now has its OWN provider, not one global switch: narration always goes through
# OpenRouter/DeepSeek (LLM_PROVIDER), while the state-update tier defaults to calling
# Google's Gemini API directly (STATE_UPDATE_PROVIDER, the operator's own GOOGLE_API_KEY,
# not routed through OpenRouter). This is NOT automatic failover - there's still no
# fallback-on-failure between providers for a given call, each tier's provider is just a
# fixed, deliberate choice, made independently per tier instead of once for the whole
# process. LLM_PROVIDER=google (forced by the offline test suite, see test/_llm_stubs.py)
# is the exception: it's a whole-process debug/testing override, and when active it wins
# for BOTH tiers regardless of STATE_UPDATE_PROVIDER - see call_llm's docstring.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openrouter")
STATE_UPDATE_PROVIDER = os.getenv("STATE_UPDATE_PROVIDER", "google")
for _provider in (LLM_PROVIDER, STATE_UPDATE_PROVIDER):
    if _provider not in ("openrouter", "google"):
        raise ValueError(f"Unknown provider {_provider!r} - expected 'openrouter' or 'google'")

# Two cost/quality tiers, matched to what each call actually needs: narration is the one
# big creative generation per turn (see build_system_prompt); every other call - state-update
# extraction, subplot generation, act-advancement judgment, ending-arc generation, and the
# compressed-summary rollover - is short, structured output where the cheaper/faster model
# is the better fit. call_llm/call_llm_json's own defaults already route to the right tier,
# so most call sites below never need to pass model= explicitly.
NARRATION_MODEL = os.getenv("NARRATION_MODEL", "deepseek/deepseek-v4-pro-20260813")
# A real Gemini model name (no "google/" prefix - that's OpenRouter's slug convention, not
# the direct API's), since STATE_UPDATE_PROVIDER defaults to "google" above.
STATE_UPDATE_MODEL = os.getenv("STATE_UPDATE_MODEL", "gemini-3.5-flash-lite")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.7-flash")

if "openrouter" in (LLM_PROVIDER, STATE_UPDATE_PROVIDER) and not OPENROUTER_API_KEY:
    raise ValueError("OPENROUTER_API_KEY not found in .env file")
if "google" in (LLM_PROVIDER, STATE_UPDATE_PROVIDER):
    if not GOOGLE_API_KEY:
        raise ValueError("GOOGLE_API_KEY not found in .env file")
    genai.configure(api_key=GOOGLE_API_KEY)


class LLMUnavailableError(Exception):
    """Raised when the LLM API call fails for a reason outside our control - rate limit,
    quota exhausted, transient outage - rather than a bug in our own code, regardless of
    which provider is active. Callers (app.py's take_turn/regenerate_turn routes) should
    show the user a friendly retry message instead of a raw 500, since no state was mutated
    when this fires (call_llm always runs before any state is saved for the turn)."""


# requests' own `timeout=` is NOT a total-call deadline - per the library's own docs, it
# only fires if zero bytes arrive for that many seconds. A response that trickles in slowly
# enough (no single gap that long) can run for many minutes without ever tripping it. That's
# what actually happened in production: requests' timeout=120 never fired, and the call ran
# past gunicorn's --timeout (Dockerfile CMD) instead, which hard-kills the worker via SIGABRT
# - no clean response reaches the client, and the turn's state is never saved (see git log for
# two real incidents). OPENROUTER_TOTAL_TIMEOUT/GOOGLE_TOTAL_TIMEOUT below are genuine
# wall-clock deadlines around each *whole* call, enforced by running it in a background
# thread and giving up on .result(timeout=...) rather than waiting on it. Sized so even the
# worst case (primary call times out, then the Gemini fail-safe below also times out) stays
# comfortably under gunicorn's --timeout (Dockerfile CMD), so a double-timeout is always
# caught here first, cleanly, instead of by gunicorn's SIGABRT.
OPENROUTER_TOTAL_TIMEOUT = 100
GOOGLE_TOTAL_TIMEOUT = 60
_openrouter_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=8, thread_name_prefix="openrouter-call"
)
_google_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=8, thread_name_prefix="google-call"
)


def _call_llm_openrouter(prompt: str, model: str) -> str:
    def do_request():
        response = requests.post(
            OPENROUTER_URL,
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                # deepseek-v4-flash-0731 (STATE_UPDATE_MODEL) alone is resold through 29
                # different OpenRouter providers, with measured throughput ranging 6-109
                # tok/s and TTFT 0.42-2.42s depending which one a request lands on -
                # OpenRouter's default routing doesn't optimize for this, so a real
                # production call landed on the slow end (see git log: an 83s state-update
                # call was the dominant cost in a 132s turn). This asks OpenRouter to
                # prefer whichever provider is currently fastest for the requested model,
                # instead of leaving that to chance - same model, same price, just routed
                # better. Applies to every OpenRouter call (narration included), not just
                # the state-update tier, since it can only help.
                "provider": {"sort": "throughput"},
            },
            # Still set (not None) as a lower-level guard: if the connection goes fully
            # dead rather than just trickling, this bounds how long an abandoned thread
            # lingers after OPENROUTER_TOTAL_TIMEOUT gives up on it below.
            timeout=120,
        )
        response.raise_for_status()
        return response.json()

    future = _openrouter_executor.submit(do_request)
    try:
        data = future.result(timeout=OPENROUTER_TOTAL_TIMEOUT)
    except concurrent.futures.TimeoutError:
        raise LLMUnavailableError(
            f"OpenRouter did not respond within {OPENROUTER_TOTAL_TIMEOUT}s"
        )
    except requests.exceptions.RequestException as e:
        raise LLMUnavailableError(str(e)) from e

    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise LLMUnavailableError(f"Unexpected OpenRouter response shape: {data}") from e


def _call_llm_google(prompt: str, model: str) -> str:
    # Imported lazily so importing story_engine doesn't require the real google-api-core
    # package under LLM_PROVIDER=openrouter - the offline test suite stubs
    # google.generativeai but not this transitive dependency, and forces
    # LLM_PROVIDER=google specifically to exercise this path against the stub.
    from google.api_core.exceptions import GoogleAPIError

    def do_request():
        gemini = genai.GenerativeModel(model)
        return gemini.generate_content(prompt)

    future = _google_executor.submit(do_request)
    try:
        response = future.result(timeout=GOOGLE_TOTAL_TIMEOUT)
    except concurrent.futures.TimeoutError:
        raise LLMUnavailableError(f"Gemini did not respond within {GOOGLE_TOTAL_TIMEOUT}s")
    except GoogleAPIError as e:
        raise LLMUnavailableError(str(e)) from e
    return response.text


def call_llm(prompt: str, model: str = NARRATION_MODEL, provider: str = None) -> str:
    """Sends prompt to the given (or default) provider and returns the raw text response.
    provider defaults to LLM_PROVIDER (the narration tier's setting); call_llm_json passes
    STATE_UPDATE_PROVIDER explicitly instead, since that tier is routed independently (see
    "LLM provider configuration" above) - as does update_state_after_turn's summary-rollover
    call site, the one place that calls call_llm directly for a state-update-tier prompt.

    Under the whole-process testing/debug override (LLM_PROVIDER=google, forced by the
    offline test suite), model is ignored in favor of GEMINI_MODEL regardless of which
    tier or provider triggered this - NARRATION_MODEL/STATE_UPDATE_MODEL default to model
    names that aren't valid Gemini ones (an OpenRouter slug, a real Gemini name
    respectively), so respecting them here would break that path. Outside of that
    (STATE_UPDATE_PROVIDER=google in real production use), model IS respected.

    Fail-safe: if the primary call raises LLMUnavailableError, this retries once against
    the operator's own free-tier Gemini model (GEMINI_MODEL) via a direct Google API call,
    before giving up - lets NARRATION_MODEL/STATE_UPDATE_MODEL be freely swapped to
    whatever's being tried (e.g. an experimental OpenRouter model) without an unreachable
    or misconfigured model taking the whole app down. This IS a genuine runtime fallback
    (unlike LLM_PROVIDER/STATE_UPDATE_PROVIDER's fixed per-tier provider selection, which
    still isn't one) - deliberately narrow in scope: it only ever falls back TO Gemini,
    never away from it, and only on a request-level failure, never a silent retry on
    output that merely looks wrong (e.g. malformed JSON - call_llm_json's caller decides
    what to do with that, same as before). Compares against the model actually attempted
    (effective_model), not the raw model argument, so this doesn't uselessly retry the
    exact same Gemini call a second time when the testing override already substituted
    GEMINI_MODEL in for a non-Gemini model argument."""
    provider = provider or LLM_PROVIDER
    effective_model = GEMINI_MODEL if provider == "google" and LLM_PROVIDER == "google" else model
    try:
        if provider == "google":
            return _call_llm_google(prompt, effective_model)
        return _call_llm_openrouter(prompt, model)
    except LLMUnavailableError as primary_error:
        if provider == "google" and effective_model == GEMINI_MODEL:
            raise  # this WAS the fail-safe call - nothing left to fall back to
        print(f"[FAILSAFE] primary call failed (provider={provider!r} model={effective_model!r}): "
              f"{primary_error} - retrying via Gemini fail-safe ({GEMINI_MODEL})")
        return _call_llm_google(prompt, GEMINI_MODEL)


def call_llm_json(prompt: str, model: str = STATE_UPDATE_MODEL) -> dict:
    """Call the LLM expecting a single JSON object back, tolerating markdown code fences."""
    raw = call_llm(prompt, model=model, provider=STATE_UPDATE_PROVIDER).strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines)
    return json.loads(raw)


# One evocative word per _timed() label, in the same register as play.html's placeholder
# busy-indicator words (Drafting, Weaving, Conjuring, ...) - shown in the web UI's busy
# popup via the /api/status poll (app.py's turn_status route) so a player sees what phase
# of the turn is actually running, not just a generic "Working...". Keep this in sync with
# every _timed() call site below.
STATUS_LABELS = {
    "narration": "Narrating",
    "state_update": "Reckoning",
    "subplot_generation": "Branching",
    "act_advancement_check": "Weighing",
    "end_story_final_arc": "Concluding",
    "summary_rollover": "Remembering",
}

# Sets (user_id, story_slug) for the duration of one take_turn/regenerate_last_turn call so
# _timed() (several stack frames deeper, in functions that don't themselves take user_id/
# story_slug) knows where to write its status beacon. Thread-local rather than a plain
# module global: each gunicorn worker (Dockerfile's CMD - sync workers, one request at a
# time per process) always runs one request's whole _timed() chain on its single main
# thread (call_llm's own ThreadPoolExecutor use is internal to it - _timed() itself never
# crosses threads), so thread-local storage stays correctly scoped per-request with no
# locking - and it degrades safely rather than crossing users if a future change ever moves
# this to threaded workers.
_status_ctx = threading.local()


def _timed(label: str, fn, model: str):
    """Wraps a single LLM call with wall-clock timing, printed to stdout (captured by
    `docker logs`/gunicorn's access log, same as the existing narration print). A turn can
    involve up to five sequential calls - narration, state-update, and conditionally
    subplot generation, act-advancement judgment, and the summary rollover - so total
    request duration alone (the access log's one number) doesn't say which of those is
    actually where the time goes. Labels line up 1:1 with the call sites below.
    `model` is the actual model name that call is about to hit (NARRATION_MODEL/
    STATE_UPDATE_MODEL, or an explicit override) - included in the log line so
    perf_dashboard.py can group latency by model, not just by call label, since
    NARRATION_MODEL/STATE_UPDATE_MODEL are now freely swapped via .env for testing.
    Also writes a status beacon (STATUS_LABELS[label]) before running fn(), if
    take_turn/regenerate_last_turn set one up for this thread - see _status_ctx above."""
    ctx = getattr(_status_ctx, "user_id", None)
    if ctx is not None:
        state_store.write_turn_status(_status_ctx.user_id, _status_ctx.story_slug, STATUS_LABELS.get(label, label))
    start = time.monotonic()
    try:
        return fn()
    finally:
        print(f"[TIMING] {label} model={model}: {time.monotonic() - start:.2f}s")


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
    stats = state["player"].get("stats", {})
    stats_block = f'\nCURRENT STATS ({", ".join(stats)}): {json.dumps(stats)}' if stats else ""

    schema_fields = [
        '  "subplot_progress": {"<subplot_id>": <integer 0-100, progress made this turn>}',
        '  "flags_set": {"<flag_name>": {"value": true, "pinned": <true if this is a '
        'foundational fact that should never be forgotten, e.g. a core revelation or '
        "identity; false if it's situational and safe to eventually forget once it's no "
        'longer recent>}}',
        '  "memory_fragments_revealed": ["<fragment_id>", "..."]',
        '  "entity_interaction": <true if the Architect appeared or acted this turn, else false>',
        '  "items_gained": ["<short item description>", "..."]',
        '  "items_lost": ["<item description, matching an existing inventory entry exactly>", "..."]',
        '  "relationship_changes": {"<character name>": <integer delta this turn, typically '
        "-10 to +10, positive for trust/warmth built, negative for damage done - only named "
        'characters the player actually interacted with or was meaningfully affected by this turn>}',
    ]
    if stats:
        schema_fields.append(
            f'  "stat_changes": {{"<stat name, must be one of: {", ".join(stats)}>": <integer '
            "delta this turn, positive or negative - only stats the turn's events actually "
            "moved, never a stat name outside that fixed list>}"
        )
    schema_str = ",\n".join(schema_fields)

    prompt = f"""Given this turn of an interactive story, report what changed in the world state.

ACTIVE SUBPLOTS: {json.dumps(active_subplots)}
UNREVEALED MEMORY FRAGMENT TRIGGERS: {json.dumps(unrevealed_fragments)}
CURRENT FLAGS: {json.dumps(state["player"]["flags_active"])}
CURRENT INVENTORY: {json.dumps(state["player"]["inventory"])}
CURRENT RELATIONSHIPS (name: score from -100 hostile to +100 devoted, 0 neutral/unknown): {json.dumps(relationships)}{stats_block}

PLAYER ACTION: {player_action}
NARRATION: {ai_response}

Respond with ONLY a JSON object, no other text, in this exact shape:
{{
{schema_str}
}}
Only include subplot ids, flags, fragment ids, items, character names, and stats that actually
changed this turn. Use {{}}/[] for nothing changed."""

    try:
        diff = _timed("state_update", lambda: call_llm_json(prompt), model=STATE_UPDATE_MODEL)
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

    # Only ever adjusts a stat that's already in player.stats (seeded once, at character
    # creation, from the chosen class's starting_stats - see apply_class_selection) - the
    # model can't introduce a new stat axis outside that fixed, story-authored set. No fixed
    # ceiling (see STAT_FLOOR's comment - stories define their own effective scale).
    for stat_name, delta in diff.get("stat_changes", {}).items():
        if stat_name in stats:
            stats[stat_name] = max(STAT_FLOOR, stats[stat_name] + int(delta))

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
        generated = _timed("subplot_generation", lambda: call_llm_json(prompt), model=STATE_UPDATE_MODEL)
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
        generated = _timed("end_story_final_arc", lambda: call_llm_json(prompt), model=STATE_UPDATE_MODEL)
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
        verdict = _timed("act_advancement_check", lambda: call_llm_json(prompt), model=STATE_UPDATE_MODEL)
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

    # One "| Label: chosen option name" per completed character-creation step (see
    # apply_creation_choice) - built generically off whatever steps the story authored in
    # character_creation, so a new step type (race, background, whatever a future story
    # wants) needs no changes here. Empty string for a story that doesn't use the mechanic
    # at all, so this adds nothing rather than showing empty labels for every story.
    creation_str = ""
    for step in state.get("character_creation", []):
        option_id = player.get("creation_choices", {}).get(step["key"])
        option = next((o for o in step["options"] if o["id"] == option_id), None)
        if option:
            creation_str += f" | {step.get('label', step['key'].title())}: {option['name']}"
    # Internal-only: stats exist for you to reason about and adjust, never to be shown to
    # the player as numbers - reflect their effect narratively (strain, confidence, risk)
    # instead of stating a value. Conditional on the story actually using stats at all, so
    # a story without them gets no irrelevant instruction clutter.
    stats_str = f" | Stats (opaque to the player): {player['stats']}" if player.get("stats") else ""
    stats_instruction = (
        "\nThe PLAYER line's Stats are for your own internal reasoning only - never state a "
        "stat's raw numeric value to the player. Reflect what it means narratively instead "
        "(strain, fatigue, confidence, risk) without quoting the number."
        if player.get("stats") else ""
    )

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
            "distinct, and plausible. Each option must be a meaningfully different course of "
            "action with real consequences for the story - never an option that just asks for "
            "more detail, investigates further before committing to anything, or otherwise "
            "stalls for more exposition instead of moving the scene forward. No extra "
            "commentary after the list."
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
PLAYER: {player['name']}{creation_str} | Traits: {', '.join(player['traits'])}{stats_str} | Inventory: {', '.join(player['inventory']) or 'nothing'} | Relationships: {player.get('relationships', {})} | Flags: {player['flags_active']}

Stay strictly within the established world, tone, and rules above.
Pace scenes like fast-moving genre fiction, not literary atmosphere-writing:
- Open on action or sensation already in motion, never on scene-setting.
- Let dialogue carry most of the scene. Characters should talk and react to each other
  far more than the narration describes what's around them - if a scene has other
  characters in it, most of its words should be spent on what gets said and done between
  people, not on the room they're standing in.
- When something does need describing, fold it into a single clause inside a sentence of
  action or dialogue, not a standalone descriptive paragraph. If you catch yourself
  writing two or more consecutive sentences that are pure description with no character
  doing or saying anything, cut it down to one folded-in detail instead.
- Keep interior thoughts to a brief, sharp aside - a clause or one short sentence - never
  an extended paragraph of introspection.
- Describe physical action the way it actually happens: quick, concrete, verb-driven
  (what a character's hands/body/voice do), not lingered on or narrated in slow motion.
- Prefer short paragraphs (1-3 sentences) that alternate briskly between narration and
  dialogue over long unbroken blocks of either.{stats_instruction}
You may lightly mark up emphasis in your prose using exactly these three markers, used
sparingly (most sentences should have none): **text** for bold, *text* for italic
(e.g. internal thought or stressed words), __text__ for underline. Do not nest them,
and do not use any other markdown (no headers, lists, links, code, or single/double
underscores for anything other than underline).
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
        updated_summary = _timed(
            "summary_rollover",
            lambda: call_llm(summary_prompt, model=STATE_UPDATE_MODEL, provider=STATE_UPDATE_PROVIDER),
            model=STATE_UPDATE_MODEL,
        )
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


def next_pending_creation_step(state: dict) -> dict:
    """The first character-creation step (from the story's template-authored
    character_creation list - see apply_creation_choice) the player hasn't completed yet,
    or None once they're all done (or the story doesn't define any). A story opts into
    this mechanic entirely by authoring that list - stories/new_babel/template.json has
    a "class" step and a "starting_place" step, in that order; a story that doesn't
    define character_creation at all (e.g. the cozy-mystery example story, where a class/
    race pick wouldn't fit the genre) skips this entirely, same as before the mechanic
    existed. Shared by the CLI loop below and app.py's play() route so both walk the same
    steps in the same order without duplicating the "what's next" logic."""
    choices = state["player"].get("creation_choices", {})
    for step in state.get("character_creation", []):
        if step["key"] not in choices:
            return step
    return None


def apply_creation_choice(state: dict, step_key: str, option_id: str) -> dict:
    """Pure state mutation, no I/O: applies one character-creation pick (step_key must
    match a step in state["character_creation"], e.g. "class" or "starting_place").
    Records player.creation_choices[step_key] = option_id, and merges the chosen option's
    starting_stats (if it has any - a flavor-only step like "starting_place" typically
    doesn't) into player.stats. Later steps merge on top of earlier ones for any stat name
    both happen to touch, in step order. Returns the chosen option dict, or None for an
    unrecognized step_key/option_id (a malformed/replayed POST) - callers must check for
    that rather than assuming the pick always succeeds."""
    steps = {s["key"]: s for s in state.get("character_creation", [])}
    step = steps.get(step_key)
    if not step:
        return None
    options = {o["id"]: o for o in step["options"]}
    chosen = options.get(option_id)
    if not chosen:
        return None
    state["player"].setdefault("creation_choices", {})[step_key] = option_id
    if chosen.get("starting_stats"):
        state["player"].setdefault("stats", {}).update(chosen["starting_stats"])
    return chosen


def run_opening_scene(user_id: str = DEFAULT_USER_ID, story_slug: str = DEFAULT_STORY_SLUG):
    """Plays the fixed, hand-authored opening (plot.opening_scene in the story's
    template) - the one constant beat every playthrough starts from, everything after
    branches from here. Captures the protagonist's name diegetically, in-fiction, as
    part of the intake scene itself, rather than a raw pre-game prompt, then walks
    whatever character-creation steps the story defines (see next_pending_creation_step),
    in order. Each part no-ops independently once already done, so resuming a save
    doesn't replay any of it."""
    state = state_store.load_state(user_id, story_slug)

    if not state["plot"]["opening_scene"]["played"]:
        print(state["plot"]["opening_scene"]["narration_before_name"])
        raw_name = input("\n> ")
        after_name = apply_opening_name(state, raw_name)
        print(f"\n{after_name}")
        state_store.save_state(state, user_id, story_slug)

    step = next_pending_creation_step(state)
    while step:
        print(f"\n{step.get('label', step['key'].title())}:")
        options = step["options"]
        for i, opt in enumerate(options, 1):
            tagline = f" - {opt['tagline']}" if opt.get("tagline") else ""
            print(f"{i}. {opt['name']}{tagline}")
        while True:
            choice = input("\n> ").strip()
            if choice.isdigit() and 1 <= int(choice) <= len(options):
                chosen = apply_creation_choice(state, step["key"], options[int(choice) - 1]["id"])
                print(f"\n{step.get('label', step['key'].title())}: {chosen['name']}.")
                break
            print(f"Please enter a number from 1 to {len(options)}.")
        state_store.save_state(state, user_id, story_slug)
        step = next_pending_creation_step(state)


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
    ai_response = _timed("narration", lambda: call_llm(prompt), model=NARRATION_MODEL)
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
    _status_ctx.user_id, _status_ctx.story_slug = user_id, story_slug
    try:
        state = state_store.load_state(user_id, story_slug)

        if is_end_story_command(player_action) and not state["plot"]["endgame"]["requested"]:
            final_arc = handle_end_story_request(state)
            state_store.save_state(state, user_id, story_slug)
            print(f"\n[The story is moving toward its conclusion: {final_arc['title']}]\n")

        pre_turn_snapshot = copy.deepcopy(state)
        pre_turn_snapshot["history_log"].pop("pending_regenerate", None)
        return _generate_and_apply_turn(state, player_action, pre_turn_snapshot, user_id, story_slug)
    finally:
        _status_ctx.user_id = None
        state_store.clear_turn_status(user_id, story_slug)


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
    _status_ctx.user_id, _status_ctx.story_slug = user_id, story_slug
    try:
        state = state_store.load_state(user_id, story_slug)
        pending = state["history_log"].get("pending_regenerate")
        if not pending:
            return False

        restored_state = pending["state"]
        player_action = pending["player_action"]
        pre_turn_snapshot = copy.deepcopy(restored_state)
        return _generate_and_apply_turn(restored_state, player_action, pre_turn_snapshot, user_id, story_slug)
    finally:
        _status_ctx.user_id = None
        state_store.clear_turn_status(user_id, story_slug)


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

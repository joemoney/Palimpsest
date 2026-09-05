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

load_dotenv()

import google.generativeai as genai
import requests

import state_store
from state_store import DEFAULT_STORY_SLUG, DEFAULT_USER_ID

RECENT_TURN_LIMIT = 10
SUMMARY_MAX_WORDS = 2000
SUBPLOT_TITLE_HISTORY_LIMIT = 15
FLAGS_ACTIVE_LIMIT = 25
RELATIONSHIPS_LIMIT = 20
# CR-03: revealed memory fragments accumulate for the whole game, same shape of problem as
# SUBPLOT_TITLE_HISTORY_LIMIT - bound how many of them reach the narration prompt, keyed off
# revealed_turn so the most recently revealed ones are the ones that survive the cap.
MEMORY_FRAGMENT_PROMPT_LIMIT = 12
# The completion_threshold a "multi_act"-span subplot gets instead of the normal 100 (see
# insert_subplot) - the only lever that actually makes one take longer to resolve, since
# progress/completion tracking (update_progress_from_turn, check_subplot_status) is generic
# over whatever threshold a subplot has. Not story-authored/tunable per-story like
# nudge_frequency - this is a mechanical pacing constant, not narrative content.
MULTI_ACT_SUBPLOT_THRESHOLD = 250
# Fallback used only for a story that predates the act_check_frequency field (see
# check_and_advance_act) - every current template authors its own value, same convention as
# nudge_frequency.
DEFAULT_ACT_CHECK_FREQUENCY = 12
# Fallback floor for a story that doesn't author mechanics.stats.floor at all (see 5.2's
# use in update_progress_from_turn) - mechanics.stats.floor/.ceiling is the real per-story
# dial now; this is just what a minimal template without one degrades to.
STAT_FLOOR = 0
# Fallback scene length for a story that omits narration.scene_length entirely (P-4: a
# minimal template must still run).
DEFAULT_SCENE_WORD_MIN = 470
DEFAULT_SCENE_WORD_MAX = 500
# OpenRouter's "provider": {"sort": "throughput"} (see _call_llm_openrouter) can route a
# request to whichever backing provider is fastest for the model, and that provider's own
# default max_tokens isn't something this app controls or can rely on - one observed in
# production truncated a narration reply mid-sentence, well short of the ~500-word scene
# plus its trailing OPTIONS block (~150 more words), with no OPTIONS block at all surviving.
# Set generously above the largest real payload (narration + options, or a state-update JSON
# blob) so a low provider default never becomes the binding constraint.
OPENROUTER_MAX_TOKENS = 4096
END_STORY_PHRASES = {"end story", "end the story", "conclude the story", "wrap up the story"}
STEER_WARNING = (
    "*** STEERING MODE: this rewrites the plot directly, bypassing narration.\n"
    "    It can easily contradict what's already happened or break story coherence\n"
    "    if the command isn't well thought out. Use plot_manager.py's commands\n"
    "    ('overview', 'add-act', 'pivot', 'add-emergent', 'promote-emergent',\n"
    "    'add-goal', 'add-theme', 'seed', 'seed-apply',\n"
    "    'seed-discard', 'seed-list', 'list-unlinked', 'promote-relationship'). ***"
)

# --- LLM tier configuration ---
# Three tiers, matched to what each call site actually needs (see CLAUDE.md's "Backend /
# Model Notes" for the full picture and the reasoning behind each choice):
#   Tier A - cheap flagship, reasoning OFF. For calls where style/format adherence matters
#     most and a model's reasoning phase swallowing the final answer (see the "reasoning"
#     comment in _call_llm_openrouter) would be a visible, player-facing failure: narration
#     (_generate_and_apply_turn's call_llm), the compressed_summary rollover, and
#     handle_end_story_request's closing arc.
#   Tier B - the SAME cheap flagship model as Tier A, but with reasoning turned ON. For
#     rarer, judgment-heavy calls where a bit of latency/failure risk is worth it for a
#     better decision: check_and_advance_act, generate_new_subplot, generate_steering_seed,
#     generate_character_from_relationship.
#   Tier C - fastest available model. Used only for update_progress_from_turn: a
#     closed-vocabulary classification/diff extraction that runs every single turn, where
#     speed and cost matter far more than reasoning depth.
# Tier A and Tier B are therefore the SAME provider/model pair (TIER_AB_PROVIDER/
# TIER_AB_MODEL) - callers distinguish the two only via call_llm's/call_llm_json's
# reasoning= flag. Tier C gets its own, independent pair. Google/Gemini is deliberately NOT
# a real tier choice here - it's reserved for the offline test suite (TESTING_FORCE_GOOGLE
# below) and call_llm's own fail-safe retry; both TIER_AB_PROVIDER and TIER_C_PROVIDER
# default to "openrouter".
TIER_AB_PROVIDER = os.getenv("TIER_AB_PROVIDER", "openrouter")
TIER_AB_MODEL = os.getenv("TIER_AB_MODEL", "deepseek/deepseek-v4-pro-20260813")
TIER_C_PROVIDER = os.getenv("TIER_C_PROVIDER", "openrouter")
TIER_C_MODEL = os.getenv("TIER_C_MODEL", "deepseek/deepseek-v4-flash-0731")
for _provider in (TIER_AB_PROVIDER, TIER_C_PROVIDER):
    if _provider not in ("openrouter", "google"):
        raise ValueError(f"Unknown provider {_provider!r} - expected 'openrouter' or 'google'")

# Whole-process testing/debug override - NOT a real tier setting. When true, every call,
# regardless of tier or whatever provider/model it was given, is forced through a direct
# Gemini call using GEMINI_MODEL. This is what test/_llm_stubs.py sets so the offline suite
# can exercise story_engine against a stubbed google.generativeai SDK without needing a fake
# OPENROUTER_API_KEY plus a requests.post stub in every test file that merely imports this
# module - see call_llm's docstring.
TESTING_FORCE_GOOGLE = os.getenv("TESTING_FORCE_GOOGLE", "").strip().lower() in ("1", "true", "yes")

# Deployment-level kill switch for call_llm's Gemini fail-safe (see its docstring). Some
# operator environments can't reach the Gemini API at all - observed in production as every
# fail-safe attempt raising google.api_core.exceptions.FailedPrecondition ("400 User location
# is not supported for the API use") regardless of API key, meaning the fail-safe currently
# only adds a guaranteed-to-fail extra round trip (and its own latency) after a primary
# failure, before the same error surfaces anyway. Defaults to enabled - this only matters for
# a deployment that's confirmed Gemini access is blocked for it.
GEMINI_FAILSAFE_ENABLED = os.getenv("GEMINI_FAILSAFE_ENABLED", "true").strip().lower() not in (
    "0", "false", "no",
)

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.7-flash")

# Skipped entirely under TESTING_FORCE_GOOGLE - every call is routed to Gemini regardless
# of TIER_AB_PROVIDER/TIER_C_PROVIDER's configured values in that mode (see call_llm), so
# OpenRouter is never actually reached and requiring its key would needlessly break the
# offline test suite, which stubs only the Google SDK.
if not TESTING_FORCE_GOOGLE and "openrouter" in (TIER_AB_PROVIDER, TIER_C_PROVIDER) and not OPENROUTER_API_KEY:
    raise ValueError("OPENROUTER_API_KEY not found in .env file")
# Required unconditionally, not just when a tier's provider is "google" - the Gemini
# fail-safe (see call_llm) can fire regardless of which provider is primary, and
# TESTING_FORCE_GOOGLE routes every call through Gemini too, so an all-openrouter deployment
# still needs a working Gemini client configured for it to have anywhere to fall back to.
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
# caught here first, cleanly, instead of by gunicorn's SIGABRT. (app.py's take_turn/
# regenerate_turn now run this whole call chain on a background thread rather than inline
# in the request - see _start_turn_job - so gunicorn's own --timeout no longer has a
# request to measure this against at all; these constants stay in place regardless, since
# they're still what makes an individual call give up and hand off to the fail-safe rather
# than hanging indefinitely.)
OPENROUTER_TOTAL_TIMEOUT = 100
GOOGLE_TOTAL_TIMEOUT = 60
_openrouter_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=8, thread_name_prefix="openrouter-call"
)
_google_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=8, thread_name_prefix="google-call"
)


_SENTENCE_END_RE = re.compile(r'[.!?][\'"”]?(?:\s|$)')


def _trim_to_last_sentence(text: str) -> str:
    """Cuts text back to its last complete sentence, for salvaging a reply truncated
    mid-sentence by finish_reason "length" (see _call_llm_openrouter) rather than leaving
    a dangling half-sentence for the player to read or for generate_missing_options to
    react to. Falls back to the untrimmed text if no sentence boundary is found at all
    (e.g. a reply cut off before finishing its very first sentence)."""
    last_end = None
    for m in _SENTENCE_END_RE.finditer(text):
        last_end = m.end()
    return text[:last_end].rstrip() if last_end else text


def _call_llm_openrouter(prompt: str, model: str, reasoning: bool = False, json_mode: bool = False) -> str:
    def do_request():
        body = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            # deepseek-v4-flash-0731 (TIER_C_MODEL) alone is resold through 29 different
            # OpenRouter providers, with measured throughput ranging 6-109 tok/s and TTFT
            # 0.42-2.42s depending which one a request lands on - OpenRouter's default
            # routing doesn't optimize for this, so a real production call landed on the
            # slow end (see git log: an 83s state-update call was the dominant cost in a
            # 132s turn). This asks OpenRouter to prefer whichever provider is currently
            # fastest for the requested model, instead of leaving that to chance - same
            # model, same price, just routed better. Applies to every OpenRouter call
            # (every tier), since it can only help.
            "provider": {"sort": "throughput"},
            # A reasoning-capable model (observed with deepseek-v4-pro) can finish
            # normally (finish_reason "stop") while leaving message.content null and
            # putting the entire finished reply - including a correctly-formatted OPTIONS
            # block - in message.reasoning instead, because nothing here told it to ever
            # close its reasoning phase. This used to send {"exclude": not reasoning} for
            # reasoning=False (Tier A, Tier C's default), on the assumption that excluding
            # reasoning from the response also forced the model to land its answer in
            # content - a real production call disproved that: exclude:true only hides
            # reasoning from the response, it doesn't stop the model spending its
            # max_tokens budget generating it, and a call landed finish_reason "length"
            # with content null and reasoning_tokens alone using the entire budget.
            # {"enabled": false} is OpenRouter's actual "turn reasoning off" switch (a
            # no-op for a model without reasoning support, and providers with genuinely
            # mandatory reasoning - none observed among this project's configured models -
            # would 400 on it, which surfaces as LLMUnavailableError same as any other
            # failure). reasoning=True (Tier B) still sends exclude:false, deliberately
            # accepting the truncation risk in exchange for the model actually reasoning
            # before it answers - judgment-heavy Tier B calls are rare and cheap to retry
            # (the empty-content and finish_reason guards below still catch a bad response
            # and hand it to call_llm's Gemini fail-safe, same as any other failure).
            "reasoning": {"enabled": False} if not reasoning else {"exclude": False},
            "max_tokens": OPENROUTER_MAX_TOKENS,
        }
        if json_mode:
            # Every call_llm_json call requests this (see its docstring), regardless of
            # tier - OpenRouter's guaranteed-valid-JSON mode, cheap insurance against the
            # model wrapping its answer in prose or breaking JSON syntax.
            body["response_format"] = {"type": "json_object"}
        response = requests.post(
            OPENROUTER_URL,
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
            json=body,
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
        choice = data["choices"][0]
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise LLMUnavailableError(f"Unexpected OpenRouter response shape: {data}") from e
    # A reply cut off by OPENROUTER_MAX_TOKENS (finish_reason "length") is genuinely unusable
    # for call_llm_json (json_mode=True) - truncated JSON won't parse, so that case still goes
    # through the Gemini fail-safe/retry path like any other LLMUnavailableError. For plain
    # narration (json_mode=False), a runaway completion (observed in production: the model
    # narrated several scenes' worth of content, ~6x the instructed word count, before hitting
    # the cap) doesn't need the whole turn to fail - it just never reached its OPTIONS block,
    # which is exactly what generate_missing_options's existing follow-up call already handles
    # for a reply that omits OPTIONS for any other reason. Trimming to the last complete
    # sentence here (instead of raising) lets that same downstream path salvage a clean,
    # if shorter-than-intended, scene deterministically - regardless of max_tokens - rather
    # than gambling on a larger cap being big enough for the next runaway.
    if choice.get("finish_reason") == "length":
        if json_mode:
            raise LLMUnavailableError(f"OpenRouter truncated response (finish_reason=length): {data}")
        content = _trim_to_last_sentence(content)
    # Some models (observed with deepseek-v4-pro) can return a 200 with
    # message.content null/empty - e.g. the reply landed entirely in a
    # "reasoning" field, or the model stopped before producing output. That's
    # not a valid narration, so treat it the same as a request-level failure
    # rather than silently handing None back up to call_llm - which would
    # otherwise skip the Gemini fail-safe and let "None" get saved as the
    # scene text (see git history for a real incident this caused).
    if not content:
        raise LLMUnavailableError(f"OpenRouter returned empty content: {data}")
    return content


def _call_llm_google(prompt: str, model: str) -> str:
    # Imported lazily so importing story_engine doesn't require the real google-api-core
    # package when every tier defaults to openrouter - the offline test suite stubs
    # google.generativeai but not this transitive dependency, and sets
    # TESTING_FORCE_GOOGLE specifically to exercise this path against the stub.
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
    # Same empty-response guard as _call_llm_openrouter - e.g. a prompt-blocked
    # response has no candidates and response.text raises/returns nothing usable.
    if not response.text:
        raise LLMUnavailableError(f"Gemini returned empty content: {response!r}")
    return response.text


def call_llm(
    prompt: str,
    model: str = TIER_AB_MODEL,
    provider: str = None,
    reasoning: bool = False,
    json_mode: bool = False,
) -> str:
    """Sends prompt to the given (or default) provider and returns the raw text response.
    Defaults to Tier A (TIER_AB_MODEL/TIER_AB_PROVIDER, reasoning off) - narration
    (_generate_and_apply_turn) and the compressed_summary rollover call it exactly this way;
    handle_end_story_request also lands on Tier A, but through call_llm_json. Tier B call
    sites (generate_new_subplot, check_and_advance_act, generate_steering_seed,
    generate_character_from_relationship) pass model=TIER_AB_MODEL,
    provider=TIER_AB_PROVIDER, reasoning=True explicitly instead - see "LLM tier
    configuration" above for which call site is which tier.

    Under TESTING_FORCE_GOOGLE (the offline test suite's whole-process override), provider
    and model are both replaced with "google"/GEMINI_MODEL regardless of what was passed in
    - every tier's real default model name is invalid for the other provider (an OpenRouter
    slug vs. a real Gemini name), so respecting them here would break that path. Outside of
    that override, provider/model are always respected as given.

    Fail-safe: if the primary call raises LLMUnavailableError, this retries once against
    the operator's own free-tier Gemini model (GEMINI_MODEL) via a direct Google API call,
    before giving up - lets TIER_AB_MODEL/TIER_C_MODEL be freely swapped to whatever's being
    tried (e.g. an experimental OpenRouter model) without an unreachable or misconfigured
    model taking the whole app down. This IS a genuine runtime fallback (unlike
    TIER_AB_PROVIDER/TIER_C_PROVIDER's fixed per-tier provider selection, which still isn't
    one) - deliberately narrow in scope: it only ever falls back TO Gemini, never away from
    it, and only on a request-level failure, never a silent retry on output that merely
    looks wrong (e.g. malformed JSON - call_llm_json's caller decides what to do with that,
    same as before). Compares against the model actually attempted (already reassigned to
    GEMINI_MODEL under TESTING_FORCE_GOOGLE above), not some separate raw argument, so this
    doesn't uselessly retry the exact same Gemini call a second time when the testing
    override already substituted GEMINI_MODEL in for a non-Gemini model argument."""
    provider = provider or TIER_AB_PROVIDER
    if TESTING_FORCE_GOOGLE:
        provider, model = "google", GEMINI_MODEL
    try:
        if provider == "google":
            return _call_llm_google(prompt, model)
        return _call_llm_openrouter(prompt, model, reasoning=reasoning, json_mode=json_mode)
    except LLMUnavailableError as primary_error:
        if provider == "google" and model == GEMINI_MODEL:
            raise  # this WAS the fail-safe call - nothing left to fall back to
        if not GEMINI_FAILSAFE_ENABLED:
            raise
        print(f"[FAILSAFE] primary call failed (provider={provider!r} model={model!r}): "
              f"{primary_error} - retrying via Gemini fail-safe ({GEMINI_MODEL})")
        return _call_llm_google(prompt, GEMINI_MODEL)


def call_llm_json(
    prompt: str,
    model: str = TIER_C_MODEL,
    provider: str = None,
    reasoning: bool = False,
) -> dict:
    """Call the LLM expecting a single JSON object back, tolerating markdown code fences.
    Defaults to Tier C (TIER_C_MODEL/TIER_C_PROVIDER) - update_progress_from_turn is the
    only call site that calls this bare, every turn. Every Tier B call site
    (generate_new_subplot, check_and_advance_act, generate_steering_seed,
    generate_character_from_relationship) passes model=TIER_AB_MODEL,
    provider=TIER_AB_PROVIDER, reasoning=True explicitly; handle_end_story_request (Tier A)
    passes the same model/provider with reasoning left at its default False. Always requests
    OpenRouter's response_format: json_object mode underneath (see _call_llm_openrouter) - a
    no-op under the google provider, which has no equivalent knob in this codebase."""
    provider = provider or TIER_C_PROVIDER
    raw = call_llm(prompt, model=model, provider=provider, reasoning=reasoning, json_mode=True).strip()
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
    # Written directly by app.py, before it even hands the turn off to a background
    # thread - not one of the _timed() labels below, so it has no DEFAULT_STEP_ESTIMATE_
    # SECONDS entry either (falls back to no progress bar, just the spinner, which suits a
    # step that's normally over in well under a second).
    "queued": "Starting",
    "narration": "Narrating",
    "options_generation": "Offering",
    "state_update": "Reckoning",
    "subplot_generation": "Branching",
    "act_advancement_check": "Weighing",
    "end_story_final_arc": "Concluding",
    "summary_rollover": "Remembering",
    # The last two _timed() labels are the ones that can't run inside a turn at all -
    # generate_steering_seed and generate_character_from_relationship are only ever reached
    # from plot_manager.py (its CLI, or app.py's Plot Manager routes calling those functions
    # directly on the request thread), where _status_ctx was never set, so no beacon is
    # written and the busy popup never sees them. They're mapped anyway so that this dict
    # stays a complete mirror of the _timed() call sites - the thing that actually goes
    # stale - and so a future change that does run one under a status context degrades to a
    # display word rather than a raw snake_case key leaking into the popup. Like "queued",
    # they deliberately have no DEFAULT_STEP_ESTIMATE_SECONDS entry.
    "steering_seed_generation": "Charting",
    "relationship_promotion": "Naming",
}

# Seed estimate (seconds) for the busy indicator's per-step progress bar before
# state_store.p50_duration has any real samples for a label - a brand-new deploy (or a
# freshly cleared data/perf_stats.json) would otherwise show no progress bar at all for
# every player's first several turns, since p50_duration returns None with zero samples.
# app.py's /api/status route only falls back to this when p50_duration is None - one real
# completed call is enough for the rolling median to take over from then on.
DEFAULT_STEP_ESTIMATE_SECONDS = {
    "narration": 17,
    # Same tier/model as narration but a much smaller ask (just the OPTIONS block, against
    # narration it's already been handed), so a fraction of narration's estimate.
    "options_generation": 8,
    "state_update": 23,
    "subplot_generation": 6,
    "act_advancement_check": 4,
    "end_story_final_arc": 15,
    "summary_rollover": 19,
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
    `docker logs`/gunicorn's access log, same as the existing narration print). A turn
    chains several sequential calls - narration, the state-update pass, and conditionally
    the missing-OPTIONS repair follow-up, subplot generation (once per subplot that
    completed this turn, so possibly more than once), act-advancement judgment, the summary
    rollover, and, on the turn an end-story command lands, the closing-arc call - so total
    request duration alone (the access log's one number) doesn't say which of those is
    actually where the time goes. Labels line up 1:1 with the call sites below.
    `model` is the actual model name that call is about to hit (TIER_AB_MODEL/
    TIER_C_MODEL, or an explicit override) - included in the log line so
    perf_dashboard.py can group latency by model, not just by call label, since
    TIER_AB_MODEL/TIER_C_MODEL are now freely swapped via .env for testing.
    Also writes a status beacon (the raw label - app.py maps it through STATUS_LABELS for
    display) before running fn(), if take_turn/regenerate_last_turn set one up for this
    thread - see _status_ctx above. After fn() returns, records the elapsed duration into
    state_store's rolling per-label stats (state_store.record_call_duration), which feeds
    the busy indicator's per-step progress-bar estimate (p50_duration) on the next turn -
    deliberately separate from perf_dashboard.py's docker-logs-based durable record, see
    that file's docstring for why this process never reads docker logs itself."""
    ctx = getattr(_status_ctx, "user_id", None)
    if ctx is not None:
        state_store.write_turn_status(_status_ctx.user_id, _status_ctx.story_slug, label)
    start = time.monotonic()
    try:
        return fn()
    finally:
        elapsed = time.monotonic() - start
        print(f"[TIMING] {label} model={model}: {elapsed:.2f}s")
        state_store.record_call_duration(label, elapsed)


# ---------------------------------------------------------------------------
# Merge-view helpers: everything above the line reads/writes only ctx["state"]
# (runtime); everything below resolves a combined view across ctx["story"]
# (authored, frozen) and ctx["state"] where the two need to be seen together -
# acts, subplots, and the character roster. See docs/SCHEMA_V2_SPEC.md §2-4.
# ---------------------------------------------------------------------------

def _main_thread_view(ctx: dict) -> dict:
    """Merged view of the main thread's title/description: plot_manager.pivot_main_plot
    used to mutate main_thread.title/description directly, which the story/state split no
    longer allows (main_thread is authored, frozen content). A pivot now writes a runtime
    override (ctx["state"]["plot"]["main_thread_override"]) that takes precedence here when
    present, leaving the template's original title/description as the unpivoted fallback.
    plot_notes has no override - a pivot has never touched it, only title/description."""
    story_mt = ctx["story"]["plot"]["main_thread"]
    override = ctx["state"]["plot"].get("main_thread_override")
    return {
        "title": override["title"] if override else story_mt["title"],
        "description": override["description"] if override else story_mt["description"],
        "plot_notes": story_mt.get("plot_notes", ""),
    }


def _current_act(ctx: dict) -> dict:
    """The main thread's currently-active act, looked up by act_number (CR-17) across the
    merged act list (template Act 1 + any generated_acts), rather than list position -
    list position breaks as soon as act numbering stops being contiguous. Returns None if
    current_act doesn't match any act (shouldn't happen in practice - callers that can't
    tolerate that already guard)."""
    number = ctx["state"]["plot"]["current_act"]
    return next((act for act in _all_acts(ctx) if act["act_number"] == number), None)


def _all_acts(ctx: dict) -> list:
    """Every act - the template's authored Act 1 (annotated with its runtime completed/
    optional flags from act_completion, since the template entry itself is frozen) plus
    every act generated during play (already self-contained, carrying its own completed/
    optional directly) - sorted by act_number."""
    completion = ctx["state"]["plot"]["act_completion"]
    merged = []
    for act in ctx["story"]["plot"]["main_thread"]["acts"]:
        overlay = completion.get(str(act["act_number"]), {})
        merged.append({
            "act_number": act["act_number"],
            "title": act["title"],
            "description": act["description"],
            "completion_signals": list(act.get("completion_signals", [])),
            "completed": overlay.get("completed", False),
            "optional": overlay.get("optional", False),
        })
    for act in ctx["state"]["plot"]["generated_acts"]:
        merged.append(dict(act))
    merged.sort(key=lambda a: a["act_number"])
    return merged


def _mark_act_completed(ctx: dict, act_number: int):
    """Marks one act completed, whichever half it lives in - the template's acts are
    frozen, so a template-authored act_number's completed flag lives in the
    act_completion overlay instead of being set on the act record itself."""
    authored_numbers = {a["act_number"] for a in ctx["story"]["plot"]["main_thread"]["acts"]}
    if act_number in authored_numbers:
        overlay = ctx["state"]["plot"]["act_completion"].setdefault(
            str(act_number), {"completed": False, "optional": False}
        )
        overlay["completed"] = True
        return
    for act in ctx["state"]["plot"]["generated_acts"]:
        if act["act_number"] == act_number:
            act["completed"] = True
            return


def _location_name(ctx: dict, location_id: str) -> str:
    """CR-02: resolves a world.locations id to its authored, human-readable name. Falls
    back to the raw id for one that isn't in the table - an older/ad-hoc location, or a
    free-text one CR-01's scene_update accepted because world.locations was empty for that
    story."""
    return ctx["story"]["world"].get("locations", {}).get(location_id, {}).get("name", location_id)


def _next_subplot_id(subplots: dict) -> str:
    existing_numbers = [
        int(sid.rsplit("_", 1)[-1])
        for sid in subplots
        if sid.rsplit("_", 1)[-1].isdigit()
    ]
    next_number = (max(existing_numbers) + 1) if existing_numbers else 1
    return f"subplot_{next_number:03d}"


def _subplot_view(ctx: dict, sid: str) -> dict:
    """Merged view of one subplot: a seeded subplot resolves title/description/priority/
    ties_to_main_plot/completion_threshold/span from the template; a generated one (no
    template counterpart) carries all of that on its own runtime entry instead, since
    there's nothing to resolve it against. If a seeded subplot's template entry has since
    been removed by an author (SCHEMA_V2_SPEC.md §2.3 reconciliation), falls back to a
    placeholder rather than raising - the runtime copy stays in place either way."""
    seed = ctx["story"]["plot"]["subplots"].get(sid, {})
    runtime = ctx["state"]["plot"]["subplots"].get(sid, {})
    return {
        "id": sid,
        "title": runtime.get("title") or seed.get("title") or f"(removed from template: {sid})",
        "description": runtime.get("description", seed.get("description", "")),
        "priority": runtime.get("priority", seed.get("priority", "medium")),
        "ties_to_main_plot": runtime.get("ties_to_main_plot", seed.get("ties_to_main_plot", "")),
        "completion_threshold": runtime.get("completion_threshold", seed.get("completion_threshold", 100)),
        "span": runtime.get("span", seed.get("span", "single_act")),
        "progress": runtime.get("progress", 0),
        "status": runtime.get("status", "not_started"),
        "active": runtime.get("active", False),
    }


def _all_subplots(ctx: dict) -> dict:
    """{id: merged view} for every subplot that currently exists - ctx["state"]["plot"]
    ["subplots"] is the authoritative id set (every template-seeded subplot is
    instantiated into it at save creation, and every generated one is added to it
    directly), so iterating its keys covers both kinds."""
    return {sid: _subplot_view(ctx, sid) for sid in ctx["state"]["plot"]["subplots"]}


def _authored_character(ctx: dict, name: str) -> dict:
    return ctx["story"]["world"].get("characters", {}).get(name)


def _character_record(ctx: dict, name: str) -> dict:
    """Merged view of one character: authored fields (description/role/
    relationship_to_player/hook) come from ctx["story"]["world"]["characters"] when the
    name is authored, otherwise from the runtime entry itself (a discovered character has
    nowhere else to keep them). Runtime fields (relationship score, first_seen_turn,
    introduced) always come from ctx["state"]["characters"], defaulting to "unmet" (no
    entry yet) when absent - CR-06's "a character with no relationship entry renders
    without a score, not as 0.\""""
    authored = _authored_character(ctx, name) or {}
    runtime = ctx["state"]["characters"].get(name) or {}
    return {
        "name": name,
        "description": authored.get("description") or runtime.get("description", ""),
        "role": authored.get("role") or runtime.get("role", ""),
        "relationship_to_player": authored.get("relationship_to_player") or runtime.get("relationship_to_player", ""),
        "hook": authored.get("hook") or runtime.get("hook", ""),
        "authored": bool(_authored_character(ctx, name)),
        "relationship": runtime.get("relationship") if name in ctx["state"]["characters"] else None,
        "first_seen_turn": runtime.get("first_seen_turn"),
        "introduced": bool(runtime.get("introduced")),
    }


def _all_character_names(ctx: dict) -> set:
    return set(ctx["story"]["world"].get("characters", {}).keys()) | set(ctx["state"]["characters"].keys())


def _existing_character_names(ctx: dict) -> list:
    """Every name that should count as "already exists, don't invent a duplicate" when
    prompting the model for new characters - the merged roster, plus the tracked entity's
    name if the story has one (mechanics.tracked_entity isn't part of the characters
    roster, but a model unaware of it could otherwise reinvent it as a new NPC)."""
    names = list(_all_character_names(ctx))
    tracked = ctx["story"].get("mechanics", {}).get("tracked_entity")
    if tracked:
        names.append(tracked["name"])
    return names


def insert_character(ctx: dict, name: str, description: str = "", role: str = "",
                       relationship_to_player: str = "", hook: str = "", introduced: bool = False,
                       origin: str = "seed", seed_note: str = None) -> str:
    """Shared by every path that creates or fleshes out a discovered NPC record -
    plot_manager.apply_steering_seed, generate_new_subplot/check_and_advance_act's proposed
    characters, update_progress_from_turn's newly-named characters, and the relationship->NPC
    promotion flow (plot_manager.promote_relationship_to_npc). Writes into
    ctx["state"]["characters"][name] - name is the only identifier a v2 character needs
    (see SCHEMA_V2_SPEC.md §3.4/§4: the template's world.characters and the save's
    characters are both keyed by the same canonical name, so no separate id/npc_id
    indirection is needed to link them). Returns `name` for symmetry with the old id-
    returning version, though every caller now just discards it or uses it as the key
    directly. `origin` records which path created/touched it, purely for later human
    review via show_plot_overview - nothing else reads it back."""
    characters = ctx["state"]["characters"]
    entry = characters.setdefault(name, {
        "relationship": 0, "first_seen_turn": ctx["state"]["pacing"]["turn_count"],
    })
    entry["introduced"] = introduced
    entry["origin"] = origin
    if description:
        entry["description"] = description
    if role:
        entry["role"] = role
    if relationship_to_player:
        entry["relationship_to_player"] = relationship_to_player
    if hook:
        entry["hook"] = hook
    if seed_note is not None:
        entry["seed_note"] = seed_note
    return name


def _maybe_insert_generated_character(ctx: dict, generated: dict, origin: str):
    """Shared by generate_new_subplot and check_and_advance_act: if the same LLM call that
    generated new subplot/act content also proposed a specific new named character to go with
    it, commit them as a real (discovered) character right away - not yet `introduced` (they
    haven't appeared on the page yet), so they surface via generate_pacing_nudge's "CHARACTERS
    TO WEAVE IN" line the same way a steering-seeded character already does."""
    draft = generated.get("new_character")
    if not isinstance(draft, dict) or not draft.get("name"):
        return
    if draft["name"] in _existing_character_names(ctx):
        return
    insert_character(
        ctx, draft["name"],
        description=draft.get("description", ""),
        role=draft.get("role", ""),
        relationship_to_player=draft.get("relationship_to_player", ""),
        hook=draft.get("hook", ""),
        introduced=False,
        origin=origin,
    )


def insert_subplot(ctx: dict, title: str, description: str, priority: str = "medium",
                     ties_to_main_plot: str = "", span: str = "single_act") -> str:
    """Shared by generate_new_subplot and plot_manager.apply_steering_seed: assigns the
    next subplot_NNN id, activates it if there's room under max_parallel_subplots, and
    inserts it into ctx["state"]["plot"]["subplots"] - fully self-contained, since anything
    reaching this function was invented at runtime and has no template counterpart to
    resolve against. span is the one field this function actually acts on: "multi_act" gets
    MULTI_ACT_SUBPLOT_THRESHOLD instead of the normal 100, which is the actual lever that
    makes it take meaningfully longer to resolve - update_progress_from_turn/
    check_subplot_status are already generic over whatever threshold a subplot has, so
    nothing else needs to change for a longer-running subplot to behave correctly."""
    subplots = ctx["state"]["plot"]["subplots"]
    new_id = _next_subplot_id(subplots)

    active_count = sum(1 for sid in subplots if _subplot_view(ctx, sid)["active"])
    max_parallel = ctx["story"]["plot"]["pacing"]["max_parallel_subplots"]
    make_active = active_count < max_parallel

    subplots[new_id] = {
        "progress": 0,
        "status": "active" if make_active else "not_started",
        "active": make_active,
        "title": title,
        "description": description,
        "priority": priority,
        "completion_threshold": MULTI_ACT_SUBPLOT_THRESHOLD if span == "multi_act" else 100,
        "ties_to_main_plot": ties_to_main_plot,
        "span": span,
    }
    return new_id


def check_subplot_status(ctx: dict) -> dict:
    """Check and update subplot completion status."""
    subplots = ctx["state"]["plot"]["subplots"]
    completed_this_check = []

    for sid in list(subplots):
        view = _subplot_view(ctx, sid)
        if view["active"] and view["progress"] >= view["completion_threshold"]:
            subplots[sid]["status"] = "completed"
            subplots[sid]["active"] = False
            completed_this_check.append(sid)

            completed_list = ctx["state"]["plot"]["completed_subplots"]
            if sid not in completed_list:
                completed_list.append(sid)
                ctx["state"]["pacing"]["subplots_completed_this_act"] += 1

    return {"completed": completed_this_check, "total_completed": len(ctx["state"]["plot"]["completed_subplots"])}


def update_progress_from_turn(ctx: dict, player_action: str, ai_response: str) -> dict:
    """Separate LLM pass (kept apart from narration) that extracts a state diff from the
    turn just narrated: subplot progress, flags, revealed memory fragments, entity contact,
    inventory changes, scene, and relationship-score changes."""
    subplots_view = _all_subplots(ctx)
    # CR-08: previously just {id: title}, giving the model a delta to report with no idea
    # where the subplot currently stands - it couldn't tell "this beat should finish the
    # thread" from "this nudges it." Progress/threshold let it calibrate the delta instead.
    active_subplot_lines = "\n".join(
        f"  {sid}: {sp['title']} - {sp['description']} [{sp['progress']}/{sp['completion_threshold']}]"
        for sid, sp in subplots_view.items() if sp["active"]
    ) or "  none"
    revelations = ctx["story"].get("mechanics", {}).get("revelations", [])
    revealed_ids = set(ctx["state"]["plot"]["revelations_revealed"].keys())
    unrevealed_fragments = {
        rev["id"]: rev["trigger"] for rev in revelations if rev["id"] not in revealed_ids
    }
    characters = ctx["state"]["characters"]
    # 5.3: mechanics.relationships.axis replaces the hardcoded "-100 hostile to +100
    # devoted" / "trust/warmth built" instruction text - interpolated into both the
    # CURRENT RELATIONSHIPS line and the relationship_changes schema field below. Absent
    # block means the story tracks no relationship scores at all: no CURRENT RELATIONSHIPS
    # line, no relationship_changes field. Character discovery (new_characters) is a
    # separate, unconditional mechanism - a story can track who's been met without scoring
    # how they feel about the player.
    relationships_cfg = ctx["story"].get("mechanics", {}).get("relationships")
    relationship_scores = {name: entry.get("relationship", 0) for name, entry in characters.items()}
    existing_characters = _existing_character_names(ctx)
    stats = ctx["state"]["protagonist"].get("stats", {})
    stats_block = f'\nCURRENT STATS ({", ".join(stats)}): {json.dumps(stats)}' if stats else ""

    # CR-01: plot.current_scene (now top-level ctx["state"]["scene"]) had no writer
    # anywhere in v1 - frozen at whatever the template seeded it to for the life of the
    # playthrough, an increasingly stale, authoritative-sounding claim contradicting
    # RECENT EXCHANGES by late-game. valid_locations empty means this story doesn't author
    # a location table at all, so scene_update.location is accepted as any free-text
    # string instead of validated against a fixed list.
    scene = ctx["state"]["scene"]
    valid_locations = list(ctx["story"]["world"].get("locations", {}).keys())
    locations_hint = (
        f"\nVALID LOCATION IDS (scene_update.location must be one of these, or the current one "
        f"if unchanged): {', '.join(valid_locations)}" if valid_locations else ""
    )

    # CR-07: "the Architect" used to be hardcoded here regardless of story, asking a cozy
    # mystery's state-update pass about an entity that doesn't exist in its template. Only
    # asked about when the story actually configures one.
    tracked_entity = ctx["story"].get("mechanics", {}).get("tracked_entity")

    # 5.7: mechanics.failure_conditions - a story that can end badly without the player
    # asking to. Evaluated alongside revelations (same authored-trigger shape, different
    # effect - see _apply_failure_condition). Not offered once the story is already ending,
    # from either cause - nothing left to fail into.
    failure_conditions = ctx["story"].get("mechanics", {}).get("failure_conditions", [])
    if ctx["state"]["plot"]["endgame"]["requested"]:
        failure_conditions = []
    failure_triggers = {c["id"]: c["trigger"] for c in failure_conditions}

    schema_fields = [
        '  "subplot_progress": {"<subplot_id>": <integer 0-100, progress made this turn - this '
        "is ADDED to the subplot's current progress shown above, and reaching its "
        'completion_threshold completes the thread>}',
        '  "flags_set": {"<flag_name>": {"value": true, "pinned": <true if this is a '
        'foundational fact that should never be forgotten, e.g. a core revelation or '
        "identity; false if it's situational and safe to eventually forget once it's no "
        'longer recent>}}',
        '  "memory_fragments_revealed": ["<the exact id of every UNREVEALED MEMORY FRAGMENT '
        'TRIGGER below that the narration satisfies this turn, or [] if none>"]',
    ]
    if tracked_entity:
        schema_fields.append(
            f'  "entity_interaction": <true if {tracked_entity["name"]} appeared or acted this '
            'turn, else false>'
        )
    schema_fields.append(
        '  "scene_update": {"location": "<location id from VALID LOCATION IDS above, or the '
        'same id if the protagonist has not moved>", "summary": "<1-2 sentences: where the '
        'protagonist is now and the immediate situation, as of the end of this turn>", '
        '"present_npcs": ["<character name>", "..."]}'
    )
    schema_fields += [
        '  "items_gained": ["<short item description>", "..."]',
        '  "items_lost": ["<item description, matching an existing inventory entry exactly>", "..."]',
    ]
    if relationships_cfg:
        axis = relationships_cfg["axis"]
        schema_fields.append(
            '  "relationship_changes": {"<character name>": <integer delta this turn, typically '
            f'-10 to +10, positive for {axis["description"]}, negative for damage done - only named '
            'characters the player actually interacted with or was meaningfully affected by this turn>}'
        )
    schema_fields.append(
        '  "new_characters": [{"name": "<full name>", "description": "<who they are, appearance, '
        'personality>", "role": "<their narrative role>", "relationship_to_player": "<their '
        'initial stance toward the player>", "hook": "<a concrete way they could naturally '
        'reappear or matter going forward>"}]'
    )
    if stats:
        schema_fields.append(
            f'  "stat_changes": {{"<stat name, must be one of: {", ".join(stats)}>": <integer '
            "delta this turn, positive or negative - only stats the turn's events actually "
            "moved, never a stat name outside that fixed list>}"
        )
    if failure_conditions:
        schema_fields.append(
            '  "failure_triggered": "<the exact id of a FAILURE CONDITION below that has now '
            'been met this turn, or null if none have>"'
        )
    schema_str = ",\n".join(schema_fields)
    relationships_line = ""
    exact_name_instruction = ""
    generic_label_instruction = ""
    if relationships_cfg:
        axis = relationships_cfg["axis"]
        relationships_line = (
            f"\nCURRENT RELATIONSHIPS (name: score from -100 {axis['negative']} to +100 "
            f"{axis['positive']}, 0 neutral/unknown): {json.dumps(relationship_scores)}"
        )
        exact_name_instruction = (
            "If a relationship_changes entry refers to someone already listed in EXISTING "
            "CHARACTERS, its key must be that exact string, copied verbatim - never a "
            "shortened, reordered, or paraphrased version of it (e.g. if EXISTING CHARACTERS "
            'lists "Salome Vence (the Advocate)", use that exact string, not "Salome Vence" or '
            '"the advocate"). This is what lets the relationship stay linked to that character\'s '
            "record instead of silently forking into an unlinked, seemingly-new name.\n"
        )
        generic_label_instruction = (
            " A generic-label character should still get a relationship_changes entry as usual,"
            " just not a new_characters one."
        )

    # A trigger is authored as a description of an event ("the first time the protagonist
    # attempts a non-trivial computational proof"), but narration never echoes that wording -
    # it renders the event. Without this, the model treats the trigger list as context rather
    # than as something to evaluate, and fires nothing: 0 of 2 across a 24-turn playthrough
    # whose turns 18 and 23 both plainly satisfied one (docs/PHASE_0_GATE_REPORT.md §4).
    fragment_instruction = ""
    if unrevealed_fragments:
        fragment_instruction = (
            "For memory_fragments_revealed, check the NARRATION against each UNREVEALED MEMORY "
            "FRAGMENT TRIGGER and list the id of every one the narration satisfies this turn. "
            "Judge a trigger by what actually happens in the scene, not by whether the narration "
            "reuses the trigger's wording - a trigger describing an act is satisfied by the "
            "protagonist performing that act however it is written. Return [] if none apply; "
            "never force a match.\n"
        )

    failure_line = f"\nFAILURE CONDITIONS (id: trigger): {json.dumps(failure_triggers)}" if failure_conditions else ""

    prompt = f"""Given this turn of an interactive story, report what changed in the world state.

ACTIVE SUBPLOTS (id: title - description [progress/threshold]):
{active_subplot_lines}
UNREVEALED MEMORY FRAGMENT TRIGGERS: {json.dumps(unrevealed_fragments)}
CURRENT FLAGS: {json.dumps(ctx["state"]["protagonist"]["flags"]["active"])}
CURRENT INVENTORY: {json.dumps(ctx["state"]["protagonist"]["inventory"])}{relationships_line}
EXISTING CHARACTERS (do not repeat in new_characters): {', '.join(existing_characters) or 'none'}{stats_block}
CURRENT SCENE ({scene['location']}): {scene['summary']}{locations_hint}{failure_line}

PLAYER ACTION: {player_action}
NARRATION: {ai_response}

Respond with ONLY a JSON object, no other text, in this exact shape:
{{
{schema_str}
}}
Only include subplot ids, flags, fragment ids, items, character names, and stats that actually
changed this turn. Use {{}}/[] for nothing changed. Omit scene_update entirely if the
protagonist's location and situation are unchanged from CURRENT SCENE above.
{fragment_instruction}{exact_name_instruction}Only add an entry to new_characters when a character is given an actual proper name for the
first time this turn (e.g. "Marlowe", "Elena Cho") AND isn't already in EXISTING CHARACTERS -
never for a generic/descriptive handle (e.g. "the guard", "the advocate", "the woman at the
terminal").{generic_label_instruction} Promoting a generic-label character to a full one later
is a separate, manual step."""

    try:
        diff = _timed("state_update", lambda: call_llm_json(prompt), model=TIER_C_MODEL)
    except (json.JSONDecodeError, ValueError):
        return {}

    subplots_state = ctx["state"]["plot"]["subplots"]
    for subplot_id, delta in diff.get("subplot_progress", {}).items():
        if subplot_id in subplots_state and subplots_state[subplot_id].get("active"):
            sp = subplots_state[subplot_id]
            threshold = _subplot_view(ctx, subplot_id)["completion_threshold"]
            sp["progress"] = max(0, min(threshold, sp.get("progress", 0) + int(delta)))

    turn_count = ctx["state"]["pacing"]["turn_count"]
    flags = ctx["state"]["protagonist"]["flags"]
    for flag_name, flag_info in diff.get("flags_set", {}).items():
        if isinstance(flag_info, dict):
            value = flag_info.get("value", True)
            pinned = bool(flag_info.get("pinned", False))
        else:
            # tolerate a bare boolean if the model doesn't follow the nested shape
            value = flag_info
            pinned = False
        flags["active"][flag_name] = value
        flags["meta"][flag_name] = {"turn_set": turn_count, "pinned": pinned}

    revealed_now = set(diff.get("memory_fragments_revealed", []))
    valid_revelation_ids = {r["id"] for r in revelations}
    for rev_id in revealed_now:
        if rev_id in valid_revelation_ids:
            ctx["state"]["plot"]["revelations_revealed"][rev_id] = {"turn": turn_count}

    if diff.get("entity_interaction"):
        ctx["state"]["plot"]["entity_contact_count"] += 1

    # CR-01: apply rules per the schema instruction above - an invalid/unknown location is
    # rejected (kept as the previous value) rather than accepted, a missing/empty summary
    # leaves the previous one in place, and a missing scene_update key is a no-op (this
    # block just never runs).
    scene_update = diff.get("scene_update")
    if isinstance(scene_update, dict):
        new_location = scene_update.get("location")
        if new_location and (not valid_locations or new_location in valid_locations):
            scene["location"] = new_location
        new_summary = scene_update.get("summary")
        if new_summary:
            scene["summary"] = new_summary
        if isinstance(scene_update.get("present_npcs"), list):
            scene["present"] = scene_update["present_npcs"]

    inventory = ctx["state"]["protagonist"]["inventory"]
    for item in diff.get("items_gained", []):
        if item:
            inventory.append(item)
    for item in diff.get("items_lost", []):
        if item in inventory:
            inventory.remove(item)

    # New, properly-named characters the narration introduced this turn (see the
    # new_characters prompt instruction above) get a real record immediately and are
    # ready to be linked to a relationship_changes entry in the very same diff (see
    # below) - this is the direct fix for a character that only ever existed as a bare
    # relationship name with nothing behind it. Deliberately gated on the model having
    # actually named them (see the prompt) rather than every incidental relationship, to
    # avoid spinning up records for generic background figures.
    known_names = set(existing_characters)
    for draft in diff.get("new_characters", []):
        name = draft.get("name")
        if not name or name in known_names:
            continue
        insert_character(
            ctx, name,
            description=draft.get("description", ""),
            role=draft.get("role", ""),
            relationship_to_player=draft.get("relationship_to_player", ""),
            hook=draft.get("hook", ""),
            introduced=True,
            origin="narration",
        )
        known_names.add(name)

    # Gated on relationships_cfg like the schema field above - a story that opted out of
    # score tracking shouldn't start accumulating scores just because a stray key showed up.
    if relationships_cfg:
        for char_name, delta in diff.get("relationship_changes", {}).items():
            if not char_name:
                continue
            entry = characters.setdefault(char_name, {"relationship": 0, "first_seen_turn": turn_count})
            entry["relationship"] = max(-100, min(100, entry.get("relationship", 0) + int(delta)))
            # Meeting an authored or previously-seeded character in play (a relationship_changes
            # entry for them means the model narrated an actual interaction this turn) is what
            # flips them off generate_pacing_nudge's "CHARACTERS TO WEAVE IN" line - name-keying
            # makes this a direct write, no separate id-matching scan needed (unlike v1's
            # npc_id-linking pass, which only had a name to go on the first time).
            entry["introduced"] = True

    # Bounded like flags_active: if a story accumulates more named/discovered characters
    # than this, drop the least narratively significant ones first (closest to neutral),
    # not the oldest - a strongly-loved or strongly-hated character should never be the one
    # that gets evicted. An authored character (present in ctx["story"]["world"]
    # ["characters"]) is never evicted, regardless of score - CR-06's "authored: true"
    # flag is no longer needed for this; presence in the template *is* the flag.
    # 5.3: mechanics.relationships.limit is the real per-story dial now; RELATIONSHIPS_LIMIT
    # is just what a story without mechanics.relationships (or without an explicit limit)
    # degrades to.
    limit = (relationships_cfg or {}).get("limit", RELATIONSHIPS_LIMIT)
    authored_names = set(ctx["story"]["world"].get("characters", {}).keys())
    if len(characters) > limit:
        removable = sorted(
            (n for n in characters if n not in authored_names),
            key=lambda n: abs(characters[n].get("relationship", 0)),
        )
        for name in removable[:len(characters) - limit]:
            del characters[name]

    # 5.2: mechanics.stats.floor/.ceiling replaces the old global STAT_FLOOR=0 constant -
    # per-story bounds, since one story's scale might be a 0-10 attribute and another's a
    # negative-capable meter (debt, temperature). ceiling is null/absent by default
    # (unbounded); floor falls back to the old global default for a story that doesn't
    # author mechanics.stats at all. Only ever adjusts a stat that's already in
    # protagonist.stats (seeded once, at character creation, from the chosen class's
    # starting_stats - see apply_creation_choice) - the model can't introduce a new stat
    # axis outside that fixed, story-authored set.
    stats_cfg = ctx["story"].get("mechanics", {}).get("stats", {})
    floor = stats_cfg.get("floor", STAT_FLOOR)
    ceiling = stats_cfg.get("ceiling")
    for stat_name, delta in diff.get("stat_changes", {}).items():
        if stat_name in stats:
            new_value = max(floor, stats[stat_name] + int(delta))
            if ceiling is not None:
                new_value = min(ceiling, new_value)
            stats[stat_name] = new_value

    # 5.7: applied last, after every other effect of this turn has already landed - a
    # failing turn's subplot progress/flags/items/etc. still get recorded before the ending
    # machinery takes over.
    if failure_conditions:
        condition = next((c for c in failure_conditions if c["id"] == diff.get("failure_triggered")), None)
        if condition:
            _apply_failure_condition(ctx, condition)

    return diff


def archive_stale_flags(ctx: dict):
    """Keep flags.active bounded without an LLM call: once a flag's setting turn falls
    outside the recent-turns window, it's retired to flags.archive - by then its
    consequences have already had a chance to pass through the compressed_summary
    rollover, so nothing narratively important is silently lost. Pinned flags (foundational
    facts) are exempt. A hard cap on flags.active is a fallback in case pins pile up."""
    flags = ctx["state"]["protagonist"]["flags"]
    turn_count = ctx["state"]["pacing"]["turn_count"]
    stale_cutoff = turn_count - RECENT_TURN_LIMIT

    for flag_name in list(flags["active"].keys()):
        meta = flags["meta"].get(flag_name, {})
        if meta.get("pinned"):
            continue
        if meta.get("turn_set", turn_count) <= stale_cutoff:
            flags["archive"][flag_name] = flags["active"].pop(flag_name)
            flags["meta"].pop(flag_name, None)

    if len(flags["active"]) > FLAGS_ACTIVE_LIMIT:
        evictable = sorted(
            (name for name in flags["active"] if not flags["meta"].get(name, {}).get("pinned")),
            key=lambda name: flags["meta"].get(name, {}).get("turn_set", 0),
        )
        for name in evictable:
            if len(flags["active"]) <= FLAGS_ACTIVE_LIMIT:
                break
            flags["archive"][name] = flags["active"].pop(name)
            flags["meta"].pop(name, None)


def generate_new_subplot(ctx: dict):
    """Invent and insert a new subplot to keep the pool topped up. No-op once the story
    is in its ending sequence, or if the pool is already full."""
    if ctx["state"]["plot"]["endgame"]["requested"]:
        return None

    subplots_view = _all_subplots(ctx)
    max_parallel = ctx["story"]["plot"]["pacing"]["max_parallel_subplots"]
    live_count = sum(1 for sp in subplots_view.values() if sp["status"] != "completed")
    if live_count >= max_parallel:
        return None

    # Live subplots are naturally bounded (max_parallel_subplots); completed ones
    # accumulate for the whole game, so only keep the most recent ones for dedup
    # context instead of sending every title ever generated.
    live_titles = [sp["title"] for sp in subplots_view.values() if sp["status"] != "completed"]
    recent_completed_ids = ctx["state"]["plot"]["completed_subplots"][-SUBPLOT_TITLE_HISTORY_LIMIT:]
    recent_completed_titles = [subplots_view[sid]["title"] for sid in recent_completed_ids if sid in subplots_view]
    existing_titles = live_titles + recent_completed_titles
    existing_characters = _existing_character_names(ctx)
    summary = ctx["state"]["history"]["compressed_summary"] or "The story has just begun."
    main_thread = _main_thread_view(ctx)
    current_act = _current_act(ctx)
    thread_steering = ctx["state"]["plot"]["thread_steering"]

    prompt = f"""Invent a new subplot for an ongoing interactive story.

WORLD RULES:
{chr(10).join(f"- {r}" for r in ctx["story"]["world"]["rules"])}

MAIN THREAD: {main_thread['title']} - {main_thread['description']}
CURRENT ACT: {current_act['title']} - {current_act['description']}
STORY SO FAR: {summary}
EXISTING SUBPLOT TITLES (do not repeat): {', '.join(existing_titles) or 'none'}
EXISTING CHARACTERS (do not repeat): {', '.join(existing_characters) or 'none'}
EMERGING THEMES: {', '.join(thread_steering.get('emerging_themes', [])) or 'none noted'}

Respond with ONLY a JSON object, no other text:
{{
  "title": "<short subplot title>",
  "description": "<1-2 sentence description>",
  "priority": "<high|medium|low>",
  "ties_to_main_plot": "<how this connects to the main thread>",
  "span": "<single_act|multi_act>",
  "new_character": <null, or {{"name": "<full name>", "description": "...", "role": "...", "relationship_to_player": "...", "hook": "..."}} if and only if this subplot genuinely requires a specific new named person to exist who isn't already listed above>
}}
Most subplots should be "single_act" - resolved within roughly the current act. Only mark
"multi_act" if the idea is substantial enough to reasonably develop over several acts -
these should feel like a throughline, not a quick errand, and should be the exception, not
the rule. Leave new_character null unless the subplot really can't work without a specific
new person - most subplots don't need one."""

    try:
        generated = _timed(
            "subplot_generation",
            lambda: call_llm_json(prompt, model=TIER_AB_MODEL, provider=TIER_AB_PROVIDER, reasoning=True),
            model=TIER_AB_MODEL,
        )
        title = generated["title"]
        description = generated["description"]
    except (json.JSONDecodeError, KeyError, ValueError):
        return None

    new_id = insert_subplot(
        ctx, title, description,
        priority=generated.get("priority", "medium"),
        ties_to_main_plot=generated.get("ties_to_main_plot", ""),
        span=generated.get("span") if generated.get("span") in ("single_act", "multi_act") else "single_act",
    )
    _maybe_insert_generated_character(ctx, generated, origin="subplot")
    return new_id


def generate_steering_seed(ctx: dict, note: str):
    """Mid-adventure steering, LLM-assisted: turns a freeform background note - not a scene
    action to narrate now, a suggestion for something to work into the story going forward -
    into a structured draft. Infers whether the note is best realized as a new CHARACTER, a
    new SUBPLOT, or a looser plot DIRECTION, and generates the matching content. Never
    mutates state or commits anything - returns {"type": ..., "draft": {...}} for
    plot_manager.stage_steering_seed to hold for the player to review/edit before anything
    is applied (see apply_steering_seed), same as generate_new_subplot returning None on a
    bad generation rather than raising, so a failed attempt just costs nothing."""
    main_thread = _main_thread_view(ctx)
    current_act = _current_act(ctx)
    subplots_view = _all_subplots(ctx)
    live_titles = [sp["title"] for sp in subplots_view.values() if sp["status"] != "completed"]
    existing_characters = _existing_character_names(ctx)
    summary = ctx["state"]["history"]["compressed_summary"] or "The story has just begun."

    prompt = f"""You are helping the player steer an ongoing interactive story between turns.
They've given you a background note - NOT a scene action to narrate right now, but a
suggestion for something to work into the story going forward. Decide what kind of
addition it implies, and generate it.

WORLD RULES:
{chr(10).join(f"- {r}" for r in ctx["story"]["world"]["rules"])}

MAIN THREAD: {main_thread['title']} - {main_thread['description']}
CURRENT ACT: {current_act['title']} - {current_act['description']}
STORY SO FAR: {summary}
EXISTING SUBPLOT TITLES (do not repeat): {', '.join(live_titles) or 'none'}
EXISTING CHARACTERS (do not repeat): {', '.join(existing_characters) or 'none'}

PLAYER'S NOTE: {note}

Decide whether this note is best realized as a new CHARACTER, a new SUBPLOT, or a plot
DIRECTION (a looser narrative hook not tied to one character or a self-contained subplot).
Respond with ONLY a JSON object, no other text, in exactly one of these three shapes:

{{"type": "character", "character": {{"name": "<full name>", "description": "<who they are, appearance, personality>", "role": "<their narrative role, e.g. 'potential romantic interest'>", "relationship_to_player": "<initial stance toward the player>", "hook": "<a concrete, specific way they could naturally enter the story soon>"}}}}

{{"type": "subplot", "subplot": {{"title": "<short subplot title>", "description": "<1-2 sentences>", "priority": "<high|medium|low>", "ties_to_main_plot": "<how this connects to the main thread>", "span": "<single_act|multi_act - multi_act only if the note clearly implies something substantial enough to develop over several acts, not a quick errand>"}}}}

{{"type": "direction", "direction": {{"title": "<short title>", "description": "<1-2 sentences>"}}}}"""

    try:
        generated = _timed(
            "steering_seed_generation",
            lambda: call_llm_json(prompt, model=TIER_AB_MODEL, provider=TIER_AB_PROVIDER, reasoning=True),
            model=TIER_AB_MODEL,
        )
        seed_type = generated["type"]
        if seed_type not in ("character", "subplot", "direction"):
            raise KeyError(seed_type)
        draft = generated[seed_type]
        if not isinstance(draft, dict):
            raise ValueError(draft)
    except (json.JSONDecodeError, KeyError, ValueError):
        return None

    return {"type": seed_type, "draft": draft}


def generate_character_from_relationship(ctx: dict, name: str):
    """Manual promotion path (plot_manager.promote_relationship_to_npc): drafts a full
    character record for a name that's only ever existed as a bare relationship score (see
    plot_manager.list_unlinked_relationships) - the tracked-but-never-formalized case
    update_progress_from_turn deliberately leaves alone for generic/descriptive handles.
    Same "never mutates state, returns a draft or None on bad output" contract as
    generate_steering_seed - the caller (promote_relationship_to_npc) decides what to do with
    the result."""
    entry = ctx["state"]["characters"].get(name)
    if entry is None:
        return None
    score = entry.get("relationship", 0)
    summary = ctx["state"]["history"]["compressed_summary"] or "The story has just begun."
    recent = "\n".join(ctx["state"]["history"]["recent_turns"][-RECENT_TURN_LIMIT:])

    prompt = f"""An interactive story has been tracking a relationship score for a character who
was never given a full character record. Draft one now, based on what's actually happened with
them so far.

WORLD RULES:
{chr(10).join(f"- {r}" for r in ctx["story"]["world"]["rules"])}

STORY SO FAR: {summary}
RECENT EXCHANGES:
{recent}

CHARACTER NAME/LABEL: {name}
CURRENT RELATIONSHIP SCORE (-100 hostile to +100 devoted, 0 neutral): {score}

Respond with ONLY a JSON object, no other text:
{{
  "description": "<who they are, appearance, personality - inferred from how they've actually appeared so far>",
  "role": "<their narrative role>",
  "relationship_to_player": "<their stance toward the player, consistent with the score above and what's happened>",
  "hook": "<a concrete way they could naturally reappear or matter going forward>"
}}"""

    try:
        draft = _timed(
            "relationship_promotion",
            lambda: call_llm_json(prompt, model=TIER_AB_MODEL, provider=TIER_AB_PROVIDER, reasoning=True),
            model=TIER_AB_MODEL,
        )
        if not isinstance(draft, dict):
            raise ValueError(draft)
    except (json.JSONDecodeError, ValueError):
        return None

    return draft


def is_end_story_command(action: str) -> bool:
    return action.strip().lower() in END_STORY_PHRASES


def _begin_endgame(ctx: dict, final_arc: dict, cause: str):
    """Shared by handle_end_story_request (cause="player_request") and 5.7's failure-
    condition path (cause=<condition id>): locks in the ending machinery - marks
    endgame.requested, records who/what caused it and when, and appends a finale act.
    No new code path for endings, per SCHEMA_V2_SPEC.md §3.6 - a failure condition firing
    is just a second way to enter the same ending machinery."""
    endgame = ctx["state"]["plot"]["endgame"]
    endgame["requested"] = True
    endgame["requested_turn"] = ctx["state"]["pacing"]["turn_count"]
    endgame["final_arc"] = final_arc
    endgame["cause"] = cause

    new_act_number = max((a["act_number"] for a in _all_acts(ctx)), default=0) + 1
    ctx["state"]["plot"]["generated_acts"].append({
        "act_number": new_act_number,
        "title": final_arc["title"],
        "description": final_arc["description"],
        "completion_signals": [],
        "completed": False,
        "optional": False,
        "is_finale": True,
    })
    ctx["state"]["plot"]["current_act"] = new_act_number


def handle_end_story_request(ctx: dict) -> dict:
    """Commit the story to a finale: no more new subplots or acts, just resolution."""
    endgame = ctx["state"]["plot"]["endgame"]
    if endgame["requested"]:
        return endgame["final_arc"]

    subplots_view = _all_subplots(ctx)
    active_subplots = [sp["title"] for sp in subplots_view.values() if sp["active"]]
    main_thread = _main_thread_view(ctx)
    summary = ctx["state"]["history"]["compressed_summary"] or "The story has just begun."

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
        generated = _timed(
            "end_story_final_arc",
            lambda: call_llm_json(prompt, model=TIER_AB_MODEL, provider=TIER_AB_PROVIDER),
            model=TIER_AB_MODEL,
        )
    except (json.JSONDecodeError, ValueError):
        generated = {}

    final_arc = {
        "title": generated.get("title", "The Reckoning"),
        "description": generated.get(
            "description",
            "Bring the story's open threads to a close as gracefully as the current momentum allows.",
        ),
    }
    _begin_endgame(ctx, final_arc, cause="player_request")
    return final_arc


def _apply_failure_condition(ctx: dict, condition: dict):
    """5.7: a mechanics.failure_conditions entry firing is evaluated in the state-update
    pass alongside revelations (same authored-trigger shape, different effect) - routes
    into the same endgame machinery handle_end_story_request uses, just entered
    automatically rather than by player request, and with no LLM call needed since the
    closing description is the condition's own authored ending_prompt rather than
    something to generate. No-ops if the story is already ending (whichever condition or
    request got there first wins)."""
    if ctx["state"]["plot"]["endgame"]["requested"]:
        return
    final_arc = {
        "title": condition.get("title") or "The Ending",
        "description": condition["ending_prompt"],
    }
    _begin_endgame(ctx, final_arc, cause=condition["id"])


def check_and_advance_act(ctx: dict):
    """At a pacing checkpoint, ask the director whether the current act has narratively
    resolved and, if so, generate the next one. No-op once the story is ending.

    Gated on "a subplot completed this act" OR "it's been act_check_frequency turns since
    the last check" - not just the former. A subplot completing is still the fast path (the
    same turn it happens, this fires right away), but requiring it unconditionally used to
    mean no act could ever advance without one - which meant every subplot was structurally
    pressured to wrap up within a single act just to keep the story moving, since a
    deliberately longer-running ("multi_act", see insert_subplot) subplot would otherwise
    stall the act forever. The turn-count fallback lets the director re-evaluate on its own
    cadence even when nothing has completed, the same way generate_pacing_nudge already
    does on nudge_frequency - it's what makes a multi_act subplot's non-completion stop
    being the only thing standing between the story and its next act."""
    if ctx["state"]["plot"]["endgame"]["requested"]:
        return None

    pacing_state = ctx["state"]["pacing"]
    completed_recently = pacing_state["subplots_completed_this_act"] >= 1
    act_check_frequency = ctx["story"]["plot"]["pacing"].get("act_check_frequency", DEFAULT_ACT_CHECK_FREQUENCY)
    due_for_check = pacing_state.get("turns_since_act_check", 0) >= act_check_frequency
    if not completed_recently and not due_for_check:
        return None
    pacing_state["turns_since_act_check"] = 0

    current_act = _current_act(ctx)
    if not current_act:
        return None

    summary = ctx["state"]["history"]["compressed_summary"] or "The story has just begun."
    recent = "\n".join(ctx["state"]["history"]["recent_turns"][-RECENT_TURN_LIMIT:])
    # completed_subplots accumulates for the whole game; subplots_completed_this_act
    # tells us how many of the most recent entries belong to the current act, so slice
    # to just those instead of feeding in every subplot ever completed.
    subplots_view = _all_subplots(ctx)
    recent_completed_ids = ctx["state"]["plot"]["completed_subplots"][-pacing_state["subplots_completed_this_act"]:]
    completed_titles = [subplots_view[sid]["title"] for sid in recent_completed_ids if sid in subplots_view]
    revealed_fragments = len(ctx["state"]["plot"]["revelations_revealed"])
    ongoing_multi_act = [
        sp["title"] for sp in subplots_view.values()
        if sp.get("span") == "multi_act" and sp["status"] != "completed"
    ]
    existing_characters = _existing_character_names(ctx)
    # CR-07: "the Architect" used to be hardcoded here regardless of story - see the matching
    # comment in update_progress_from_turn. Only asked about when the story configures one.
    tracked_entity = ctx["story"].get("mechanics", {}).get("tracked_entity")
    entity_line = (
        f"{tracked_entity['name']} ENCOUNTERS: {ctx['state']['plot']['entity_contact_count']}\n"
        if tracked_entity else ""
    )

    prompt = f"""You are the pacing director for an ongoing interactive story. Judge whether the
current act feels narratively resolved, based on what's actually happened - not a checklist.

CURRENT ACT: {current_act['title']} - {current_act['description']}
SIGNALS THIS ACT WAS BUILT AROUND: {', '.join(current_act.get('completion_signals', [])) or 'none'}
SUBPLOTS COMPLETED THIS ACT: {', '.join(completed_titles) or 'none'}
ONGOING MULTI-ACT SUBPLOTS (deliberately still running, expected to continue beyond this act - their non-completion is not a sign the act hasn't resolved): {', '.join(ongoing_multi_act) or 'none'}
MEMORY FRAGMENTS REVEALED: {revealed_fragments}
{entity_line}EXISTING CHARACTERS (do not repeat): {', '.join(existing_characters) or 'none'}
STORY SO FAR: {summary}
RECENT EXCHANGES:
{recent}

Respond with ONLY a JSON object, no other text:
{{
  "ready": <true|false>,
  "reason": "<one sentence>",
  "next_act_title": "<title, only if ready>",
  "next_act_description": "<1-2 sentences, only if ready>",
  "completion_signals": ["<specific, checkable signal for what would resolve the NEXT act>", "..."],
  "new_character": <null, or {{"name": "<full name>", "description": "...", "role": "...", "relationship_to_player": "...", "hook": "..."}} if and only if ready is true and the next act genuinely requires a specific new named person who isn't already listed above>
}}
completion_signals is required whenever ready is true - 2-4 specific, checkable signals for
the act you're creating (not a restatement of its description), the same specificity as the
signals a human author would write for a hand-crafted act. Leave new_character null unless
the next act really can't work without a specific new person - most acts don't need one."""

    try:
        verdict = _timed(
            "act_advancement_check",
            lambda: call_llm_json(prompt, model=TIER_AB_MODEL, provider=TIER_AB_PROVIDER, reasoning=True),
            model=TIER_AB_MODEL,
        )
    except (json.JSONDecodeError, ValueError):
        return None

    if not verdict.get("ready"):
        return None

    _mark_act_completed(ctx, current_act["act_number"])
    new_act_number = max((a["act_number"] for a in _all_acts(ctx)), default=0) + 1
    ctx["state"]["plot"]["generated_acts"].append({
        "act_number": new_act_number,
        "title": verdict.get("next_act_title") or f"Act {new_act_number}",
        "description": verdict.get("next_act_description", ""),
        "completion_signals": verdict.get("completion_signals") or [],
        "completed": False,
        "optional": False,
    })
    ctx["state"]["plot"]["act_history"].append({
        "from_act": current_act["act_number"],
        "to_act": new_act_number,
        "reason": verdict.get("reason", ""),
        "turn": pacing_state["turn_count"],
    })
    ctx["state"]["plot"]["current_act"] = new_act_number
    pacing_state["subplots_completed_this_act"] = 0
    _maybe_insert_generated_character(ctx, verdict, origin="act")

    return new_act_number


def generate_pacing_nudge(ctx: dict) -> str:
    """Generate a meta-instruction to nudge the story toward active goals."""
    pacing_state = ctx["state"]["pacing"]
    subplots_view = _all_subplots(ctx)
    thread_steering = ctx["state"]["plot"]["thread_steering"]

    active_subplots = [(sid, sp) for sid, sp in subplots_view.items() if sp["active"]]

    nudge_parts = []

    current_act = _current_act(ctx)
    if current_act:
        nudge_parts.append(f"PACING: Currently in Act {current_act['act_number']} - {current_act['description']}")
        # CR-09: completion_signals reached the act-check prompt but never the narrator,
        # who was never told what would actually resolve the act it's writing toward.
        if current_act.get("completion_signals"):
            nudge_parts.append(f"THIS ACT RESOLVES WHEN: {', '.join(current_act['completion_signals'])}")

    if active_subplots:
        priority_map = {"high": 3, "medium": 2, "low": 1}
        active_subplots_sorted = sorted(active_subplots, key=lambda x: priority_map.get(x[1]["priority"], 0), reverse=True)

        primary_subplot = active_subplots_sorted[0][1]
        primary_tag = " (ongoing, multi-act)" if primary_subplot.get("span") == "multi_act" else ""
        nudge_parts.append(f"ACTIVE SUBPLOT: '{primary_subplot['title']}'{primary_tag} - {primary_subplot['description']}")
        # CR-13: ties_to_main_plot is authored/generated for every subplot and shown in
        # subplot_manager.html, but never told to the narrator - this is the field that
        # says *why* the subplot matters to the main thread.
        if primary_subplot.get("ties_to_main_plot"):
            nudge_parts.append(f"TIES TO MAIN: {primary_subplot['ties_to_main_plot']}")

        if len(active_subplots) > 1:
            other_titles = [
                sp["title"] + (" (multi-act)" if sp.get("span") == "multi_act" else "")
                for _, sp in active_subplots_sorted[1:3]
            ]
            nudge_parts.append(f"BACKGROUND SUBPLOTS: {', '.join(other_titles)}")

    max_parallel = ctx["story"]["plot"]["pacing"]["max_parallel_subplots"]
    if len(active_subplots) < max_parallel:
        inactive_subplots = [(sid, sp) for sid, sp in subplots_view.items() if sp["status"] == "not_started"]
        if inactive_subplots:
            nudge_parts.append(f"SUBPLOT OPPORTUNITY: Consider introducing hooks for '{inactive_subplots[0][1]['title']}' when appropriate.")

    # Steering content noted between turns - via plot_manager.py's add-emergent/add-goal, or
    # an applied steering seed (generate_steering_seed/apply_steering_seed). Each list stays
    # unbounded on disk (same as completed_subplots); only the most recent couple of
    # *unresolved* entries are ever pulled into the prompt - a promoted direction or an
    # introduced character stops appearing on its own via the flag that already marks it
    # resolved, no separate eviction needed.
    # Runs over the merged roster, not just ctx["state"]["characters"] - a template's
    # authored world.characters never get a state entry until they actually appear in play,
    # so reading state alone meant a hand-authored NPC's `hook` (the one field whose whole
    # job is "here's a concrete way to bring this person on stage") reached no prompt at
    # all, and only LLM-invented characters were ever actively woven in. Ordered authored-
    # first so the [-2:] slice still favours the most recently discovered character, and
    # so the order is stable rather than set-iteration order.
    authored_names = ctx["story"]["world"].get("characters", {})
    ordered_names = list(authored_names) + [n for n in ctx["state"]["characters"] if n not in authored_names]
    pending_characters = [
        record for record in (_character_record(ctx, name) for name in ordered_names)
        if not record["introduced"] and record["hook"]
    ]
    if pending_characters:
        hooks = "; ".join(f"{c['name']} - {c['hook']}" for c in pending_characters[-2:])
        nudge_parts.append(f"CHARACTERS TO WEAVE IN: {hooks}")

    pending_directions = [d for d in ctx["state"]["plot"].get("emergent_directions", []) if not d.get("promoted")]
    if pending_directions:
        directions = "; ".join(f"{d['title']} - {d['description']}" for d in pending_directions[-2:])
        nudge_parts.append(f"NOTED DIRECTION: {directions}")

    active_goals = [g for g in thread_steering.get("player_driven_goals", []) if g.get("active")]
    if active_goals:
        goals = "; ".join(g["description"] for g in active_goals[-2:])
        nudge_parts.append(f"PLAYER GOAL: {goals}")

    # CR-12: emerging_themes reached generate_new_subplot but never the prose itself.
    emerging_themes = thread_steering.get("emerging_themes", [])
    if emerging_themes:
        nudge_parts.append(f"EMERGING THEMES: {', '.join(emerging_themes)}")

    pacing_state["last_direction"] = " | ".join(nudge_parts)
    return "\n".join(nudge_parts)


# ---------------------------------------------------------------------------
# build_system_prompt's SECTIONS (SCHEMA_V2_SPEC.md §5): a declarative list of
# (ctx -> str|None) builders instead of one long f-string. Each returns a self-contained
# block with no leading/trailing blank lines of its own; build_system_prompt joins every
# non-None result with a blank line. None means "contributes nothing" - not an empty
# header - which is what makes every optional module (setting, factions, revelations, ...)
# a one-line addition to SECTIONS rather than a new conditional threaded through a giant
# f-string. Ordered per G-3: stable content first (identity, world rules, setting,
# factions, main thread - a cacheable prefix across many turns), volatile content last
# (recent exchanges, pacing nudge/endgame, scene, player line).
# ---------------------------------------------------------------------------

def _section_identity(ctx: dict) -> str:
    meta = ctx["story"]["meta"]
    narration_cfg = ctx["story"].get("narration", {})
    # CR-14: narration.pov was declared in every template but never actually stated to the
    # narrator - it only held because the hand-authored opening scene establishes the voice
    # and RECENT EXCHANGES sustains it from there.
    pov_str = f" | POV: {narration_cfg['pov']}" if narration_cfg.get("pov") else ""
    return (
        f"TITLE: {meta['title']} | GENRE: {meta['genre']} | TONE: {meta['tone']}{pov_str}\n"
        f"CONTENT RULES: {', '.join(meta['content_rules'])}"
    )


def _section_world_rules(ctx: dict) -> str:
    rules_str = "\n".join(f"- {r}" for r in ctx["story"]["world"]["rules"])
    return f"WORLD RULES (must not be broken):\n{rules_str}"


def _section_setting(ctx: dict) -> str | None:
    # CR-04: world.setting_summary was authored but never fed to any prompt. Stable for
    # the whole playthrough, so it sits in the cacheable prefix above STORY SO FAR (G-3).
    summary = ctx["story"]["world"].get("setting_summary")
    return f"SETTING: {summary}" if summary else None


def _section_factions(ctx: dict) -> str | None:
    # CR-04: same as setting - authored, stable, previously unprompted.
    factions = ctx["story"]["world"].get("factions", {})
    if not factions:
        return None
    lines = "\n".join(
        f"- {f['name']}: {f.get('goals', '')} (toward the player: {f.get('relationship_to_player', '')})"
        for f in factions.values()
    )
    return f"FACTIONS:\n{lines}"


def _section_roster(ctx: dict) -> str | None:
    """5.6/CR-06: the top-level characters registry used to be entirely disconnected from
    the score-tracking one - authored NPCs in a template were invisible to the narrator,
    and relationship scores attached to whatever free-text name the model chose, with no
    canonical identity behind them. The story/state split already made ctx["story"]["world"]
    ["characters"] (authored) and ctx["state"]["characters"] (discovered) share one key
    space (see _character_record) - this section is what actually surfaces the merged
    result to the narrator. A bare relationship-only stub (no description, not authored) is
    left out - it has no identity worth restating beyond the name the model itself chose.
    Bounded implicitly by RELATIONSHIPS_LIMIT/mechanics.relationships.limit, since
    ctx["state"]["characters"] is already capped there at write time (see
    update_progress_from_turn) - no separate cap needed here."""
    relationships_cfg = ctx["story"].get("mechanics", {}).get("relationships")
    lines = []
    for name in sorted(_all_character_names(ctx)):
        record = _character_record(ctx, name)
        if not record["description"] and not record["authored"]:
            continue
        score_part = ""
        if relationships_cfg and record["relationship"] is not None:
            score_part = f" ({record['relationship']:+d})"
        line = f"- {name}{score_part}"
        if record["description"]:
            line += f": {record['description']}"
        lines.append(line)
    if not lines:
        return None
    axis_hint = ""
    if relationships_cfg:
        axis = relationships_cfg["axis"]
        axis_hint = f"; standing is -100 {axis['negative']} to +100 {axis['positive']}"
    return f"KNOWN CHARACTERS (use these exact names{axis_hint}):\n" + "\n".join(lines)


def _section_main_thread(ctx: dict) -> str:
    # CR-05: main_thread reached generate_new_subplot and handle_end_story_request, but
    # never the narration prompt itself - seven turns out of eight, the narrator had no
    # statement of what the story is about beyond the compressed summary. This is standing
    # context, present every turn; the pacing nudge's own act line (_section_pacing, below)
    # is separate - its role is triggering a pacing *change* on its own cadence, not just
    # stating where things stand.
    main_thread = _main_thread_view(ctx)
    current_act = _current_act(ctx)
    block = f"MAIN THREAD: {main_thread['title']} - {main_thread['description']}"
    if current_act:
        block += f"\nCURRENT ACT {current_act['act_number']}: {current_act['title']} - {current_act['description']}"
    return block


def _section_tracked_entity(ctx: dict) -> str | None:
    """5.5/CR-16: mechanics.tracked_entity's name/description already reached the
    state-update and act-check prompts (CR-07); entity_contact_count and pacing_note never
    reached the narrator at all, so it had no way to pace the entity's appearances against
    how often it had already shown up. Absent module means no block, same as before."""
    tracked_entity = ctx["story"].get("mechanics", {}).get("tracked_entity")
    if not tracked_entity:
        return None
    count = ctx["state"]["plot"]["entity_contact_count"]
    block = f"TRACKED ENTITY: {tracked_entity['name']} - {tracked_entity.get('description', '')}"
    if tracked_entity.get("pacing_note"):
        block += f"\nPacing note: {tracked_entity['pacing_note']}"
    block += f"\nPrior contact this playthrough: {count} time{'s' if count != 1 else ''}."
    return block


def _section_story_so_far(ctx: dict) -> str:
    summary = ctx["state"]["history"]["compressed_summary"] or "The story has just begun."
    return f"STORY SO FAR: {summary}"


def _section_recent(ctx: dict) -> str:
    recent = "\n".join(ctx["state"]["history"]["recent_turns"][-RECENT_TURN_LIMIT:])
    return f"RECENT EXCHANGES:\n{recent}"


def _section_pacing_or_endgame(ctx: dict) -> str | None:
    """The one section that's genuinely two mutually exclusive modes rather than a single
    optional block: once the player has asked to end the story, this becomes the ENDGAME
    instruction (every turn, not gated by nudge_frequency); otherwise it's the periodic
    pacing nudge (generate_pacing_nudge), gated on turns_since_nudge like before. Resets
    turns_since_nudge as a side effect when the nudge actually fires - same as the
    pre-refactor code."""
    endgame = ctx["state"]["plot"]["endgame"]
    if endgame["requested"]:
        subplots_view = _all_subplots(ctx)
        active_titles = [sp["title"] for sp in subplots_view.values() if sp["active"]]
        final_arc = endgame["final_arc"] or {}
        return (
            f'ENDGAME: The player has asked to conclude the story. Narrate toward a satisfying, '
            f'conclusive\nending for: "{final_arc.get("title", "")}" - {final_arc.get("description", "")}\n'
            f"Resolve these open threads and do not introduce any new subplots, factions, or plot "
            f"threads: {', '.join(active_titles) or 'none remaining'}.\n"
            'When the story reaches a natural conclusion, end the narration with the exact line '
            '"THE END" on\nits own line. Do not include an "OPTIONS:" block or numbered choices.'
        )
    pacing_state = ctx["state"]["pacing"]
    nudge_frequency = ctx["story"]["plot"]["pacing"]["nudge_frequency"]
    if pacing_state["turns_since_nudge"] >= nudge_frequency:
        pacing_state["turns_since_nudge"] = 0
        return generate_pacing_nudge(ctx)
    return None


def _section_scene(ctx: dict) -> str:
    # CR-02/CR-04: HERE/ADJACENT changes on movement, so - unlike SETTING/FACTIONS - it's
    # placed with the volatile CURRENT SCENE line rather than in the stable prefix. Only
    # the current location and its direct neighbours, not the full table - keeps this
    # bounded regardless of how many locations a story authors. A dangling connected_to id
    # (or a location not in world.locations at all, e.g. a free-text one CR-01's
    # scene_update accepted) is skipped silently rather than rendered raw or raised.
    scene = ctx["state"]["scene"]
    locations = ctx["story"]["world"].get("locations", {})
    here_block = ""
    current_location = locations.get(scene["location"])
    if current_location:
        adjacent_names = [
            _location_name(ctx, nid) for nid in current_location.get("connected_to", []) if nid in locations
        ]
        here_block = f"HERE: {current_location['name']} - {current_location.get('description', '')}\n"
        if adjacent_names:
            here_block += f"ADJACENT: {', '.join(adjacent_names)}\n"
    return f"{here_block}CURRENT SCENE ({_location_name(ctx, scene['location'])}): {scene['summary']}"


def _section_revelations(ctx: dict) -> str | None:
    # CR-03: only revealed fragments' content ever reaches the narrator here; the state-update
    # prompt (update_progress_from_turn) sees only unrevealed triggers - neither pass sees the
    # other half. Capped to the most recently revealed MEMORY_FRAGMENT_PROMPT_LIMIT so this
    # doesn't grow unbounded over a long game.
    revelations = ctx["story"].get("mechanics", {}).get("revelations", [])
    revealed_map = ctx["state"]["plot"]["revelations_revealed"]
    revealed_fragments = sorted(
        (r for r in revelations if r["id"] in revealed_map),
        key=lambda r: revealed_map[r["id"]].get("turn", 0),
        reverse=True,
    )
    if not revealed_fragments:
        return None
    memory_lines = "\n".join(f"- {r['content']}" for r in revealed_fragments[:MEMORY_FRAGMENT_PROMPT_LIMIT])
    return (
        "REVEALED MEMORIES (the protagonist already knows these; reference them naturally, "
        f"do not re-reveal them as though they were new):\n{memory_lines}"
    )


def _section_protagonist(ctx: dict) -> str:
    story = ctx["story"]
    protagonist = ctx["state"]["protagonist"]
    # One "| Label: chosen option name" per completed character-creation step (see
    # apply_creation_choice) - built generically off whatever steps the story authored in
    # character_creation, so a new step type (race, background, whatever a future story
    # wants) needs no changes here. Empty string for a story that doesn't use the mechanic
    # at all, so this adds nothing rather than showing empty labels for every story.
    creation_str = ""
    for step in story.get("character_creation", []):
        option_id = protagonist.get("creation_choices", {}).get(step["key"])
        option = next((o for o in step["options"] if o["id"] == option_id), None)
        if option:
            creation_str += f" | {step.get('label', step['key'].title())}: {option['name']}"
    # Internal-only: stats exist for you to reason about and adjust, never to be shown to
    # the player as numbers - reflect their effect narratively (strain, confidence, risk)
    # instead of stating a value. Conditional on the story actually using stats at all, so
    # a story without them gets no irrelevant instruction clutter.
    stats_str = f" | Stats (opaque to the player): {protagonist['stats']}" if protagonist.get("stats") else ""
    # 5.3: absent mechanics.relationships means the story tracks no relationship scores at
    # all - omitted here rather than shown as an always-empty dict, matching how it vanishes
    # from the state-update schema (update_progress_from_turn).
    relationships_part = ""
    if story.get("mechanics", {}).get("relationships"):
        relationships_str = {name: entry.get("relationship", 0) for name, entry in ctx["state"]["characters"].items()}
        relationships_part = f" | Relationships: {relationships_str}"
    return (
        f"PLAYER: {protagonist['name']}{creation_str} | Traits: {', '.join(protagonist['traits'])}"
        f"{stats_str} | Inventory: {', '.join(protagonist['inventory']) or 'nothing'}"
        f"{relationships_part} | Flags: {protagonist['flags']['active']}"
    )


def _section_style(ctx: dict) -> str | None:
    """5.1: the prose-aesthetic bullet list used to be hardcoded in story_engine.py
    regardless of genre. It's now story content (narration.style) - moved out entirely
    rather than kept as a fallback, per SCHEMA_V2_SPEC.md §3.3: absent means the model
    works from TITLE/GENRE/TONE alone, which is a sane default. Placed in the stable
    prefix (with identity) since it doesn't change turn to turn."""
    style = ctx["story"].get("narration", {}).get("style", [])
    if not style:
        return None
    lines = "\n".join(f"- {s}" for s in style)
    return f"PROSE STYLE:\n{lines}"


def _options_block_instruction(option_count: int, option_pov: str) -> str:
    """The "end with an OPTIONS: block" instruction, shared between _section_footer (the
    normal narration prompt) and generate_missing_options (a standalone follow-up call used
    when a narration reply skips the block entirely - see that function's docstring) so the
    two never drift out of a format parse_narration_and_options can actually parse."""
    numbered_examples = " / ".join(f"{i}." for i in range(1, option_count + 1))
    return (
        "End your narration with a blank line, then the exact heading \"OPTIONS:\" on its "
        f"own line, followed by exactly {option_count} numbered options ({numbered_examples}), "
        "one per line, each in the exact format <short third-person action label> || <1-2 "
        f"sentence {option_pov} prose rendition of taking that action>. Keep the action label "
        "under 15 words, distinct, and plausible.\n"
        f"The {option_count} options must diverge in kind, not in degree. Give each one a "
        "different mode: speaking or pressing someone; acting physically on the world; going "
        "somewhere or leaving; committing to a risk. Two options that differ only in tone, "
        "posture, or wording while leading to the same next scene count as one option, not "
        "two - replace one of them. At least one option must change the protagonist's "
        "physical situation rather than continue the current exchange. Asking a question is "
        "legitimate when the answer would genuinely change what the protagonist does next, "
        "but never more than one such option, and never all of them. No extra commentary "
        "after the list."
    )


def generate_missing_options(ctx: dict, narration_text: str) -> str | None:
    """Follow-up call for when a narration reply's own OPTIONS block came back missing or
    malformed (parse_narration_and_options fell back to an empty list) - rather than
    silently degrading that turn to free-text-only, or re-rolling the whole (expensive,
    already-good) narration just to get a differently-formatted block, ask the model for
    just the missing OPTIONS block, seeded with the narration it needs to react to.

    Returns the raw "OPTIONS:\\n1. ... || ...\\n..." text to append to the original
    narration, or None if this follow-up call itself failed or came back malformed - callers
    should treat None the same as the original empty-options fallback (still playable via
    free text), not as a hard failure worth failing the whole turn over."""
    narration_cfg = ctx["story"].get("narration", {})
    option_pov = narration_cfg.get("option_pov") or narration_cfg.get("pov", "first-person")
    option_count = narration_cfg.get("option_count", 3)
    prompt = (
        f"{narration_text}\n\n"
        "The scene above is missing its required list of player options. "
        f"{_options_block_instruction(option_count, option_pov)} "
        "Respond with only the OPTIONS block - no narration, no other text."
    )
    try:
        response = _timed("options_generation", lambda: call_llm(prompt), model=TIER_AB_MODEL)
    except LLMUnavailableError:
        return None
    _, options = parse_narration_and_options(f"\n\nOPTIONS:\n{response}", option_count=option_count)
    if not options:
        return None
    return response.strip()


def _section_footer(ctx: dict) -> str:
    story = ctx["story"]
    protagonist = ctx["state"]["protagonist"]
    endgame = ctx["state"]["plot"]["endgame"]
    narration_cfg = story.get("narration", {})
    scene_length = narration_cfg.get("scene_length", {})
    scene_min = scene_length.get("min", DEFAULT_SCENE_WORD_MIN)
    scene_max = scene_length.get("max", DEFAULT_SCENE_WORD_MAX)
    # 5.1: option_pov defaults to narration.pov (v1 hardcoded first-person options against
    # whatever the narration's own pov was, which happened to work only because both
    # existing stories are second-person narrated with first-person option prose - now
    # derived, and independently overridable). option_count likewise defaults to 3, but is
    # a real per-story dial - threaded through parse_narration_and_options's own
    # minimum-count fallback too (see app.py's call sites).
    option_pov = narration_cfg.get("option_pov") or narration_cfg.get("pov", "first-person")
    option_count = narration_cfg.get("option_count", 3)
    stats_instruction = (
        "\nThe PLAYER line's Stats are for your own internal reasoning only - never state a "
        "stat's raw numeric value to the player. Reflect what it means narratively instead "
        "(strain, fatigue, confidence, risk) without quoting the number."
        if protagonist.get("stats") else ""
    )

    if endgame["requested"]:
        instruction_footer = (
            f"Continue the story based on the player's next action, moving it toward its "
            f"conclusion. Narrate the scene itself in {scene_min}-{scene_max} words."
        )
    else:
        instruction_footer = (
            f"Continue the story based on the player's next action. Narrate the scene itself in "
            f"{scene_min}-{scene_max} words. "
            f"{_options_block_instruction(option_count, option_pov)}"
        )

    return f"""Stay strictly within the established world, tone, and rules above.{stats_instruction}
You may lightly mark up emphasis in your prose using exactly these three markers, used
sparingly (most sentences should have none): **text** for bold, *text* for italic
(e.g. internal thought or stressed words), __text__ for underline. Do not nest them,
and do not use any other markdown (no headers, lists, links, code, or single/double
underscores for anything other than underline).
{instruction_footer}"""


SECTIONS = [
    _section_identity,
    _section_style,
    _section_world_rules,
    _section_setting,
    _section_factions,
    _section_roster,
    _section_main_thread,
    _section_tracked_entity,
    _section_story_so_far,
    _section_recent,
    _section_pacing_or_endgame,
    _section_scene,
    _section_revelations,
    _section_protagonist,
    _section_footer,
]


def build_system_prompt(ctx: dict) -> str:
    body = "\n\n".join(rendered for section in SECTIONS if (rendered := section(ctx)) is not None)
    return f"You are the narrator of an interactive story.\n\n{body}\n"


_OPTIONS_HEADING_RE = re.compile(r"OPTIONS\s*:\s*\n?", re.IGNORECASE)
_OPTION_LINE_RE = re.compile(r"^\s*\d+[.)]\s*(.+?)\s*\|\|\s*(.+)$", re.MULTILINE)


def parse_narration_and_options(text: str, option_count: int = 3):
    """Splits a narration response from its trailing "OPTIONS:" block (see the
    instruction_footer format required in build_system_prompt) into
    (narration_without_options, [option_1, ..., option_N]), where each option is
    {"action": <short third-person label, shown on the choice button>, "prose": <prose
    rendition in narration.option_pov, shown as a preview and - if this option is picked -
    submitted as the player's actual action, so what lands in the novel is the prose, not
    the menu label>}.

    option_count is the story's narration.option_count (default 3, see build_system_prompt/
    _section_footer, which asks the model for exactly that many) - both the minimum-count
    fallback and the cap on how many are kept.

    Falls back to (text, []) whenever a well-formed option_count-option "action || prose"
    block isn't found - the fixed opening scene, an endgame turn (which is told not to
    produce one), or a model that ignored the format. Callers should treat an empty list as
    "no buttons, free-text action only", not an error."""
    match = _OPTIONS_HEADING_RE.search(text)
    if not match:
        return text.strip(), []
    narration = text[:match.start()].rstrip()
    options = [
        {"action": action.strip(), "prose": prose.strip()}
        for action, prose in _OPTION_LINE_RE.findall(text[match.end():])
        if action.strip() and prose.strip()
    ][:option_count]
    if len(options) < option_count:
        return text.strip(), []
    return narration, options


def split_turn_entry(entry: str):
    """(player_action, narration) for one history entry. Entries are either
    'Player: ...\\nNarrator: ...' (a normal turn - player_action is the action that led to
    this scene) or 'Narrator: ...' (the synthetic opening-scene entry, which has no
    preceding player action - player_action is None). Shared by app.py's turn rendering and
    export_story.py's plain-text export, so the "Player: "/"Narrator: " wire format defined
    by take_turn/apply_opening_name only needs to be parsed in one place."""
    marker = "Narrator: "
    idx = entry.find(marker)
    narration = entry[idx + len(marker):] if idx != -1 else entry
    player_action = None
    if idx != -1 and entry.startswith("Player: "):
        player_action = entry[len("Player: "):idx].rstrip("\n")
    return player_action, narration


def all_turns(ctx: dict) -> list:
    """The complete chronological turn sequence for a save, oldest first - full_transcript
    (unbounded, disk-only, only populated once turns roll out of recent_turns) followed by
    recent_turns (the live window). See CLAUDE.md's 'Keeping LLM Context Bounded' section."""
    return ctx["state"]["history"].get("full_transcript", []) + ctx["state"]["history"]["recent_turns"]


def export_narrative(ctx: dict, include_actions: bool = False) -> str:
    """Plain-text export of just the story the LLM generated - the Narrator half of every
    turn, in order - not the player's typed actions (unless include_actions is set) or any
    of the surrounding plot/character/pacing state. Used by both export_story.py's CLI and
    app.py's /export route."""
    title = ctx["story"]["meta"]["title"]
    lines = [title, "=" * len(title)]
    for entry in all_turns(ctx):
        player_action, narration = split_turn_entry(entry)
        lines.append("")
        if include_actions and player_action:
            lines.append(f"> {player_action}")
            lines.append("")
        lines.append(narration.strip())
    return "\n".join(lines) + "\n"


def _enforce_word_cap(text: str, max_words: int) -> str:
    """SUMMARY_MAX_WORDS is an instruction the model overshoots - 2,912 words against a
    2,000 cap after 24 turns (docs/PHASE_0_GATE_REPORT.md §1). Truncating here is what makes
    the documented bound real rather than aspirational. The trim back to a sentence boundary
    matters because this text is fed verbatim into every later prompt and into the next
    rollover's CURRENT SUMMARY, where a mid-clause cut would compound."""
    words = text.split()
    if len(words) <= max_words:
        return text
    truncated = " ".join(words[:max_words])
    cut = max(truncated.rfind(". "), truncated.rfind("! "), truncated.rfind("? "))
    return truncated[:cut + 1] if cut > 0 else truncated


def update_state_after_turn(
    ctx: dict,
    player_action: str,
    ai_response: str,
    user_id: str = DEFAULT_USER_ID,
    story_slug: str = DEFAULT_STORY_SLUG,
):
    turn_text = f"Player: {player_action}\nNarrator: {ai_response}"
    ctx["state"]["history"]["recent_turns"].append(turn_text)

    pacing_state = ctx["state"]["pacing"]
    pacing_state["turn_count"] += 1
    pacing_state["turns_since_nudge"] += 1
    pacing_state["turns_since_act_check"] = pacing_state.get("turns_since_act_check", 0) + 1

    # Separate state-update pass: subplot progress, flags, memory fragments, entity contact
    update_progress_from_turn(ctx, player_action, ai_response)

    # Retire non-pinned flags that have aged out of the recent-turns window
    archive_stale_flags(ctx)

    # Check subplot completion status, then keep the pool topped up
    status = check_subplot_status(ctx)
    for _ in status["completed"]:
        generate_new_subplot(ctx)

    # See if the current act has narratively resolved and needs a successor
    check_and_advance_act(ctx)

    # Roll oldest turns into compressed summary once over the limit
    history = ctx["state"]["history"]
    if len(history["recent_turns"]) > RECENT_TURN_LIMIT:
        overflow = history["recent_turns"][:-RECENT_TURN_LIMIT]
        history["recent_turns"] = history["recent_turns"][-RECENT_TURN_LIMIT:]

        # Verbatim, unbounded, disk-only archive of every turn's full text once it's about
        # to be lossy-compressed away - never read back into a prompt, so it costs nothing
        # in LLM context no matter how long the game runs.
        history.setdefault("full_transcript", []).extend(overflow)

        summary_prompt = f"""Update the running story summary below by folding in these new events.
Preserve key facts, decisions, consequences, and anything that might matter later (open
threads, foreshadowing, unresolved stakes). The result must stay under {SUMMARY_MAX_WORDS}
words total, so drop lower-priority detail as needed rather than just appending.

CURRENT SUMMARY: {history["compressed_summary"] or "(none yet)"}

NEW EVENTS:
{chr(10).join(overflow)}

Respond with ONLY the updated summary text, under {SUMMARY_MAX_WORDS} words, no preamble."""
        updated_summary = _timed(
            "summary_rollover",
            lambda: call_llm(summary_prompt, model=TIER_AB_MODEL, provider=TIER_AB_PROVIDER),
            model=TIER_AB_MODEL,
        )
        history["compressed_summary"] = _enforce_word_cap(
            updated_summary.strip(), SUMMARY_MAX_WORDS
        )

    state_store.save_state(ctx, user_id, story_slug)


def opening_needs_name_capture(ctx: dict) -> bool:
    """5.8: a story opts out of diegetic name capture by authoring opening_scene as
    {"narration": "..."} instead of the {"narration_before_name", "narration_after_name"}
    pair - for an established/historical protagonist whose name isn't the player's to
    choose. True (the default) covers every template written before this option existed,
    since "narration" simply won't be a key there."""
    return "narration" not in ctx["story"]["plot"]["opening_scene"]


def apply_fixed_opening(ctx: dict) -> str:
    """5.8 counterpart to apply_opening_name for a story with no name capture: applies
    protagonist.default_name directly (still available as {player_name} in the authored
    text, for a line that wants to state it), marks the opening played, and logs the
    synthetic turn for LLM continuity - same contract as apply_opening_name otherwise."""
    default_name = ctx["story"]["protagonist"].get("default_name", "Traveller")
    ctx["state"]["protagonist"]["name"] = default_name

    opening = ctx["story"]["plot"]["opening_scene"]
    text = opening["narration"].replace("{player_name}", default_name)
    ctx["state"]["plot"]["opening_played"] = True
    ctx["state"]["history"]["recent_turns"].append(f"Narrator: {text}")
    return text


def apply_opening_name(ctx: dict, raw_name: str) -> str:
    """Pure state mutation, no I/O: applies a (possibly blank) name to the opening
    scene - sets protagonist.name, substitutes it into narration_after_name, marks the
    opening played, and logs the synthetic turn for LLM continuity. Returns the
    after-name narration text. Shared by the CLI opening (run_opening_scene, which
    wraps this with print/input) and the web /play route (which wraps it with a
    render/form instead)."""
    default_name = ctx["story"]["protagonist"].get("default_name", "Traveller")
    name = (raw_name or "").strip() or default_name
    ctx["state"]["protagonist"]["name"] = name

    opening = ctx["story"]["plot"]["opening_scene"]
    after_name = opening["narration_after_name"].replace("{player_name}", name)
    ctx["state"]["plot"]["opening_played"] = True
    full_opening_text = f"{opening['narration_before_name']}\n\n{after_name}"
    ctx["state"]["history"]["recent_turns"].append(f"Narrator: {full_opening_text}")
    return after_name


def next_pending_creation_step(ctx: dict) -> dict:
    """The first character-creation step (from the story's template-authored
    character_creation list - see apply_creation_choice) the player hasn't completed yet,
    or None once they're all done (or the story doesn't define any). A story opts into
    this mechanic entirely by authoring that list - stories/new_babel/template.json has
    a "class" step and a "starting_place" step, in that order; a story that doesn't
    define character_creation at all (e.g. the cozy-mystery example story, where a class/
    race pick wouldn't fit the genre) skips this entirely, same as before the mechanic
    existed. Shared by the CLI loop below and app.py's play() route so both walk the same
    steps in the same order without duplicating the "what's next" logic."""
    choices = ctx["state"]["protagonist"].get("creation_choices", {})
    for step in ctx["story"].get("character_creation", []):
        if step["key"] not in choices:
            return step
    return None


def apply_creation_choice(ctx: dict, step_key: str, option_id: str) -> dict:
    """Pure state mutation, no I/O: applies one character-creation pick (step_key must
    match a step in ctx["story"]["character_creation"], e.g. "class" or "starting_place").
    Records protagonist.creation_choices[step_key] = option_id, and merges the chosen
    option's starting_stats (if it has any - a flavor-only step like "starting_place"
    typically doesn't) into protagonist.stats. Later steps merge on top of earlier ones for
    any stat name both happen to touch, in step order. Returns the chosen option dict, or
    None for an unrecognized step_key/option_id (a malformed/replayed POST) - callers must
    check for that rather than assuming the pick always succeeds."""
    steps = {s["key"]: s for s in ctx["story"].get("character_creation", [])}
    step = steps.get(step_key)
    if not step:
        return None
    options = {o["id"]: o for o in step["options"]}
    chosen = options.get(option_id)
    if not chosen:
        return None
    ctx["state"]["protagonist"].setdefault("creation_choices", {})[step_key] = option_id
    if chosen.get("starting_stats"):
        ctx["state"]["protagonist"].setdefault("stats", {}).update(chosen["starting_stats"])
    return chosen


def run_opening_scene(user_id: str = DEFAULT_USER_ID, story_slug: str = DEFAULT_STORY_SLUG):
    """Plays the fixed, hand-authored opening (ctx["story"]["plot"]["opening_scene"]) - the
    one constant beat every playthrough starts from, everything after branches from here.
    Captures the protagonist's name diegetically, in-fiction, as part of the intake scene
    itself, rather than a raw pre-game prompt, then walks whatever character-creation steps
    the story defines (see next_pending_creation_step), in order. Each part no-ops
    independently once already done, so resuming a save doesn't replay any of it."""
    ctx = state_store.load_state(user_id, story_slug)

    if not ctx["state"]["plot"]["opening_played"]:
        if opening_needs_name_capture(ctx):
            print(ctx["story"]["plot"]["opening_scene"]["narration_before_name"])
            raw_name = input("\n> ")
            after_name = apply_opening_name(ctx, raw_name)
            print(f"\n{after_name}")
        else:
            print(apply_fixed_opening(ctx))
        state_store.save_state(ctx, user_id, story_slug)

    step = next_pending_creation_step(ctx)
    while step:
        print(f"\n{step.get('label', step['key'].title())}:")
        options = step["options"]
        for i, opt in enumerate(options, 1):
            tagline = f" - {opt['tagline']}" if opt.get("tagline") else ""
            print(f"{i}. {opt['name']}{tagline}")
        while True:
            choice = input("\n> ").strip()
            if choice.isdigit() and 1 <= int(choice) <= len(options):
                chosen = apply_creation_choice(ctx, step["key"], options[int(choice) - 1]["id"])
                print(f"\n{step.get('label', step['key'].title())}: {chosen['name']}.")
                break
            print(f"Please enter a number from 1 to {len(options)}.")
        state_store.save_state(ctx, user_id, story_slug)
        step = next_pending_creation_step(ctx)


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
    ctx: dict,
    player_action: str,
    pre_turn_snapshot: dict,
    user_id: str,
    story_slug: str,
) -> bool:
    """Shared by take_turn and regenerate_last_turn: calls the LLM for player_action against
    the given ctx, applies the resulting state-update pass, and stashes pre_turn_snapshot
    (a deep copy of ctx["state"] from just before this turn) so a later regenerate_last_turn
    call can restore to exactly this point and re-roll. Returns True once the story has
    concluded."""
    prompt = build_system_prompt(ctx) + f"\n\nPlayer action: {player_action}\n\nNarrator:"
    ai_response = _timed("narration", lambda: call_llm(prompt), model=TIER_AB_MODEL)

    # A non-endgame turn is required to end with an OPTIONS block (endgame turns are
    # explicitly told not to produce one - see _section_footer). If the model skipped it,
    # parse_narration_and_options's normal fallback would silently leave the player with
    # free-text-only input for this turn; try one cheap, targeted follow-up call for just
    # the missing block before accepting that degradation.
    if not ctx["state"]["plot"]["endgame"]["requested"]:
        option_count = ctx["story"].get("narration", {}).get("option_count", 3)
        _, options = parse_narration_and_options(ai_response, option_count=option_count)
        if not options:
            missing_block = generate_missing_options(ctx, ai_response)
            if missing_block:
                ai_response = f"{ai_response}\n\n{missing_block}"

    print(ai_response)

    update_state_after_turn(ctx, player_action, ai_response, user_id, story_slug)

    # Bounded to exactly one level (this turn only, overwriting whatever was pending before) -
    # regenerating re-rolls the latest scene, it isn't a multi-step undo stack.
    ctx["state"]["pending_regenerate"] = {
        "state": pre_turn_snapshot,
        "player_action": player_action,
    }

    if ctx["state"]["plot"]["endgame"]["requested"] and "THE END" in ai_response:
        ctx["state"]["plot"]["endgame"]["concluded"] = True

    state_store.save_state(ctx, user_id, story_slug)
    return ctx["state"]["plot"]["endgame"]["concluded"]


def take_turn(
    player_action: str,
    user_id: str = DEFAULT_USER_ID,
    story_slug: str = DEFAULT_STORY_SLUG,
) -> bool:
    """Runs one turn of the story. Returns True once the story has concluded (THE END)."""
    _status_ctx.user_id, _status_ctx.story_slug = user_id, story_slug
    try:
        ctx = state_store.load_state(user_id, story_slug)

        if is_end_story_command(player_action) and not ctx["state"]["plot"]["endgame"]["requested"]:
            final_arc = handle_end_story_request(ctx)
            state_store.save_state(ctx, user_id, story_slug)
            print(f"\n[The story is moving toward its conclusion: {final_arc['title']}]\n")

        pre_turn_snapshot = copy.deepcopy(ctx["state"])
        pre_turn_snapshot.pop("pending_regenerate", None)
        return _generate_and_apply_turn(ctx, player_action, pre_turn_snapshot, user_id, story_slug)
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
        ctx = state_store.load_state(user_id, story_slug)
        pending = ctx["state"].get("pending_regenerate")
        if not pending:
            return False

        restored_state = pending["state"]
        player_action = pending["player_action"]
        pre_turn_snapshot = copy.deepcopy(restored_state)
        restored_ctx = {"story": ctx["story"], "state": restored_state}
        return _generate_and_apply_turn(restored_ctx, player_action, pre_turn_snapshot, user_id, story_slug)
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

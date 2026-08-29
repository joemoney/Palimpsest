import os
import time
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, redirect, render_template, request, session, url_for

import plot_manager
import state_store
import story_engine
import subplot_manager

load_dotenv()

# frontend/ (Jinja templates) and static/ live at the repo root, not alongside this file -
# Flask's default (relative to this module's own location) would look under backend/
# instead, so both are pointed back explicitly. template_folder is renamed from Flask's
# usual "templates" purely by convention (frontend/ pairs with backend/) - Flask itself
# doesn't care what the directory is called. static_url_path is left at the default
# "/static" so existing url_for('static', ...) calls (e.g. htmx.min.js in base.html)
# don't need to change.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
app = Flask(
    __name__,
    template_folder=os.path.join(_REPO_ROOT, "frontend"),
    static_folder=os.path.join(_REPO_ROOT, "static"),
)

FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY")
if not FLASK_SECRET_KEY:
    raise ValueError("FLASK_SECRET_KEY not found in .env file")
app.secret_key = FLASK_SECRET_KEY


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


INITIAL_TURNS_SHOWN = 3


def _split_turn_entry(entry: str):
    """(player_action, narration) for one history_log entry. Entries are either
    'Player: ...\\nNarrator: ...' (a normal turn - player_action is the action that led to
    this scene) or 'Narrator: ...' (the synthetic opening-scene entry, which has no
    preceding player action - player_action is None)."""
    marker = "Narrator: "
    idx = entry.find(marker)
    narration = entry[idx + len(marker):] if idx != -1 else entry
    player_action = None
    if idx != -1 and entry.startswith("Player: "):
        player_action = entry[len("Player: "):idx].rstrip("\n")
    return player_action, narration


def _all_turns(state: dict) -> list:
    """The complete chronological turn sequence for a save, oldest first - full_transcript
    (unbounded, disk-only, only populated once turns roll out of recent_turns) followed by
    recent_turns (the live window). See CLAUDE.md's 'Keeping LLM Context Bounded' section."""
    return state["history_log"].get("full_transcript", []) + state["history_log"]["recent_turns"]


def _render_turn(entry: str, index: int, animate: bool, reveal_from: int = 0) -> dict:
    """Splits and options-strips one turn for display. parse_narration_and_options is run
    on every turn, not just the latest - the raw stored text is the LLM's full response
    including any trailing 'OPTIONS:' block, so an older turn left unstripped would show
    that block as literal text. options is only meaningful (and only rendered by
    _controls.html) for the single latest turn - see _latest_rendered_turn.

    reveal_from is a character offset into narration: text before it renders instantly
    (already shown to the player elsewhere), only the remainder is typewriter-animated.
    Only ever non-zero for the opening-scene turn - see play()'s comment on why."""
    player_action, raw_narration = _split_turn_entry(entry)
    narration, options = story_engine.parse_narration_and_options(raw_narration)
    return {
        "player_action": player_action, "narration": narration, "animate": animate,
        "index": index, "options": options, "reveal_from": reveal_from,
    }


def _latest_rendered_turn(state: dict, animate: bool) -> dict:
    all_turns = _all_turns(state)
    return _render_turn(all_turns[-1], len(all_turns) - 1, animate)


def _scene_and_controls_response(state: dict, story_slug: str) -> str:
    """Shared by take_turn and regenerate_turn: renders the latest turn as a _scene_block
    fragment plus an out-of-band _controls update, so a single htmx swap both shows the new
    scene and replaces the old choices with fresh ones (or removes them, at endgame)."""
    turn = _latest_rendered_turn(state, animate=True)
    mode = "concluded" if state["plot"]["endgame"]["concluded"] else "playing"
    scene_html = render_template("_scene_block.html", turn=turn, story_slug=story_slug)
    controls_html = render_template(
        "_controls.html", turn=turn, options=turn["options"], mode=mode, story_slug=story_slug
    )
    # class must be repeated here: an hx-swap-oob replacement swaps the whole element
    # (attributes included), so omitting it would drop base.html's .post-narration layout
    # and unhide the controls before the freshly-animated narration above them finishes.
    # Tag matches play.html's <fieldset id="controls"> (not a <div>) so hx-disabled-elt's
    # native disabled-cascade to every descendant button/textarea keeps working turn over
    # turn, not just on the very first swap.
    return scene_html + (
        f'<fieldset id="controls" class="post-narration hidden" hx-swap-oob="true">'
        f'{controls_html}</fieldset>'
    )


def _turn_in_progress_response():
    """Shared by take_turn/regenerate_turn: refuses to start a second turn for a save that
    already has one running, rather than racing it - reuses state_store's turn-status beacon
    (written by story_engine.py's _timed(), otherwise just a display hint for the busy
    indicator) as a cheap in-flight lock. Guards against a duplicate POST /api/turn actually
    doing anything - a proxy/tunnel retry on a slow request, a fast double-tap outrunning
    hx-disabled-elt's disable, etc. - which previously produced two independent, fully valid
    take_turn() calls against a state that had moved on between them, silently discarding
    the player's intended action. 409, not 2xx: htmx doesn't swap a non-2xx response, so the
    scene/choices are left untouched and only the turn already in flight ends up landing."""
    return "A turn is already in progress for this story - please wait for it to finish.", 409


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        user_id = state_store.verify_login(username, password)
        if user_id is None:
            return render_template("login.html", error="Incorrect username or password.")
        session["user_id"] = user_id
        session["username"] = username
        return redirect(url_for("stories"))
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    return redirect(url_for("stories"))


@app.route("/stories")
@login_required
def stories():
    return render_template("stories.html", stories=state_store.list_stories())


@app.route("/help")
@login_required
def help_page():
    # story_slug is optional here (an ?args query param, not part of the route) - the "?"
    # icon is reachable from anywhere in the app, including before any story is loaded
    # (base.html shows it whenever a session exists, regardless of story_slug). When it IS
    # present (base.html's topbar link forwards the current page's story_slug whenever one
    # exists), it both lets help.html render a "Back to Play" link and, via the same
    # story_slug being in this render's own context, makes base.html's own topbar show its
    # usual story-scoped icons (back-to-stories, Manage dropdown) on this page too. See
    # README.md's "User Manual" section, which this mirrors - keep the two in sync when
    # either changes.
    return render_template("help.html", story_slug=request.args.get("story_slug"))


@app.route("/play/<story_slug>", methods=["GET", "POST"])
@login_required
def play(story_slug):
    user_id = session["user_id"]
    state = state_store.load_state(user_id, story_slug)
    story_title = state["meta"]["title"]

    if not state["plot"]["opening_scene"]["played"]:
        if request.method == "POST":
            story_engine.apply_opening_name(state, request.form.get("name", ""))
            state_store.save_state(state, user_id, story_slug)
            return redirect(url_for("play", story_slug=story_slug, fresh=1))
        narration = state["plot"]["opening_scene"]["narration_before_name"]
        return render_template(
            "play.html", story_title=story_title, story_slug=story_slug, narration=narration,
            options=[], player_action=None, mode="name_entry", animate=False
        )

    # A story opts into this entirely by authoring a top-level character_creation list (an
    # ordered sequence of steps - e.g. new_babel's "class" then "starting_place") - absent/
    # empty for a story that doesn't use the mechanic (e.g. the cozy-mystery example story),
    # so this whole block is a no-op there. Independent of the opening_scene.played gate
    # above (not nested inside "if not played") so it still applies correctly to a save
    # whose opening already played but hasn't completed every step yet. One request handles
    # exactly one step; a story with multiple steps naturally chains through them one screen
    # at a time, since each redirect below re-enters play() and next_pending_creation_step
    # picks up wherever the player left off.
    step = story_engine.next_pending_creation_step(state)
    if step:
        error = None
        if request.method == "POST":
            chosen = story_engine.apply_creation_choice(state, step["key"], request.form.get("option_id", ""))
            if chosen is not None:
                state_store.save_state(state, user_id, story_slug)
                return redirect(url_for("play", story_slug=story_slug, fresh=1))
            error = "Please choose one of the options below."
        prompt = step.get("prompt") or f"{step.get('label', step['key'].title())}: choose one."
        return render_template(
            "creation_step.html", story_title=story_title, story_slug=story_slug,
            step=step, prompt=prompt, error=error,
        )

    # Only the request immediately following the name-entry submission carries ?fresh=1 -
    # any other GET (resuming a save, refreshing, navigating back from the story picker)
    # renders every visible scene instantly instead of replaying the reveal animation.
    animate = request.args.get("fresh") == "1"
    mode = "concluded" if state["plot"]["endgame"]["concluded"] else "playing"
    all_turns = _all_turns(state)
    total = len(all_turns)
    oldest_index = max(0, total - INITIAL_TURNS_SHOWN)
    # Turn 0's stored text is "narration_before_name\n\nnarration_after_name" (see
    # apply_opening_name) - the before-name half was already shown, unanimated, on the
    # name-entry form itself, so on this first fresh=1 load only the after-name half
    # should type out. Without this offset the typewriter replays the whole opening from
    # scratch, including text the player just read.
    before_name_len = len(state["plot"]["opening_scene"]["narration_before_name"])
    initial_turns = [
        _render_turn(
            entry, idx, animate=(animate and idx == total - 1),
            reveal_from=(before_name_len + 2 if idx == 0 else 0),
        )
        for idx, entry in enumerate(all_turns[oldest_index:], start=oldest_index)
    ]
    latest = initial_turns[-1] if initial_turns else {"options": []}
    return render_template(
        "play.html", story_title=story_title, story_slug=story_slug,
        initial_turns=initial_turns, oldest_index=oldest_index, has_older=oldest_index > 0,
        turn=latest, options=latest["options"], mode=mode, animate=animate,
    )


@app.route("/play/<story_slug>/api/status", methods=["GET"])
@login_required
def turn_status(story_slug):
    """Polled by play.html's busy indicator while a /api/turn or /api/regenerate request is
    in flight, to show which of that turn's (possibly several) sequential LLM calls is
    currently running (see story_engine.py's STATUS_LABELS/_timed) instead of a generic
    "Working..." for the whole duration. Cheap: no LLM call, no state_store save-file lock -
    just a small best-effort status file read (state_store.read_turn_status).

    `progress` estimates how far into the current step the request is, as a 0-99 percent
    of that step's rolling P50 (median) duration (state_store.p50_duration, falling back to
    story_engine.DEFAULT_STEP_ESTIMATE_SECONDS until real samples exist for this label -
    otherwise a fresh deploy would show no progress bar at all for anyone's first several
    turns) - capped below 100 since the step is, by definition, still running while this
    route can be polled; the UI's progress-bar fill only ever reaches 100% when the step
    actually finishes (the next poll reports a different label, or the request completes).
    P50 rather than P90 - a typical call fills the bar at roughly the pace it actually
    completes, at the cost of a slower-than-typical call sitting at the 99% cap for a
    while longer before it actually finishes."""
    user_id = session["user_id"]
    status = state_store.read_turn_status(user_id, story_slug)
    if status is None:
        return {"label": None, "progress": None}
    label_key = status["label_key"]
    estimate = state_store.p50_duration(label_key) or story_engine.DEFAULT_STEP_ESTIMATE_SECONDS.get(label_key)
    progress = min(99, round((time.time() - status["started_at"]) / estimate * 100)) if estimate else None
    return {"label": story_engine.STATUS_LABELS.get(label_key, label_key), "progress": progress}


@app.route("/play/<story_slug>/api/turn", methods=["POST"])
@login_required
def take_turn(story_slug):
    user_id = session["user_id"]
    action = request.form.get("action", "")
    if not action.strip():
        return ""  # matches the textarea's required attribute - nothing to submit
    if state_store.read_turn_status(user_id, story_slug) is not None:
        return _turn_in_progress_response()
    try:
        story_engine.take_turn(action, user_id, story_slug)
    except story_engine.LLMUnavailableError as e:
        # call_llm always runs before any state is saved for the turn, so nothing was
        # lost - htmx doesn't swap on a non-2xx response, so #scene-list/#controls are
        # left exactly as they were and the player can just retry the same choice.
        return str(e), 503
    state = state_store.load_state(user_id, story_slug)
    return _scene_and_controls_response(state, story_slug)


@app.route("/play/<story_slug>/api/regenerate", methods=["POST"])
@login_required
def regenerate_turn(story_slug):
    user_id = session["user_id"]
    if state_store.read_turn_status(user_id, story_slug) is not None:
        return _turn_in_progress_response()
    try:
        story_engine.regenerate_last_turn(user_id, story_slug)
    except story_engine.LLMUnavailableError as e:
        return str(e), 503
    state = state_store.load_state(user_id, story_slug)
    return _scene_and_controls_response(state, story_slug)


@app.route("/play/<story_slug>/api/history", methods=["GET"])
@login_required
def turn_history(story_slug):
    user_id = session["user_id"]
    state = state_store.load_state(user_id, story_slug)
    all_turns = _all_turns(state)
    total = len(all_turns)
    before = max(0, min(_int_or_none(request.args.get("before")) or 0, total))
    count = _int_or_none(request.args.get("count")) or 3
    start = max(0, before - count)
    batch = [
        _render_turn(entry, idx, animate=False)
        for idx, entry in enumerate(all_turns[start:before], start=start)
    ]
    html = "".join(
        render_template("_scene_block.html", turn=t, story_slug=story_slug) for t in batch
    )
    if start > 0:
        # Chains the "reverse infinite scroll" pattern: the response replaces the old
        # sentinel with [older blocks + a fresh sentinel above them], so the topmost
        # sentinel always points at the next-older batch. Omitted once nothing is left
        # before start, which naturally ends the chain.
        html = render_template(
            "_scroll_sentinel.html", story_slug=story_slug, oldest_index=start
        ) + html
    return html


def _int_or_none(value):
    return int(value) if value else None


@app.route("/play/<story_slug>/plot", methods=["GET", "POST"])
@login_required
def plot_manager_view(story_slug):
    user_id = session["user_id"]
    state = state_store.load_state(user_id, story_slug)

    if request.method == "POST":
        command = request.form.get("command", "")
        if command == "add-act":
            plot_manager.add_act(
                state, request.form.get("title", ""), request.form.get("description", ""),
                position=_int_or_none(request.form.get("position")),
                optional=bool(request.form.get("optional")),
            )
        elif command == "modify-act":
            kwargs = {}
            if request.form.get("title"):
                kwargs["title"] = request.form["title"]
            if request.form.get("description"):
                kwargs["description"] = request.form["description"]
            plot_manager.modify_act(state, int(request.form.get("act_number", 0)), **kwargs)
        elif command == "pivot":
            plot_manager.pivot_main_plot(
                state, request.form.get("title", ""), request.form.get("description", ""),
                request.form.get("reason", ""),
            )
        elif command == "add-emergent":
            plot_manager.add_emergent_direction(
                state, request.form.get("title", ""), request.form.get("description", "")
            )
        elif command == "promote-emergent":
            plot_manager.promote_emergent_to_act(
                state, int(request.form.get("index", 0)), _int_or_none(request.form.get("position"))
            )
        elif command == "create-alt":
            plot_manager.create_alternate_thread(
                state, request.form.get("thread_id", ""), request.form.get("title", ""),
                request.form.get("description", ""),
            )
        elif command == "focus":
            plot_manager.toggle_thread_focus(state, request.form.get("thread_id") or None)
        elif command == "add-goal":
            plot_manager.add_player_goal(state, request.form.get("goal", ""))
        elif command == "add-theme":
            plot_manager.add_emerging_theme(state, request.form.get("theme", ""))
        state_store.save_state(state, user_id, story_slug)
        return redirect(url_for("plot_manager_view", story_slug=story_slug))

    return render_template(
        "plot_manager.html", story_title=state["meta"]["title"], story_slug=story_slug, plot=state["plot"]
    )


@app.route("/play/<story_slug>/subplots", methods=["GET", "POST"])
@login_required
def subplot_manager_view(story_slug):
    user_id = session["user_id"]
    state = state_store.load_state(user_id, story_slug)

    if request.method == "POST":
        command = request.form.get("command", "")
        if command == "progress":
            subplot_manager.update_subplot_progress(
                state, request.form.get("subplot_id", ""), int(request.form.get("delta", 0))
            )
        elif command == "activate":
            subplot_manager.activate_subplot(state, request.form.get("subplot_id", ""))
        elif command == "modify-subplot":
            kwargs = {}
            if request.form.get("title"):
                kwargs["title"] = request.form["title"]
            if request.form.get("description"):
                kwargs["description"] = request.form["description"]
            if request.form.get("priority"):
                kwargs["priority"] = request.form["priority"]
            if request.form.get("ties_to_main_plot"):
                kwargs["ties_to_main_plot"] = request.form["ties_to_main_plot"]
            subplot_manager.modify_subplot(state, request.form.get("subplot_id", ""), **kwargs)
        elif command == "advance-act":
            subplot_manager.advance_act(state)
        elif command == "reveal":
            subplot_manager.reveal_memory_fragment(state, request.form.get("fragment_id", ""))
        state_store.save_state(state, user_id, story_slug)
        return redirect(url_for("subplot_manager_view", story_slug=story_slug))

    return render_template(
        "subplot_manager.html", story_title=state["meta"]["title"], story_slug=story_slug,
        plot=state["plot"], player=state["player"]
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)

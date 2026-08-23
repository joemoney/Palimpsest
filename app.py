import os
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, redirect, render_template, request, session, url_for

import state_store
import story_engine

load_dotenv()

app = Flask(__name__)

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


def _latest_turn(state: dict):
    """(player_action, narration) for the most recent turn, for display. recent_turns
    entries are either 'Player: ...\\nNarrator: ...' (a normal turn - player_action is the
    action that led to this scene) or 'Narrator: ...' (the synthetic opening-scene entry,
    which has no preceding player action - player_action is None)."""
    turns = state["history_log"]["recent_turns"]
    if not turns:
        return None, ""
    last = turns[-1]
    marker = "Narrator: "
    idx = last.find(marker)
    narration = last[idx + len(marker):] if idx != -1 else last
    player_action = None
    if idx != -1 and last.startswith("Player: "):
        player_action = last[len("Player: "):idx].rstrip("\n")
    return player_action, narration


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
            "play.html", story_title=story_title, narration=narration, options=[],
            player_action=None, mode="name_entry", animate=False
        )

    if request.method == "POST":
        action = request.form.get("action", "")
        if action.strip():
            story_engine.take_turn(action, user_id, story_slug)
        return redirect(url_for("play", story_slug=story_slug, fresh=1))

    # Only the request immediately following a form submission (name or action) carries
    # ?fresh=1 - any other GET (resuming a save, refreshing, navigating back from the story
    # picker) renders the scene instantly instead of replaying the reveal animation.
    animate = request.args.get("fresh") == "1"
    state = state_store.load_state(user_id, story_slug)
    mode = "concluded" if state["plot"]["endgame"]["concluded"] else "playing"
    player_action, raw_narration = _latest_turn(state)
    narration, options = story_engine.parse_narration_and_options(raw_narration)
    return render_template(
        "play.html", story_title=story_title, narration=narration, options=options,
        player_action=player_action, mode=mode, animate=animate
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)

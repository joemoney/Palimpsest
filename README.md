# Palimpsest

An AI-powered Choose-Your-Own-Adventure engine (inspired by apps like OOC)
where an LLM narrates a branching story constrained by a persistent
world-state JSON object. The goal is to keep AI-generated narrative
on-theme and coherent over long sessions without hand-scripting every
branch.

## Stories

This engine is story-agnostic — `stories/<slug>/` holds one story's seed
content and its own `README.md` with that story's pitch, setting, and design
rationale. Adding a new story is a content change (drop in
`stories/<new-slug>/template.json` + `README.md`), not a code change — see
"Multi-User, Multi-Story Architecture" in `CLAUDE.md`.

### Public vs. private stories

This repo is public, but story *content* (setting, plot, characters — the
creative work, as opposed to the engine that runs it) isn't necessarily
meant to be. Two kinds of `stories/<slug>/` entries exist side by side:

- **`stories/example/`** — committed directly in this repo, public. *The
  Last Ferry to Millbrook*, a small cozy-mystery demo story that exists so
  this repo is runnable and demoable with nothing more than a clone — see
  [`stories/example/README.md`](stories/example/README.md). Also the
  reference to copy from when authoring a new story's `template.json`.
- **`stories/new_babel/`** — a **git submodule** pointing at a private
  companion repo, not committed directly here. *The Attention Economy*, a
  Lovecraftian-cyberpunk reincarnation story — see
  [`stories/new_babel/README.md`](stories/new_babel/README.md) once checked
  out. This repo's `.gitmodules` records the private repo's name and URL
  (so its existence is visible), but its content is only readable by
  someone with access to that repo.

A plain `git clone` of this repo leaves `stories/new_babel/` as an empty
directory. To pull it in (if you have access):
```bash
git submodule update --init --recursive
```
Without that step, the engine still runs fine against `stories/example/`
alone — `state_store.list_stories()` just won't list New Babel.

Following this same pattern for a new story of your own: keep it directly
in this repo if you're fine with it being public, or give it its own repo
and `git submodule add <url> stories/<slug>` if you want it private.

## Running It

### CLI (single local player)
```bash
pip install -r requirements.txt
python backend/story_engine.py
```
Needs a `.env` file (not checked in) with `OPENROUTER_API_KEY` (an
[OpenRouter](https://openrouter.ai/keys) key — the default LLM provider)
and, for the web interface, `FLASK_SECRET_KEY` (any random string — used to
sign session cookies). `GOOGLE_API_KEY` (a Gemini key from
[Google AI Studio](https://aistudio.google.com/apikey)) is only needed if
you set `LLM_PROVIDER=google` — Gemini is kept around for testing/debugging,
not used by default. See `CLAUDE.md`'s "Backend / Model Notes" for the
`NARRATION_MODEL`/`STATE_UPDATE_MODEL` env vars if you want to override the
default DeepSeek models.
Boots straight into `stories/example/`'s opening with no flags needed — the
CLI defaults to a local single-player save against the public example story
(`--user`/`--story` flags exist if you want to target a different one, e.g.
`--story new_babel` once you've pulled that private submodule in).

Special commands, typed at the prompt like a normal action:
- `quit` / `exit` — leave the session
- `end story` (or "end the story" / "conclude the story" / "wrap up the story") —
  begin wrapping the narrative up for good
- `steer ...` — directly reshape the plot via `backend/plot_manager.py` (see below).
  Prints a warning every time: it bypasses narration and edits plot state
  directly, so a vague command can break story coherence.

### Web (multi-user)
```bash
docker-compose up
python backend/state_store.py create-account <username> <password>   # accounts are backend-only, no signup form
```
Then log in at `http://localhost:8000/login`. `docker-compose.yml` runs both
a `web` service (the Flask app) and a `story-engine` service (an interactive
CLI session in its own container) against the same shared `data/` volume.

### Mid-Adventure Steering
`backend/plot_manager.py` lets you dynamically adjust plot structure during
play, from a separate terminal, via the in-session `steer` command above, or
from the web UI itself — the "Manage" dropdown on the play screen (top
right) has a **Plot Manager** page covering every command below as a form,
reading and writing the same save file. Run `python backend/plot_manager.py`
with no arguments for the full current CLI command list; the common ones:

```bash
python backend/plot_manager.py overview                                              # view current state
python backend/plot_manager.py add-act 'Act Title' 'Description of what happens'     # add a new act
python backend/plot_manager.py add-act 'Side Quest' 'Optional arc' --optional
python backend/plot_manager.py pivot 'New Main Goal' 'Updated description' 'Why we pivoted'
python backend/plot_manager.py add-emergent 'Corporate Conspiracy' 'Player discovered...'
python backend/plot_manager.py promote-emergent 0                                    # promote emergent -> full act
python backend/plot_manager.py create-alt 'thread_faction' 'Faction War' 'Megacorps vs underground'
python backend/plot_manager.py focus thread_faction                                  # switch primary focus
python backend/plot_manager.py focus                                                 # switch back to main
python backend/plot_manager.py add-goal 'Player wants to rescue trapped AI'
python backend/plot_manager.py add-theme 'Identity and memory'
```

Add `--user <id> --story <slug>` to any command to target a specific save
instead of the local default.

**When to reach for it**: player choices reveal a more interesting direction
than the planned acts; subplots become more compelling than the main thread;
the story runs longer than expected and needs new acts added; emergent
themes surface that warrant dedicated focus; optional/side content becomes
central. Prefer letting the LLM narrate its way to a direction change where
possible — reach for `steer` when you need to force a specific structural
change the model isn't going to arrive at on its own.

### Subplot Management
Also reachable from the same "Manage" dropdown as **Subplot Manager**, with
the same coverage as the CLI:
```bash
python backend/subplot_manager.py status                    # view all subplots + pacing state
python backend/subplot_manager.py progress subplot_001 +25   # increase progress
python backend/subplot_manager.py activate subplot_002       # start a new subplot
python backend/subplot_manager.py modify-subplot subplot_002 --description '...'  # edit title/description/priority/ties
python backend/subplot_manager.py advance-act                # manually force-complete the current act
python backend/subplot_manager.py reveal frag_0001            # surface a memory fragment
```

## User Manual
Everything below is written for a player using the web UI, not a developer -
the same content is also available in-app at `/help` once logged in (the
"?" icon in the top bar). Playing via the CLI works too (see "Running It"
above) but the two "steer"/`plot_manager.py`/`subplot_manager.py` sections
above are the CLI equivalent of the web walkthrough below.

### How to Play
1. Log in at `/login`. Accounts are created for you ahead of time (there's
   no self-service signup) - ask whoever's running the server for one.
2. Pick a story from `/stories`.
3. The first time you play a given story, you're asked to name your
   protagonist - typed in-fiction, as part of the opening scene itself,
   not a separate setup form.
4. Each turn shows the current scene, followed by up to three numbered
   choices as buttons (a short label plus the full first-person action
   it'll submit if picked) and a free-text box below them for typing your
   own action instead - "steer your own way" if none of the three fit
   what you want to do.
5. **Regenerate** - didn't like how the last scene played out? The
   regenerate button (below the most recent scene) re-rolls it with a
   fresh response to the same action, discarding the version you didn't
   like. Only ever affects the single most recent scene.
6. **The story has no fixed length** - there's no set number of acts or a
   built-in ending waiting for you. Only the first act is pre-written;
   whenever the current one feels resolved, the engine judges that for
   itself and generates the next act on the spot, with no ceiling.
   Subplots work the same way, automatically topping back up as old ones
   complete. Nothing here is scripted in advance, so don't expect a fixed
   chapter count or a natural stopping point - the story keeps going until
   you decide to end it (next).
7. **Ending the story** - type one of `end story`, `end the story`,
   `conclude the story`, or `wrap up the story` as your action. The
   narration shifts into wrapping up open threads, and the story
   concludes once it ends a response with the line `THE END` - after
   that, no further acts or subplots generate automatically (manual
   Plot/Subplot Manager edits still work, if you want to keep steering
   the finale by hand).
8. Scroll up to reread earlier scenes - older history loads in
   automatically as you scroll, no pagination to click through.
9. If the AI model is temporarily unreachable (rate limit, brief outage),
   you'll see an error message and nothing will have been lost - your
   previous scene and choices are untouched, just retry.
10. The title bar can get in the way while reading - collapse it with the
   chevron button in the top-right, and a small tab at the very top of the
   screen brings it back whenever you want it.

### How to Modify Acts and Subplots
Sometimes the story doesn't head where you want it to on its own. From the
gear icon ("Manage") in the top bar while playing a story, **Plot Manager**
and **Subplot Manager** let you edit the story's structure directly,
bypassing narration entirely. Treat these as power tools: the AI treats
whatever's here as established fact going forward, so a vague or
contradictory edit can break story coherence. Prefer letting the story
arrive at a direction change on its own where possible; reach for these
when you need to force a specific change the model isn't going to make by
itself.

**Plot Manager** (Manage → Plot Manager):
- **Add Act** - add a new act to the main story: a title and description
  of what happens, optionally marked Optional, inserted wherever you like.
- **Edit Act** - change an existing act's title or description.
- **Pivot Main Plot** - redirect the overall story goal entirely (new
  title, description, and a reason for the change).
- **Note Emergent Direction** - flag a direction the story already seems
  to be drifting toward, without committing to it as a full act yet.
- **Promote Emergent Direction to Act** - turn a previously noted
  direction into a real act once you're sure you want it.
- **Create Alternate Thread** - start a separate storyline running
  alongside the main plot.
- **Switch Focus** - change which thread (main, or an alternate) the
  narration currently follows.
- **Record Player Goal** / **Note Emerging Theme** - leave notes about
  where the story should head, without immediately acting on them.

**Subplot Manager** (Manage → Subplot Manager):
- **Adjust Progress** - nudge a subplot's completion percentage up or down.
- **Modify Subplot** - edit a subplot's title, description, priority, or
  how it ties to the main plot (leave any field blank to keep it
  unchanged).
- **Activate Subplot** - start one of the story's not-yet-started subplots.
- **Advance Act** - force the current act to complete right away; the next
  act generates automatically the next time you take a turn.
- **Reveal Memory Fragment** - manually surface one of the protagonist's
  hidden backstory fragments.

You rarely need any of this by hand - subplots regenerate automatically as
old ones complete, and acts are open-ended with no fixed count - but it's
here for when the story needs a deliberate push.

## File Structure
- `stories/<slug>/template.json` — authored seed content for one story (meta,
  world, player, characters, plot, history_log). `stories/example/` is
  committed here directly (public); `stories/new_babel/` is a private git
  submodule — see "Public vs. private stories" above. Adding a new story is
  a content change, not a code change.
- `backend/` — all engine/server Python code:
  - `state_store.py` — the storage layer: story catalog, per-user save
    load/save, and account creation/login.
  - `story_engine.py` — reference implementation: builds the system prompt,
    calls the Gemini API for narration, runs a separate state-update pass,
    drives automatic subplot/act generation and the player-triggered ending.
  - `plot_manager.py` / `subplot_manager.py` — mid-adventure steering; both a
    CLI and, since `app.py` imports and calls their functions directly, the
    web Plot/Subplot Manager pages.
  - `app.py` — the web interface (login, story picker, play, regenerate,
    Plot Manager, Subplot Manager). Points `template_folder`/`static_folder`
    back at the repo-root `frontend/`/`static/` below, since those aren't
    part of `backend/`.
- `frontend/` — Jinja2 templates for the web interface.
- `static/` — served as-is by Flask (currently just the vendored
  `htmx.min.js` the play page uses).
- `data/` — runtime-only (gitignored): per-user saves and the accounts
  database.
- `test/` — offline regression tests (stubbed LLM/deps, no network or
  pip-installed packages required to run most of them — see
  `test/_llm_stubs.py`). Run with `python test/run_all.py`.

## Roadmap
- [x] Cloud LLM backend (Gemini, via `google-generativeai`)
- [x] Separate state-update pass after each narration call (subplot
      progress, flags, memory-fragment reveals, entity interactions)
- [x] Pacing/director meta-instruction injected every N turns
- [x] Subplots and acts regenerate automatically instead of stopping at a
      fixed count, with a player-triggered ending sequence
- [x] `app.py` wired to `story_engine.py` — login, story picker, play
- [x] Multi-user, multi-story storage architecture (`state_store.py`)
- [x] World/setting content filled in (New Babel, now split into a private
      companion repo; The Last Ferry to Millbrook as the public example)
- [x] The LLM's 3 choices render as clickable buttons (each paired with a
      first-person prose rendition, submitted as the actual player action),
      plus a free-text box as a 4th "steer your own way" option
- [x] Public repo / private story-content split via git submodule
- [x] State-update pass extended to cover inventory (`items_gained`/
      `items_lost`) and relationship scores (`relationship_changes`,
      -100 to 100, bounded to the 20 most significant), not just
      flags/subplot-progress — both now also fed into narration prompts
- [x] Regenerate button for the latest scene — re-rolls the most recent
      narration/options in place, replaying the same player action against
      a state rolled back to just before that turn
- [x] Plot Manager and Subplot Manager web UI, reachable from a "Manage"
      dropdown on the play screen — full coverage of `plot_manager.py`'s and
      `subplot_manager.py`'s commands as forms, calling the same functions
      directly (no subprocess/CLI shell-out)
- [ ] Revert to an earlier scene (similar to a Claude conversation fork) —
      roll the save back to a prior turn, discarding everything after it,
      so a player can back up and try a different path
- [ ] Scene image generation button, in the same `scene-actions` div as
      Regenerate. Best implemented by feeding a reference image (e.g. a
      previously generated scene, or a character/setting portrait) into the
      generation call alongside the prompt, rather than generating from text
      alone each time — keeps character/setting appearance visually
      consistent across a playthrough instead of drifting scene to scene
- [ ] If migrating an in-progress story from another app: paste the raw
      transcript and ask Claude to extract characters/locations/flags/plot
      threads into this schema

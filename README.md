# Palimpsest

An AI-powered Choose-Your-Own-Adventure engine where an LLM narrates a
branching story constrained by a persistent world-state JSON object. The goal
is to keep AI-generated narrative on-theme and coherent over long sessions
without hand-scripting every branch.

The engine is **modular**: every mechanic (stats, relationships, inventory,
revelations, gates, subplots, pacing, progression) is a separate, pluggable
engine. Stories declare which ones they use; unused features contribute
nothing. This makes it possible to author radically different genres — a
survival game with resource management, a mystery with clue reveals, a
relationship-driven narrative — from the same code by mixing and matching
mechanics.

## Stories

This engine is story-agnostic — `stories/<slug>/` holds one story's seed
content and its own `README.md` with that story's pitch, setting, and design
rationale. Adding a new story is a content change (drop in
`stories/<new-slug>/template.json` + `README.md`), not a code change — see
"Multi-User, Multi-Story Architecture" in `docs/ARCHITECTURE.md`.

### Public vs. private stories

This repo is public, but story *content* (setting, plot, characters — the
creative work, as opposed to the engine that runs it) isn't necessarily
meant to be. Two kinds of `stories/<slug>/` entries exist side by side:

- **`stories/example/`** — committed directly in this repo, public. *The
  Last Ferry to Millbrook*, a small cozy-mystery demo story that exists so
  this repo is runnable and demoable with nothing more than a clone — see
  [`stories/example/README.md`](stories/example/README.md). Also the
  reference to copy from when authoring a new story's `template.json`.
- **`stories/private/`** — a single **git submodule** pointing at a private
  companion repo, holding every private story, one folder each
  (`stories/private/<slug>/template.json`). It is a second story *root*, not a
  story: the engine scans both (`state_store.story_roots`), and
  `stories/private/` itself is skipped since it has no `template.json` of its
  own. This repo's `.gitmodules` records the private repo's name and URL (so
  its existence is visible), but its content is only readable by someone with
  access to that repo.

A plain `git clone` of this repo leaves `stories/private/` as an empty
directory, and the catalog is just the public stories. To pull it in (if you
have access):
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
Needs a `.env` file (not checked in) with both `OPENROUTER_API_KEY` (an
[OpenRouter](https://openrouter.ai/keys) key, for both LLM tiers by default)
and `GOOGLE_API_KEY` (a Gemini key from
[Google AI Studio](https://aistudio.google.com/apikey) — required as a
fail-safe even if you never point a tier at Google directly: if any tier's
primary call fails, it's automatically retried once against your free-tier
`GEMINI_MODEL`), plus, for the web interface, `FLASK_SECRET_KEY` (any
random string — used to sign session cookies). LLM calls are split into
three tiers (Tier A: cheap flagship, reasoning off; Tier B: the same
flagship model, reasoning on; Tier C: fastest available model) —
`TIER_AB_MODEL`/`TIER_C_MODEL` are freely swappable via `.env` if you want
to try a different OpenRouter model for either one — the Gemini fail-safe
means an unreachable or misconfigured experiment won't take the whole app
down. See `docs/ARCHITECTURE.md`'s "Backend / Model Notes" for the full
`TIER_AB_PROVIDER`/`TIER_AB_MODEL`/`TIER_C_PROVIDER`/`TIER_C_MODEL` picture.
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
python backend/plot_manager.py add-goal 'Player wants to rescue trapped AI'
python backend/plot_manager.py add-theme 'Identity and memory'
python backend/plot_manager.py seed 'Add a legal advocate who could become an ally'  # LLM drafts a character/subplot/direction from a note
python backend/plot_manager.py seed-list                                             # review drafts staged by `seed` before committing
python backend/plot_manager.py seed-apply seed_001 --role 'close confidant'          # commit a draft, optionally editing a field first
python backend/plot_manager.py seed-discard seed_001                                 # drop a draft instead
python backend/plot_manager.py list-unlinked                                         # relationship names not yet backed by a full character
python backend/plot_manager.py promote-relationship 'the advocate'                   # draft and commit a full character for one, and link it
```

Add `--user <id> --story <slug>` to any command to target a specific save
instead of the local default.

**Characters and relationships**: a `characters` entry (name, description,
role, hook, etc.) can come from four places — a `seed` you write by hand
(above), a subplot or act that decides it genuinely needs a specific new
person to exist, the narrator giving someone an actual proper name mid-scene
(a generic label like "the advocate" deliberately does *not* auto-create
one, to avoid spinning up an NPC for every incidental background figure),
or `promote-relationship` for turning one of those still-generic labels into
a full character by hand once it's earned one. Whichever path creates it,
`player.relationships[name]` carries an explicit `npc_id` link back to the
`characters` entry once one exists — `overview`/the web Plot Manager page's
"Unlinked Relationships" section show which tracked relationships don't
have one yet.

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
python backend/subplot_manager.py reveal frag_0001            # reveal a fragment by hand
```

## User Manual
Everything below is written for someone using the web UI, not a developer.
The playing sections are also available in-app at `/help` once logged in (the
"?" icon in the top bar); the storyboard walkthrough, for story authors, is
here only. Playing via the CLI works too (see "Running It"
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
- **Record Player Goal** / **Note Emerging Theme** - leave notes about
  where the story should head, without immediately acting on them.
- **Seed a Future Addition** - describe a character/subplot/direction in
  your own words; the AI drafts it for you to review, edit, and apply (or
  discard) before it's committed.
- **Unlinked Relationships** - any tracked relationship (a name the story
  has assigned a score to) that isn't backed by a full character yet - most
  are generic labels the narration used instead of a proper name. Promote
  one to a real character on demand.

**Subplot Manager** (Manage → Subplot Manager):
- **Adjust Progress** - nudge a subplot's completion percentage up or down.
- **Modify Subplot** - edit a subplot's title, description, priority, or
  how it ties to the main plot (leave any field blank to keep it
  unchanged).
- **Activate Subplot** - start one of the story's not-yet-started subplots.
- **Advance Act** - force the current act to complete right away; the next
  act generates automatically the next time you take a turn.
- **Reveal Fragment** - manually reveal one of the story's fragments
  (backstory, lore, a secret, a clue) as though its trigger had happened.

You rarely need any of this by hand - subplots regenerate automatically as
old ones complete, and acts are open-ended with no fixed count - but it's
here for when the story needs a deliberate push.

### How to Author a Story (the Storyboard)
The storyboard is a visual editor at `/author`. You use it to lay out a
story's structure: its endings, the threads that lead to them, the cast, and
the stats that measure progress. It writes the story's `template.json` for
you. This walkthrough builds a story from an empty folder to a saved,
lint-clean template.

Before you start, know that **the storyboard runs ahead of the engine**. It
lets you author features the story engine can't run yet, such as tier
`on_enter` directives. Fields like that are marked with a **not built** chip. A story that
uses them saves fine, but it won't load for play until the engine catches up.
That's expected during the V3 overhaul, and so is the fact that play itself
is closed for now (see `PLAY_ENABLED`). Author the story you want, not the one
today's engine can run.

**Words the board uses**

| Term | Meaning |
|---|---|
| **Thread** | A subplot (`plot.subplots`). It has one of three roles. A **spine** thread carries the story toward an ending. A **personal** thread is a relationship arc. A **texture** thread is an episodic interlude that carries nothing. |
| **Destination ending** | An ending the engine steers toward (`mechanics.endings`, `kind: destination`). |
| **Failure ending** | An ending reached through stats, at any time, and never steered toward (`kind: terminal`). |
| **Waypoint** | Something that must happen on the page before a destination can be reached. Threads *carry* waypoints. |
| **Catch-all** | A destination that can never be ruled out. Every story needs one, so the story always has somewhere to go. |
| **Condition** | A rule such as "NERVE at least 6" or "not flag `lark_departed`". Conditions unlock threads, prune endings, and decide when an ending is ready. |
| **Flag** | A named fact the story can set, such as `lark_departed`. A condition can only use flags you've declared. |

#### Step 1 - Get access
The storyboard is off by default, and its routes return 404 to anyone not on
the list. Whoever runs the server enables it with two environment variables:
```bash
AUTHOR_ENABLED=1
AUTHOR_USER_IDS=alice,bob     # comma-separated account ids allowed to author
```
Once you're on the list, log in and go to `/author`. You'll see every story
the server can find, public and private.

#### Step 2 - Create the story's folder and starter file
The board edits existing stories but doesn't create new ones yet, so start
with a small file on disk. Create `stories/<slug>/template.json`. Use
`stories/private/<slug>/` instead if the story must stay out of this public
repo (see *Public vs. private stories*, above). The slug is the folder name,
in lowercase with no spaces, e.g. `salt_road`.

This is the smallest template the board accepts. Rename and rewrite it for
your story:
```json
{
  "schema_version": 3,
  "story_version": "0.1.0",
  "meta": {
    "title": "The Salt Road",
    "synopsis": "A courier carries a sealed letter across a drowned coast."
  },
  "narration": { "pov": "second" },
  "world": {
    "setting_summary": "A coastline half swallowed by the sea.",
    "rules": ["No magic. The sea is the only thing that is never wrong."]
  },
  "protagonist": { "default_name": "Wren", "stats": { "nerve": 5 } },
  "mechanics": {
    "subplots": { "engine": "weighted_threads" },
    "stats": { "engine": "bounded_counter", "floor": 0, "ceiling": 10 }
  },
  "plot": {
    "main_thread": {
      "title": "Deliver the letter",
      "description": "Get the sealed letter to Harrowgate before the tide road closes.",
      "acts": [{ "act_number": 1, "title": "The Quay", "description": "Leave port." }]
    },
    "pacing": { "nudge_frequency": 8, "act_check_frequency": 12 },
    "initial_scene": { "location": "the quay", "summary": "Dawn at the quay." },
    "opening_scene": { "narration": "The tide is out, and the road is open for now." }
  }
}
```
- `protagonist.stats` seeds the stat axes. Each key is one axis, and its
  value is the starting number. Leave out `stats` here, and the whole
  `mechanics.stats` block, if the story has no stats.
- `opening_scene` can be either a single `narration`, or a
  `narration_before_name` and `narration_after_name` pair. The pair is for an
  opening that asks the player to name their character.
- For every other optional field (factions, locations, character creation,
  inventory, relationships and so on), copy from `stories/example/template.json`.

Optionally, add a `README.md` next to the template. After every save, the
board rewrites its `## Synopsis` section from `meta.synopsis`.

> **Everything in the template can be edited on the board.** The title,
> synopsis, setting, rules, locations and factions are in the **World** tab
> (Step 4). The protagonist, character creation and the opening are the
> **Start** cards on the Diagram tab (Step 3). Narration, pacing and every
> mechanic's settings are in the **Forms** tab (Step 17). So the starter file
> only needs enough to load.

#### Step 3 - Open the board and find your way around
Go to `/author` and click your story. The board has four parts:

- **The toolbar** (top): the **Diagram / Matrix / Cast / Fragments / World / Forms**
  views; the *Free canvas* and *Timeline* toggles; the **+ Thread**,
  **+ Ending**, **Sample state**, **Raw JSON**, **Validate** and **Save**
  buttons; and the health badge at far right.
- **The Diagram tab** (middle) shows the story the way it actually runs:
  - **The Start column** on the far left has two cards:
    - **Protagonist**: their name, traits, background, starting stats and
      inventory, and the character-creation steps. Creation lives here
      because it's how the protagonist is made, though the template keeps it
      as its own section (`character_creation`). **The player names the
      protagonist** is a checkbox. When it's ticked, the opening stops to ask
      for a name, and the name you give here is the fallback for a blank
      answer. When it isn't, the story never asks, and this is simply the
      protagonist's name.
    - **Opening**: the opening location (the same setting as the World
      tab's), the starting scene summary, and the opening narration. That's
      two parts, before and after the name, when the player names the
      protagonist, and one otherwise. `{player_name}` is replaced with the
      name either way.
  - **The acts strip** across the top holds the main thread, then your
    authored acts, then the acts generated in play, then the finale. Acts
    are the only thing in a story that happens in sequence.
  - **Thread lanes** on the left, grouped *Spine*, *Personal* and
    *Texture*. Threads run in parallel, so lanes are stacked, not chained.
    Each lane shows when the thread becomes active (● at start, ◐ when a
    condition holds, ○ started by hand), any *fails when*, how many
    waypoints it carries, and its characters.
  - **Endings** on the right: destination endings with their waypoints,
    then failure endings.
  - **A line from a lane to a waypoint** means that thread carries that
    waypoint. A waypoint with a hollow dot has no thread carrying it yet.
- **The inspector** (right): shows whatever you've selected. With nothing
  selected, it shows **Story health**: the lint issues, the story's flags,
  and the ending-funnel settings.
- **The stat sidebar** (above the acts): one entry per stat axis.

Click a lane or an ending to select it. Everything it doesn't touch is
dimmed, so you can see what feeds what. **Esc** clears the selection.

Tick **Free canvas** for the original drag-and-pan diagram (Start box, drawn
unlock links, Auto-layout). Both views edit the same story. Box positions on
the free canvas are kept in your browser and written to the file
(`_storyboard.positions`) when you save. Nothing else is written until you
press **Save**.

#### Step 4 - Build the world
Open the **World** tab. It's one page with six sections, and each field is
labelled with who sees it:

- **Story**: title, synopsis (for you and the README; never prompted), and
  the genre, tone and content rules sent with every narration. The Start
  box's *Title* and *Synopsis* edit the same two fields.
- **Setting**: the world in a paragraph, sent every turn.
- **World rules**: strict facts the story must never break. Every rule
  reaches every narration and every generated act, so the section shows
  their total word count. A rule that only matters when a particular
  character or place is on the page belongs in lore instead.
- **Locations**: where scenes can happen. The narrator sees only the current
  location's description and the names of the places connected to it. Pick
  the **Opening location** here. Connections are one-way, and a location's
  inspector marks which ones connect back. Renaming a location's id updates
  every connection, the opening location, and any gate that guards it.
- **Factions**: name, goals and stance toward the player, all sent every
  turn.
- **Lore** (*not built*): facts injected only when they matter. An entry has
  **keys** (words matched in the player's action or the last scene), an
  optional **Also when** condition (injects the entry with no key on the
  page), an optional **Unlocks when** condition (keeps it dormant until
  earned), a **priority**, **sticky turns**, and its **content**. At most
  **Max active** entries are injected at once, highest priority first. Both
  conditions are checked strictly: one naming something unknown never
  injects. The engine that runs lore doesn't exist yet, so a story with lore
  saves and checks fine but won't load for play until it does.

Validate flags a connection, opening location or gate naming a location that
doesn't exist, a lore id used twice, lore keys too short or common to be
useful (L13), and lore that has neither keys nor a condition.

#### Step 5 - Add your endings, starting with the catch-all
Work backwards: decide where the story can end before deciding how it gets
there.

1. Click **+ Ending**. A *New ending* box appears, with its inspector open.
2. Give it a **Title** and a **Main theme** (a line on what this ending is
   about, for you).
3. Leave **Kind** as *Destination: steered toward*.
4. For your first ending, tick **Catch-all: can never be ruled out**. Every
   story needs one catch-all. Without it the health panel shows an error
   (L08), and the story could reach a point with nowhere to go.
5. Add one or two more destinations. For each one that can be *lost* during
   play, fill in **Viable while**, the condition under which it stays
   possible. For example, "not flag `lark_departed`" means the ending is
   removed for good once Lark leaves. Filling in *Viable while* unticks
   *Catch-all*, and clearing it ticks the box again.

You can come back later to each ending's other fields:
- **Ready when**: the condition that commits the story to this ending, e.g. a
  stat threshold plus "all waypoints done".
- **Hint**: a line of in-world detail that may be slipped into a pacing
  nudge while this ending is being steered toward.
- **Criteria**: what the judge checks before committing to the ending. The
  narrator never sees it, and it must stay that way: if the narrator could
  see it, it would give away the twist on turn one.
- **Arc title / Arc description**: what the narrator is given once the story
  has committed to this ending.
- **Epilogue**: text shown to the player after THE END. It never reaches the
  narrator.

#### Step 6 - Give each destination its waypoints
Waypoints are the events that must happen on the page before a destination
can be reached. In a destination's inspector, under **Waypoints**:

1. Type the event into *New waypoint* and press **Add**. Write the event
   itself, not its meaning: "a door opens for Lark", not "Lark earns trust".
2. Give each waypoint a way to be recognised. Use at least one of these,
   otherwise it's an error (L09):
   - **detect**: text a judge reads each scene against, e.g.
     *"Lark is named aloud"*.
   - **done_when**: a condition the engine checks, e.g. *REACH at least 20*.
3. **AI: Suggest waypoints** drafts a list from the ending's arc, criteria
   and hint. Accept the suggestions you like and discard the rest. Nothing is
   written until you save. This button needs a working LLM key on the server.

A waypoint that no thread carries shows *No thread carries this yet*. You
fix that in Step 8.

#### Step 7 - Add threads
1. Click **+ Thread** and give it a **Title**. Write its **Main theme**, which
   becomes the subplot's description.
2. Choose its **Role**:
   - **Spine** for a thread that carries waypoints toward an ending.
   - **Personal** for a relationship arc. It may also carry waypoints.
   - **Texture** for episodic breathing room. It carries nothing and closes
     within the act.
3. Optional:
   - **Fails when**: a condition that marks the thread failed. If a
     destination still has waypoints that only failed threads carry, that
     destination is removed.
   - **On complete: stat events**: comma-separated cost keys applied when
     the thread completes, e.g. `lattice.rejoined`. These only mean something
     in a story that prices its stats (see Step 13).
4. Under **Characters in this thread**, tick who the thread involves. The
   list comes from the Cast tab, and a character whose name already appears
   in the thread's text is marked so you can spot them. Ticked characters
   show as chips on the thread's box on the canvas. This is for you only:
   nothing is sent to the narrator. The health panel warns about a
   *personal* thread with nobody in it.

#### Step 8 - Wire it up
Two things connect a thread to the rest of the story, and neither of them is
another thread.

**When it becomes active** is set on the thread itself, under **Becomes
active** in its inspector:

- **At the start**: active from the first turn, alongside every other thread
  that starts active. Most threads should be this.
- **When a condition holds**: it starts itself the turn the condition becomes
  true, e.g. *REACH at least 50*. To start a thread after another one, use a
  *Thread status* condition, e.g. *A Second Pair of Hands progressed*.
- **Only when started by hand**: it waits for an activation in the Subplot
  Manager. The health panel warns about these.

**What it carries** is a line from its lane to an ending's waypoint. Drag
from the dot on the lane's right edge onto a waypoint row. You can also drop
it on an ending card, which picks the first waypoint nothing carries yet, or
use **+ Carry a waypoint…** in the thread's inspector. Click a line to select
it and remove it. Texture threads carry nothing, and failure endings take no
lines because they're reached through stats.

When there are many threads, the **Matrix** tab is quicker. It shows a grid
of threads against waypoints: click a dot to add or remove a link. The
columns with no carrier are the ones still to fill.

**Acts.** Click an act in the strip to edit its title, description and
*completion signals*, which are what the act judge looks for to decide the
act has resolved. **+ Act** adds another authored act. Click **Main thread**
to edit the story's through-line and **Max acts**. The engine doesn't
enforce *Max acts* yet, so it's marked *not built*.

#### Step 9 - Write conditions, and declare flags first
Every condition field (*Viable while*, *Ready when*, *Fails when*,
*done_when*, *Unlocks when*) uses the same builder:

- Pick a leaf from the dropdown: **Stat**, **Relationship**, **Flag**,
  **Revelation revealed**, **Thread status**, **Turn at least**,
  **Act at least** or **Waypoints done**. Then fill in its values.
- Combine leaves with **all / any** groups, up to three levels deep.
- **Negate (NOT)** turns a condition into "this must not hold".
- **Raw JSON** switches to editing the condition as JSON, for anything the
  builder can't express. The board never drops a condition it can't display.
  It shows it as JSON instead.

A **Flag** leaf only offers flags the story has declared. To declare one:
1. Deselect everything (press **Esc**) so the inspector shows Story health.
2. Under **Story flags**, click **+ Flag**.
3. Give it an **id** (e.g. `lark_departed`) and **detect** text describing
   what on the page sets it (e.g. *"Lark has left the story for good"*). A
   flag with no detect text can never be set, so the health panel warns
   about it.

A condition that names an undeclared flag, or an unknown stat, fragment or
character, is a save-blocking error (L10). Typos are caught here rather than
in play.

#### Step 10 - Build the cast
Switch to the **Cast** tab and click **+ Character**. For each character:

| Field | Who sees it | What to write |
|---|---|---|
| **Name** | Everyone | Their one identity. Relationship scores attach to this exact name, so avoid renaming once a story is being played. |
| **Description** | The narrator, every turn | Who they are, in a few sentences. This is all of the character the narrator sees. The word count is shown so you can keep it short. |
| **First contact** | The narrator, until the first scored interaction | Their stance when they first meet the protagonist. Once a relationship score exists, the relationship tiers take over. |
| **Hook** | The pacing nudge | A concrete way to bring them on stage. Leave it empty for a constant companion. Only the **last two** characters with hooks are nudged at a time, so use **Move earlier / Move later** to set the order. |
| **Role** | You only | A card subtitle for you. It is never sent to the narrator, so it's safe to say where their arc goes. |
| **Canon** | You only | Secret truths about the character, added with **+ Canon note**. The engine never reads them. |

The **leak check** marks any canon that has crept into the description or
hook in red, because those two fields reach the narrator. **Threads** lists
every thread with a checkbox. It's the same link as a thread's *Characters in
this thread* (Step 7), editable from either side. **Named on the board**
lists the threads and endings whose text mentions the character, which is a
text match and not a link. Renaming a character updates every thread that
casts them and every relationship condition that names them. Deleting a
character removes them from every thread.

#### Step 11 - Add bonds and side threads (optional)
These two go together, and both are marked **not built**: the storyboard
saves them, but the engines that play them don't exist yet, so a story that
authors either one won't load for play until they do.

**Bonds** are how characters feel about each other, one way at a time. Mira
toward Salome and Salome toward Mira are separate scores, so unrequited
feeling can exist. At the bottom of the **Cast** tab, click **+ Add bonds**,
then fill in:

| Part | Who sees it | What to write |
|---|---|---|
| **Low end / High end** | You only | What the two ends of a bond mean, e.g. *hostile* and *devoted*. |
| **Registers** | The state-update pass | The closed list of things one character does to or for another, each with how far it moves the bond, e.g. `covered_for_them` 8, `betrayed_them` -15. Nothing else can move a bond. |
| **Tiers** | The narrator, as labels | Score bands, e.g. *warm* at 25, *wary* at -25. The narrator sees only the label, never a number, and only when both characters are in the scene. |
| **Starting bonds** | The engine | A grid: read across, the row is who feels it and the column is who it's about. Leave a cell blank and the pair starts at 0 the first time something happens between them. |
| **Limits** | The engine | How many bonds with characters the model invented are kept, and the most a bond can move within a few turns. |

A **Bond** condition tests one direction, e.g. *Salome → Mira at least warm*,
so a bond can start a thread or feed an ending like any other condition.

**Side threads** are short episodes the story runs on its own between the
planned beats. They aren't threads: they carry no waypoints, never count
toward an act, and can never be part of an ending. The engine decides when one
starts and who it's about; the model writes what happens. Open the **Side
threads** tab and click **+ Add side threads**:

1. **When they start and end.** How many can run at once, how many turns to
   wait between starts, and the turn limit after which the engine closes one.
   Tick the pacing-loop beats after which one may start (none ticked means
   *respite*).
2. **Built-in recipe.** When none of your recipes applies, the engine casts
   the two characters seen recently with the strongest bond. Untick it to turn
   that off.
3. **Protected characters.** Anyone whose arc the story needs to keep for
   itself. No side thread will cast them. The same switch is on each
   character in the Cast tab.
4. **Recipes.** Click **+ Recipe** for each situation the story can start an
   episode from:
   - **Premise**: what the episode is about, written about the slots rather
     than named people, e.g. *one of them has started to care, and the other
     hasn't noticed*.
   - **Cast**: the roles it needs. A slot is a character (anyone, an authored
     character, one the model invented, or a named character), a place, or
     an item with a given tag.
   - **Eligible when**: a condition that can name the slots, e.g. *slot a →
     slot b at least warm*. The engine checks it for every way of filling the
     slots. A typo means the recipe never starts, never that it starts on the
     wrong people.
   - **May move**: the only values the episode's premise and end conditions
     may involve, such as a bond between two slots, a slot's relationship
     with the protagonist, a stat, items with a tag, or a kind of leverage.
   - **Callback** (optional): make the recipe follow an episode that already
     ended, e.g. the favour repaid. Each ended episode is followed at most once.
5. **Player-started side threads** (optional). Click **+ Let the player start
   side threads** so that a pursuit the player chooses off the rails, like
   running errands for a bar owner, gets a shape and an end. The state-update
   pass reports a pursuit only when the protagonist chose it and it matches no
   existing thread. It becomes a side thread once it has been reported
   **Reported** times within **Within turns** turns, so one curious scene never
   does. Set how many can run at once, and after how many offers without
   progress one ends as abandoned. **May move** works as for recipes, except
   that a pursuit has no slots: its cast is whoever was in the scenes that
   reported it, named as *the cast*. A pursuit whose cast would include a
   protected character never opens. The player still can't end the story. A
   recipe's callback can follow *a thread the player started*, including one
   the player abandoned. This replaces the Plot Manager's player goals once
   its engine is built; until then, player goals work as before.
6. **Vignettes** (optional). Single scenes of texture with no thread and no
   extra model call. Set how often, write seeds of your own, and tick which
   places, characters or held items may also be featured.

**Validate** flags a slot or seed naming someone who isn't in the cast, a
protected character named in a recipe, a beat, item tag, stat or leverage
kind the story doesn't have, and a setup that could never start anything.
Renaming a character updates their starting bonds, the protected list, recipe
slots that name them, and every Bond condition.

#### Step 12 - Write fragments (optional)
A fragment is information the story holds back until something specific
happens on the page. It doesn't have to be a memory: it can be backstory, a
piece of lore, a secret, or a clue. Once revealed, it does two jobs:
- **The narrator can use it.** Its content joins the narrator's prompt for the
  rest of the story, under a heading that only says it has been revealed. So
  write the content as what it is: something the protagonist remembers,
  something they learned about the world, or something someone let slip.
- **Conditions can test it.** A *Revealed* condition (Step 9) can start a
  thread, hold an act, or be part of an ending's conditions.

Lore on the World tab (Step 4) is different: it's background the narrator is
given whenever it's relevant, not a discovery the story records, so conditions
can't wait on it. Use a fragment when the moment of finding out matters.

Open the **Fragments** tab and click
**+ Fragment**. For each one, fill in:

| Field | Who sees it | What to write |
|---|---|---|
| **Id** | Conditions | What a *Revelation revealed* condition names, e.g. `frag_0006`. Renaming it updates every condition on the board that uses it. |
| **Title** | You only | A short name, e.g. *the bloodline record*. It's shown wherever a condition names this fragment, so conditions read as what they mean rather than as an id. It's never sent to the narrator. |
| **Trigger** | The state-update pass, until revealed | The event on the page that reveals the fragment. Write something a scene can show, not what it means. |
| **Content** | The narrator, once revealed | What the narrator is given after the fragment is revealed. Until then it reaches no prompt. |
| **Revealed only after** | The engine | Other fragments that must be revealed first, to build a chain. |

**Used by** lists every condition that names the fragment, and clicking one opens it. In
the condition builder, a *Revelation revealed* leaf lists fragments as
`id — title`, with a chip that opens the fragment. A fragment with no title is
labelled by the start of its trigger, never by its content. The health panel
flags a duplicate id, an *after* naming a fragment that doesn't exist or
forming a loop, and a missing trigger or content.

#### Step 13 - Set stat tiers
If the template has a `mechanics.stats` block (Step 2), the stat sidebar lists
each axis. Click an axis to open its **tier ladder**, a strip from floor to
ceiling:

1. Click **+ Tier** for each band you want, e.g. *shaken* from 0, *steady*
   from 4, *iron* from 8.
2. For each tier, set **at** (where it starts), a **label**, and the
   **narration** line. The narration line is the only thing the narrator is
   given about this axis, never the numbers.
3. Drag the boundaries on the strip, or use the arrow keys, to adjust. A
   boundary can't be dragged past its neighbours.
4. **On enter: directive** is an instruction that fires when a tier is
   entered from below. It's marked *not built*.

An axis with no tiers gets a warning (L06), because the narrator then has no
idea what the number means. Tiers out of order or sharing a start value are
an error (L07). **Sort tiers by where they start** fixes the ordering. New
axes, costs and drift (`per_turn`) aren't edited on the board yet; set them in
Raw JSON. Adding `costs` to any axis switches the whole story to priced stats.

#### Step 14 - Add failure endings (optional)
A failure ending is a loss the story can hit at any time, such as running out
of NERVE. Click **+ Ending** and set **Kind** to *Failure: reached through
stats*. Then fill in:
- **Min turn**: the earliest turn it can trigger.
- **Ready when**: the condition the engine checks every turn, e.g. NERVE at
  most 0.
- **Criteria**: the judge's confirmation, e.g. *"the scene just narrated was
  lethal, not merely damaging."*
- **Arc** and **Epilogue**, as for destinations.

Failure endings carry no waypoints, and threads can't connect to them.

#### Step 15 - Set the ending timeline (optional)
Tick **Timeline** in the toolbar. It shows the story's ending-funnel timing
as four phases:

- **Open**: every destination is still in play.
- **Narrow**: the engine starts steering toward the leading destinations.
- **Commit window**: the story can commit to an ending.
- **Forced**: past **commit by**, the engine forces a commit.

Drag the boundaries to set them. If none are set yet, **Start from
40 / 90 / 140** gives you a starting point. The same numbers, plus **Check
every N turns**, **Steer top N destinations** and **Finale min/max turns**,
are also under *Ending funnel settings* in the Story health panel. A blank
*Check every* or *Steer top* uses the engine's default (6 and 2). A blank
phase boundary means that phase never begins. With no *commit by*, for
example, the story is never forced to an ending, so it ends only when an
ending becomes ready. The failure-ending row shows a tick at each failure
ending's min turn.

#### Step 16 - Test your conditions with Sample state
Click **Sample state** to describe a moment of play. You can set stat values,
relationship scores and peaks, bond scores (for every starting bond and every
pair a Bond condition names), set flags, revealed fragments, thread status,
planted waypoints, and the turn and act. Then click **Evaluate conditions**.
A side-thread recipe's condition isn't in the table: it names slots, which
only get filled when the engine picks a cast.

You get a table of every condition in the story, showing whether it holds,
how close it is, and whether an unknown name reads as true or false in that
spot. The stat sidebar also shows each axis's sample value and tier.
Conditions are evaluated by the real engine code, so the table shows what
play would do. Nothing is saved.

A good check for each ending that can be lost: set up the state that should
rule it out, and confirm its *Viable while* no longer holds.

#### Step 17 - Fill in everything else in the Forms tab
The **Forms** tab covers every part of the template that has no dedicated
editor: narration, pacing, and the settings of every mechanic (stats,
relationships, inventory, thread progress, the pacing loop, progression,
gates, the tracked entity). Pick a section on the left. The protagonist,
character creation and the opening are on the Diagram tab's Start cards.

- **The forms are built from the template schema itself**, so they offer
  exactly the fields the story format allows. Each field's help text comes
  from the schema.
- **A section the story doesn't author** says so, with an **+ Add** button.
  An absent section means that feature doesn't exist in the story. Nothing
  about it is prompted or tracked.
- **Author notes** (keys starting with `_`, such as `_authored`) are shown
  as editable notes. They're never read by the engine.
- **Edit as JSON** switches a section to its raw JSON.
- **Fields other tabs own are left out** so each field has one editor: stat
  tiers are on the tier ladder, and the opening location is in the World tab.

Only the sections you change are written on Save. A thread's priority, span,
completion threshold and "ties to the main plot" are in its own panel on the
Diagram tab, and an act's **Requires** condition is in the act's panel.

#### Step 18 - Validate and save
- **The health badge** (top right) counts open issues and reads
  **Story holds** when there are none. Click an issue to jump to the box,
  link or character it's about. *Fix* marks an error and *Check* marks a
  warning.
- **Validate** runs the full server-side check (schema plus lint) without
  writing anything. It lists errors and warnings, and offers **Confirm &
  Save**.
- **Save** always writes the whole template, even with errors outstanding,
  so you can save half-finished work and come back to it. It bumps the
  story's `story_version` and refreshes the README synopsis. **A story with
  lint errors is hidden from players** until they're fixed.
- **Raw JSON** opens the whole template as text, for when you'd rather edit
  it all at once. **Validate & save** there runs the same lint and refuses to
  save if there are errors.

Nothing about a story's structure is locked in. Reopen the board at any time
and keep going. If the story authors something the engine doesn't support
yet, loading it for play fails loudly (`UnknownEngineError`) rather than
quietly skipping it. That's intended while the overhaul is under way.

## Architecture

The core engine is **modular**: every mechanic (stats, relationships, inventory,
revelations, gates, subplots, pacing, progression) is a separate engine under
`backend/mechanics/`, registered dynamically from the template's `mechanics`
block. When a story declares `"engine": "bounded_counter"` inside
`protagonist.stats`, the engine loads that module; omit the declaration and the
feature doesn't exist in that story — no state, no prompt lines, nothing.

This move — from monolithic `story_engine` to the **mechanic registry** (v2,
phases 1–6) — makes it possible to author wildly different story types (a
survival game with resource management, a mystery with clue reveals, a
relationship-driven narrative) from the same code by mixing and matching
mechanics, and to extend the system without editing the narrative engine.

State-update is where mechanics live: a turn's narration + player action flows
through `run_observation_pipeline()`, each engine reads its own field from the
diff, events are logged, and then resolved in dependency order to produce
effects. Effects carry absolute values (not deltas), so "set fuel to 12"
replays correctly every time. See `docs/ARCHITECTURE.md` § *The Mechanic
Registry* for the full picture and `docs/SCHEMA_V2_SPEC.md` for the template
schema.

Saves are still schema v2, unchanged — the v2→v3 cutover was never needed
because templates moved to v3 (new declare-to-bind `mechanics` blocks) while
saves stayed put, so every existing save loads against v3 templates without
migration.

## File Structure
- `stories/<slug>/template.json` — authored seed content for one story (meta,
  world, player, characters, plot, history_log, and a v3-format `mechanics`
  block declaring which engines this story uses). `stories/example/` is
  committed here directly (public); `stories/private/<slug>/` comes from a
  private git submodule — see "Public vs. private stories" above. Adding a new
  story is a content change, not a code change, in either root.
- `backend/` — all engine/server Python code:
  - `state_store.py` — the storage layer: story catalog, per-user save
    load/save, and account creation/login.
  - `story_engine.py` — turn orchestration: builds the system prompt,
    calls the LLM for narration, runs the observation pipeline through each
    mechanic engine, and drives automatic subplot/act generation and the
    player-triggered ending. Narration (Tier A) and state-update (Tier B/C
    depending on the call) are separate LLM calls.
  - `mechanics/` — the mechanic registry, one module per engine:
    `__init__.py` (the contract), `bounded_counter.py` (stats),
    `scored_axis.py` (relationships), `tagged_items.py` (inventory),
    `triggered_reveal.py` (revelations), `triggered_ending.py` (failure
    conditions), `weighted_threads.py` (subplots), `beat_counter.py`
    (pacing/director), `spendable_ledger.py` (progression costs), and
    `precondition.py` (gates). Every module registers itself on import.
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
- `docs/` — architecture and design docs:
  - `ARCHITECTURE.md` — how the engine actually works (as built).
  - `SCHEMA_V2_SPEC.md` — template/save schema and design principles.
  - `Narrative_Pacing_Loop_Spec_v4.md` — the pacing/director system.
  - `Web_UI_Spec.md` — web UI design intent.
  - `Narrative_Engine_Spec.md` — what the engine stores but never prompts.
  - `ENGINE_V2_SPEC.md` — the mechanic registry design (v2 was the plan;
    phases 1–6 built it as described).
  - `analysis_and_plans/` — incident notes, measurement reports, phase summaries.
- `test/` — offline regression tests (stubbed LLM/deps, no network or
  pip-installed packages required to run most of them — see
  `test/_llm_stubs.py`). Run with `python test/run_all.py`.

## Roadmap
- [x] Cloud LLM backend (Gemini via `google-generativeai`, OpenRouter via
      `requests`; three cost/latency tiers with automatic fail-safe to Gemini)
- [x] Separate state-update pass after each narration call (subplot
      progress, flags, fragment reveals, entity interactions)
- [x] Pacing/director meta-instruction injected every N turns
- [x] Subplots and acts regenerate automatically instead of stopping at a
      fixed count, with a player-triggered ending sequence
- [x] `app.py` wired to `story_engine.py` — login, story picker, play
- [x] Multi-user, multi-story storage architecture (`state_store.py`)
- [x] World/setting content filled in (three story templates: one public, two
      in a private companion repo)
- [x] The LLM's 3 choices render as clickable buttons (each paired with a
      first-person prose rendition, submitted as the actual player action),
      plus a free-text box as a 4th "steer your own way" option
- [x] Public repo / private story-content split via git submodule
- [x] State-update pass extended to cover inventory (`items_gained`/
      `items_lost`), relationship scores (`relationship_changes`), stats,
      flags, and revelations — all fed into narration prompts
- [x] Regenerate button for the latest scene — re-rolls the most recent
      narration/options in place, replaying the same player action against
      a state rolled back to just before that turn
- [x] Plot Manager and Subplot Manager web UI, reachable from a "Manage"
      dropdown on the play screen — full coverage of `plot_manager.py`'s and
      `subplot_manager.py`'s commands as forms, calling the same functions
      directly (no subprocess/CLI shell-out)
- [x] Characters can be created automatically — from a subplot/act that
      calls for a specific new person, or the narrator naming someone new
      mid-scene (never for a generic label) — or promoted by hand from an
      existing relationship-only name. `player.relationships[name]` now
      carries an explicit `npc_id` link to its `characters` entry instead of
      being tied together only by matching name strings
- [x] **Mechanic registry** (v2, phases 1–6) — every mechanic (stats,
      relationships, inventory, revelations, gates, subplots, pacing,
      progression) moved from monolithic `story_engine` into separate,
      dynamically-loaded engines. Stories declare which they use; unused ones
      contribute nothing. Makes it possible to author radically different
      genres from the same code.
- [x] **Priced stats via event vocabulary** — stories can author a closed
      set of events that prices stat changes, replacing the open delta map.
      Enables consistency and limits unforeseen side effects on a priced axis.
- [x] **Asynchronous turn-taking** — `/api/turn` and `/api/regenerate` now
      return 202 immediately and kick the turn onto a background thread, with
      the client polling for the result. Fixes a production Cloudflare tunnel
      timeout on long turns.
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
- [ ] Locations generated automatically, mirroring how characters already
      are — a subplot/act names a new required location only when genuinely
      needed (not the common case), committed via a new `insert_location()`
      into `world.locations` with `connected_to` back to the current
      location, rather than a freestanding generator
- [ ] Engine v3 — further isolate narration/mechanics, lift state shape into
      a separate schema from the template, allow real-time stat/mechanic
      validation, and resolve even more architecture debt. (v2 was built as
      planned; v3 design is in `docs/ENGINE_V2_SPEC.md` and phase order in
      `docs/analysis_and_plans/ENGINE_V2/ENGINE_V2_PHASES.md`; actual build
      deferred.)

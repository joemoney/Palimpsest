# Authoring a Palimpsest story by editing JSON: a manual for AI agents

This manual is for an agent (or a person) who writes a story template **by editing
`template.json` directly**, without the storyboard at `/author/<slug>/board`. The storyboard
and this manual describe the same file. Everything the board can do, you can do here, and a
story written this way opens on the board unchanged if you follow the shapes below.

Read CLAUDE.md first if you will touch engine code. You don't need it to write a story. If
you are helping a human who uses the board, use `docs/Assistant_CoAuthor_Manual.md` instead.

Contents:
1. [The loop](#1-the-loop)
2. [Ground rules](#2-ground-rules)
3. [What is built and what is not](#3-what-is-built-and-what-is-not)
4. [Who sees what](#4-who-sees-what)
5. [Build order](#5-build-order)
6. [Section reference](#6-section-reference)
7. [Conditions](#7-conditions)
8. [Endings](#8-endings)
9. [Threads](#9-threads)
10. [Optional mechanics](#10-optional-mechanics)
11. [Worked example](#11-worked-example)
12. [Lint messages and what they mean](#12-lint-messages-and-what-they-mean)
13. [Done checklist](#13-done-checklist)

---

## 1. The loop

A story is one file: `stories/<slug>/template.json`. The slug is the folder name (lowercase,
underscores). A story meant to stay private goes in the private submodule instead, at
`stories/private/<slug>/template.json`. Never put a private story in this repository's history.

```
edit template.json
python3 scripts/lint_template.py <slug>      # or a path to any template.json
fix every ERROR; read every warning
repeat
python3 scripts/lint_template.py <slug> --write   # canonical formatting + story_version bump
```

`scripts/lint_template.py` runs exactly the checks the board's **Validate** runs, plus:

| Line in the report | Meaning |
|---|---|
| `N error(s), M warning(s)` | Errors keep the story out of the players' list. Warnings are advice. |
| `Loads for play: yes / no - <reason>` | Whether the engine will start the story. `no` is expected while you author something the engine hasn't built yet (section 3). |
| `Authored but not built in this engine yet: ...` | Modules the engine will refuse until they are built. |
| `The board would rewrite these top-level sections on its next Save: ...` | You wrote a shape the board stores differently. It still works, but the next board Save will change it. Fix it to the shape in this manual. |
| `Not in canonical formatting` | Run with `--write`. |

`--write` is the board's Save. It rewrites the file with 2-space indentation, real unicode
(not `\uXXXX`) and a trailing newline, stamps a new `story_version` (`YYYY-MM-DD.N`), and
resyncs the story README's `## Synopsis` section from `meta.synopsis` if a README exists. It
writes even when there are errors, as the board does. Don't edit `story_version` by hand.

Exit status: 0 no errors, 1 errors, 2 unreadable file. `--json` prints the report as JSON.

---

## 2. Ground rules

These are the rules most likely to be broken by someone editing JSON rather than using the
board. Each one has broken a real story at least once.

1. **Mechanics live inside `mechanics`, and each block declares its `engine`.** A block one
   level up (`"stats": {...}` at the top level) parses, seeds nothing and does nothing. A block
   under `mechanics` with no `"engine"` key binds nothing: the story looks fine and never
   progresses. Use only the engine names in section 3.
2. **An absent module means the feature does not exist.** Leave a mechanic out entirely rather
   than writing an empty block. Don't write empty strings, empty lists or `null` for optional
   fields: omit the key.
3. **A character's name is their only identity.** The key in `world.characters`, the `name`
   inside it, every thread `cast`, every `relationship` or `bond` condition and every bond seed
   must use the exact same string, e.g. `"Salome Vence (the Advocate)"` everywhere, never
   `"Salome"` in one place.
4. **Every story needs `mechanics.endings` with at least one catch-all.** The player cannot end
   the story (there is no end-story command). A story ends only when the ending funnel commits
   to a destination, forces one at `commit_by`, or confirms a terminal. A catch-all is a
   destination with no `viable_while`.
5. **The engine does the arithmetic, never the narrator.** Don't write rules like "if the
   player chose X and Y, Lark is a woman" into `world.rules`. Anything that must be right every
   time is authored as data (a stat tier, a condition, a derived value) so code produces it.
6. **Keep secrets out of what the narrator sees.** Canon, twists and the truth about a
   character go in `canon`, `protagonist.background`, an ending's `criteria` or a `_` note.
   Never paste them into a description, rule, hook or first-contact line (section 4). The board
   flags canon that turns up in a character's description or hook.
7. **`_`-prefixed keys are author notes.** Any object accepts them (`"_authored": "why this
   threshold"`). The engine ignores them and they never reach a prompt. Use them to record why.
   Don't hand-edit `_storyboard`: it holds board card positions.
8. **No creative decision belongs in the engine.** If a novelist would have an opinion about a
   number (a threshold, a cost, when the story may end), it is authored here. The engine has no
   defaults for those, on purpose, so leaving one out means that thing never happens.
9. **Refer to things by the id you declared.** Flags must be declared before a condition names
   them. Fragments, locations, threads, endings, waypoints, recipes and creation steps are all
   referred to by id, and lint (L10, L16) catches a name that doesn't exist.

---

## 3. What is built and what is not

The storyboard leads and the engine follows: you may author anything below, including
mechanics the engine doesn't run yet. A story that authors an unbuilt one **refuses to load
for play, loudly**, until the engine catches up. That's correct, not something to work around
by leaving the mechanic out.

| Template path | Engine | Status |
|---|---|---|
| `mechanics.stats` | `bounded_counter` | Built |
| `mechanics.relationships` | `scored_axis` | Built |
| `mechanics.inventory` | `tagged_items` | Built |
| `mechanics.progression` | `spendable_ledger` | Built |
| `mechanics.pacing_loop` | `beat_counter` | Built |
| `mechanics.revelations` | `triggered_reveal` | Built |
| `mechanics.gate` | `precondition` | Built |
| `mechanics.subplots` | `weighted_threads` | Built |
| `mechanics.endings` | `ending_funnel` | Built: pruning, scoring, commits, forced commit, terminals. **Not built:** steering (`hint`, `plant` in nudges), `epilogue` display, `max_acts`. |
| `mechanics.failure_conditions` | `triggered_ending` | Built but **retired**: write terminals in `mechanics.endings` instead. |
| `mechanics.flags` | (no engine key) | Declared flags are readable by conditions. **Gap:** the state-update pass is not yet told each flag's `detect` text, so a declared flag is set only if the model happens to use its exact id. |
| `mechanics.tracked_entity` | (no engine key) | Built |
| `plot.pacing.story_clock` | none | **Not built** (loads, with a warning; every turn still counts) |
| `narration.scene_length_by_moment` | none | **Not built** (loads, with a warning; every scene still uses `scene_length`) |
| `mechanics.lore` | `keyed_lore` | **Not built** (refuses load) |
| `mechanics.bonds` | `scored_bonds` | **Not built** (refuses load) |
| `mechanics.side_threads` | `episodic_threads` | **Not built** (refuses load) |
| `derived` (top level) | none | **Not built** (refuses load: nothing substitutes `{name}` yet) |
| `mechanics.stats.axes.<axis>.tiers[].on_enter` | `bounded_counter` | **Not built** (loads, with a warning) |
| `plot.subplots.<id>.cast` | none | Author-only; no engine reads it |

---

## 4. Who sees what

Three audiences read a template:

- **Narrator**: the model writing the scene. What reaches it shapes every page.
- **Judge**: the state-update pass and the one-off judge calls. They classify what happened
  and never write prose, so they may be told things the narrator must not know.
- **Author**: you. Never sent to any model.

| Field | Audience |
|---|---|
| `meta.genre`, `meta.tone`, `meta.content_rules`, `narration.*` | Narrator, every turn |
| `meta.title`, `meta.synopsis` | Author (the synopsis also feeds the README) |
| `world.setting_summary`, `world.rules` | Narrator, every turn. Keep rules short: every one costs tokens on every turn. |
| `world.locations.<id>.name` / `description` | Narrator, while the scene is there. Connected locations are shown by name. |
| `world.factions.*` | Narrator, every turn |
| `world.characters.<name>.description` | Narrator, every turn |
| `.first_contact` | Narrator, only until the first scored interaction with that character |
| `.hook` | Pacing nudge, until the character is introduced (only the last two characters with hooks are nudged) |
| `.role`, `.canon` | Author |
| `protagonist.background` | Author |
| `plot.main_thread.title` / `description`, act `title` / `description` | Narrator |
| act `completion_signals` | Judge (act check), and the narrator through the pacing nudge ("this act resolves when") |
| thread `title` / `description` / `ties_to_main_plot` | Narrator through the pacing nudge, while active: the highest-priority active thread with its description and ties, the next two by title |
| fragment `trigger` | Judge, until revealed |
| fragment `content` | Narrator, once revealed |
| fragment `title` | Author |
| flag `detect`, waypoint `detect` | Judge |
| waypoint `plant` | Narrator (steering and forced-commit bridging) |
| ending `arc` | Narrator, once that ending is committed (the finale) |
| ending `criteria` | Judge (commit and terminal confirmation) |
| ending `hint` | Narrator via pacing nudge (steering; not built) |
| ending `name` | Author and board; used for the finale only when there is no `arc` |
| gate `refusal_hint` | Narrator, when an action is refused |
| stat tier `narration` | Narrator, while the stat is in that tier |
| relationship tier `narration` | Narrator, while that character is in that tier |
| lore `content` | Narrator, when injected (not built) |
| `tracked_entity.description` | Narrator |
| `tracked_entity.canon` | Author |

---

## 5. Build order

Work in this order. It mirrors the storyboard guide in README.md (the step numbers match),
and each step leaves a file that lints.

1. **Skeleton.** Every required top-level key (section 6.1).
2. **World.** `meta`, `narration`, `world.setting_summary`, `world.rules`, locations,
   factions.
3. **Start.** `protagonist`, optional `character_creation`, `plot.initial_scene`,
   `plot.opening_scene`.
4. **Endings first.** A catch-all destination, then the other destinations, each with
   `ready_when` and waypoints (section 8). Designing the ends first tells you which threads
   you need.
5. **Threads.** Threads that carry those waypoints via `delivers`, each with an activation
   (section 9).
6. **Flags.** Declare every flag a condition will name, with its `detect` text.
7. **Conditions.** `viable_while`, `ready_when`, waypoint `done_when`, `activate_when`,
   `fail_when`, act `requires` (section 7).
8. **Cast.** `world.characters`, and each thread's `cast`.
9. **Mechanics.** Stats and tiers, relationships, inventory, progression, fragments, gates,
   pacing loop (section 10).
10. **Optional unbuilt modules.** Bonds, side threads, lore, derived values, knowing the story
    won't load for play until their engines exist.
11. **Lint to zero errors**, then `--write`.

---

## 6. Section reference

### 6.1 Top level

```json
{
  "schema_version": 3,
  "story_version": "2026-01-01.1",
  "meta": {...},
  "narration": {...},
  "world": {...},
  "protagonist": {...},
  "mechanics": {...},
  "plot": {...},
  "character_creation": [...],
  "derived": [...]
}
```

Required: `schema_version` (always `3`), `story_version`, `meta`, `narration`, `world`,
`protagonist`, `mechanics`, `plot`. `character_creation` and `derived` are optional. No other
top-level keys are allowed except `_` notes.

### 6.2 `meta`

`title` (required), `genre`, `tone`, `synopsis`, `content_rules` (list of hard content
boundaries, sent every turn).

### 6.3 `narration`

`pov` (required, e.g. `"second-person"`), `option_pov` (the voice of the choices offered to the
player, e.g. `"first-person"`), `scene_length` (`{"min": 250, "max": 350}`, in words), `style`
(list of short instructions).

Optional `scene_length_by_moment` (CR-14, not built) sets word ranges per moment:

```json
{"beats": {"respite": {"min": 250, "max": 350}},
 "directive": {"min": 450, "max": 550},
 "finale": {"min": 500, "max": 650},
 "inquiry": {"min": 120, "max": 220}}
```

The first that applies wins: finale, then a fired directive, then the previous turn's beat, then
`scene_length`. `inquiry` is judged by the narrator. Every range needs both `min` and `max`, and
beat names must come from the pacing loop.

### 6.4 `world`

- `setting_summary` (required): a paragraph.
- `rules` (required): list of strings. They are strict, non-negotiable facts about how the world
  works. A fact that matters only when a particular person or place is on the page belongs in
  lore instead.
- `locations`: `{ "<id>": {"name", "description", "connected_to": ["<id>", ...]} }`, keyed by
  id (`loc_...`). Every `connected_to` entry must be a location id.
- `factions`: `{ "<id>": {"name", "goals", "relationship_to_player"} }`.
- `characters`: `{ "<name>": {"name", "description", "role", "first_contact", "hook",
  "canon": {"<key>": "<text>"}} }`. The key and `name` must match exactly.

### 6.5 `protagonist`

```json
"protagonist": {
  "default_name": "Wren",
  "traits": ["patient"],
  "stats": {"nerve": 5},
  "starting_inventory": ["a tin of matches", {"id": "itm_001", "label": "a brass pole", "tags": ["tool"], "uses": 3}],
  "background": {"truth": "author-only notes"}
}
```

- `default_name` is required. It is the fallback when the player leaves the name blank, or the
  name itself when the player is not asked (see `opening_scene`).
- `stats` seeds stat axes. The model can never add an axis, so every axis the story uses must
  be seeded here, in a creation option's `starting_stats`, or in `mechanics.stats.axes`.
- A `starting_inventory` entry is either a plain label or an item record: `label` is required,
  plus `id`, `tags` (from `mechanics.inventory.tags`) and `uses`.
- `background` is author-only.

### 6.6 `character_creation` (optional)

A list of steps the player goes through after naming the protagonist:

```json
[{"key": "years", "label": "Years on the job", "prompt": "How long have you lit Vell Street?",
  "prompt_label": "optional short label",
  "options": [{"id": "old", "name": "Twenty winters", "tagline": "optional", "starting_stats": {"nerve": 8}}]}]
```

`key`, `label`, `prompt` and `options` are required, and each option needs `id` and `name`.
`starting_stats` add on top of `protagonist.stats`, and later steps add on top of earlier ones.
Conditions test a choice with `{"creation": {"<key>": "<option id>"}}`.

### 6.7 `plot`

Required: `main_thread`, `pacing`, `initial_scene`, `opening_scene`. Optional: `subplots`
(the threads, section 9).

- `main_thread`: `title`, `description`, `acts` (required), `plot_notes`, `max_acts` (not
  enforced yet). Each act needs `act_number` (1, 2, 3...), `title` and `description`, with
  optional `completion_signals` (what the act check looks for) and `requires` (a condition that
  must hold before this act can be completed). Acts are generated on demand after the authored
  ones run out, so author only as many as you need. Don't write `is_finale` or `optional`: the
  engine sets them.
- `pacing`: `nudge_frequency` and `act_check_frequency` (turns, required),
  `max_parallel_subplots`. The act check runs when a thread completes in the current act **or**
  every `act_check_frequency` turns, and the model then judges whether the act has resolved.
  Optional `story_clock` (CR-13, not built): `{"free_idle_streak": 3, "push_directive": "..."}`.
  Up to `free_idle_streak` turns in a row that move nothing don't count against the ending
  budget, act checks or `turn_gte`. After that the options lean forward, `push_directive` (narrator,
  one turn) fires once, and idle turns count again. See `Story_Mechanics_Update.md` CR-13.
- `initial_scene`: `location` (a location id) and `summary`.
- `opening_scene` has exactly one of two shapes:
  - `{"narration_before_name": "...", "narration_after_name": "..."}`: the player names the
    protagonist between the two parts.
  - `{"narration": "..."}`: the player is never asked, and `default_name` is the name.

  Either way, `{player_name}` is replaced with the name.

---

## 7. Conditions

One grammar serves every condition field. A condition is an object whose keys are ANDed:

| Leaf | Example | True when |
|---|---|---|
| stat | `{"stat": "nerve", "gte": 5}` (`lte`, `between: [a, b]`) | the protagonist's stat compares |
| tier | `{"tier": ["nerve", "steady"]}` | the stat is in that tier now |
| tier_reached | `{"tier_reached": ["nerve", "steady"]}` | the stat has ever reached that tier |
| relationship | `{"relationship": "Ada Quill", "tier_gte": "trusting"}` (`tier_lte`, `peak_gte`, `gte`, `lte`, `between`) | the protagonist's standing with them compares. Unmet means false. |
| bond | `{"bond": ["Ada Quill", "Wren Hale"], "tier_gte": "warm"}` (`tier_lte`, `gte`, `lte`, `between`) | one character's bond **toward** another (one-way; unopened reads 0; not built) |
| revealed | `{"revealed": "frag_0001"}` | that fragment has been revealed |
| flag | `{"flag": "lamp_nine_lit"}` | that declared flag is set (active or archived) |
| item_tag | `{"item_tag": "document"}` | the protagonist holds an item with that tag |
| creation | `{"creation": {"years": "old"}}` | that choice was made at character creation |
| turn_gte / act_gte | `{"turn_gte": 20}` | the turn or act count has reached it |
| subplot_status | `{"subplot_status": {"sp_ledger": "completed"}}` (`active`, `progressed`, `completed`, `failed`) | that thread is in that state |
| waypoints_done | `{"waypoints_done": "all"}` (or a count, or a list of ids) | this ending's waypoints are done (inside an ending only) |
| leverage_kind / leverage_label_matches | `{"leverage_kind": "access"}` | the progression ledger holds such an entry |

Combine them with `{"all": [...]}`, `{"any": [...]}` and `{"not": {...}}`, nested at most 3
deep. An empty condition, or an empty `all`/`any`, always holds.

**Polarity: what an unknown name means.** A condition can name something that doesn't exist
(a typo). Each field decides what that means based on which mistake can't be undone. Lint
reports every unknown name as an error (L10) either way.

| Field | Unknown reads | Why |
|---|---|---|
| gate `requires`, act `requires`, thread `activate_when`, ending `viable_while` | **true** (open) | A typo should cost a locked door, never a stuck story or a permanently pruned ending. |
| ending `ready_when`, waypoint `done_when`, thread `fail_when`, lore `also_when`/`unlock`, recipe `eligible_when`, derived `when` | **false** (closed) | A typo must never commit an ending, plant a waypoint, fail a thread, leak lore, start an episode or fix a wrong value. |

Prefer conditions that stay true once reached (a flag, a revealed fragment, a completed thread,
`tier_reached`, `peak_gte`) for anything that gates progress. A current stat value can drop
again.

---

## 8. Endings

```json
"endings": {
  "engine": "ending_funnel",
  "check_every": 6,
  "budget": {"open_until": 20, "narrow_until": 40, "commit_by": 60},
  "steer_top": 2,
  "finale_turns": {"min": 2, "max": 4},
  "entries": [ ... ]
}
```

**Two kinds of entry:**

- **`destination`**: an ending the story heads toward. It needs:
  - `id` and `name`;
  - `ready_when`: what commits the story to it;
  - `waypoints`: the beats that should happen on the way;
  - `arc: {title, description}`: what the finale is about. Lint warns without it.

  Optionally it has:
  - `viable_while`: the ending stays possible only while this holds. The moment it goes false,
    the ending is pruned for good.
  - `criteria`: what the commit judge checks.
  - `hint` and `epilogue`.

  **A destination with no `viable_while` is a catch-all.** At least one is required.
- **`terminal`**: a failure ending reached through the numbers at any time, never steered
  toward. It needs `id`, `name` and a `ready_when` (usually a stat, e.g. `{"stat": "nerve",
  "lte": 0}`). It may also have:
  - `criteria`: a judge confirms the scene really meets it before the story ends. Without it,
    the condition is the whole rule.
  - `min_turn`: it can't fire before this turn.
  - `arc`.

**Waypoints:** `{"id", "plant", "done_when" | "detect"}`. `plant` is the beat in plain words.
A waypoint is marked done by `done_when` (checked in code every turn) or by `detect` (judge
text the state-update pass checks). It must have at least one of them, or it can never be
done (L09).

**The budget** sets when the story may end, in turns. Every boundary is optional, and a missing
one means that phase never begins:
- before `open_until`, every viable destination is steered toward;
- from `open_until` to `narrow_until`, only the top `steer_top` by score are steered;
- from `open_until` on, a destination whose `ready_when` holds can be committed at each check
  (every `check_every` turns);
- at `commit_by`, the highest-scoring viable destination is committed regardless.

With no `open_until`, commits are possible from the start. With no `commit_by`, nothing is ever
forced.

**Wiring:** threads carry waypoints. Each waypoint should be named in some thread's `delivers`,
spelled `"<ending id>.<waypoint id>"`. A waypoint no thread carries and a destination only one
thread carries are warnings. An ending with waypoints but no carrier at all is an error.

---

## 9. Threads

`plot.subplots` is keyed by thread id (`sp_...` or `subplot_001`):

```json
"sp_ledger": {
  "title": "The Old Ledger",
  "description": "Ada's ledgers go back further than the city does.",
  "starts_active": true,
  "priority": "high",
  "span": "single_act",
  "cast": ["Ada Quill"],
  "delivers": ["ending_lit.ledger_read"],
  "fail_when": {"flag": "ledger_burned"}
}
```

| Field | Notes |
|---|---|
| `role` | `spine` (carries waypoints to an ending), `personal` (a relationship arc) or `texture` (episodic). **Omit it for spine**: spine is the default and the board drops it. |
| activation | `"starts_active": true` (active at start), **or** `"activate_when": {condition}` (starts when it holds), **or** `"starts_active": false` with no condition (started only by hand from the Subplot Manager; lint warns). Write exactly one. |
| `fail_when` | Closed condition: when it holds, the thread fails for good. A destination whose remaining waypoints are carried only by failed threads is pruned at the next check (never a catch-all). |
| `priority` | `high`, `medium` or `low`. |
| `span` | `single_act` (default) or `multi_act` (takes more beats to complete, runs across acts). |
| `delivers` | Waypoints this thread carries, `"<ending id>.<waypoint id>"`. |
| `cast` | Authored character names. Author-only for now. |
| `completion_threshold`, `ties_to_main_plot`, `on_complete` | Optional. Leave `completion_threshold` out to get the default. |

Thread progress is priced by the engine from what the model reports (touched / advanced /
decisive / resolved). `mechanics.subplots: {"engine": "weighted_threads"}` must be declared
whenever the story authors threads, or they never progress. `weights` (optional) overrides the
default prices.

---

## 10. Optional mechanics

Each block goes under `mechanics` with its `engine` key.

### Stats: `bounded_counter`

```json
"stats": {
  "engine": "bounded_counter", "floor": 0, "ceiling": 10, "visible": true,
  "axes": {"nerve": {"ceiling": 10, "tiers": [
    {"at": 0, "label": "shaken", "narration": "hands unsteady; every shadow is a person"},
    {"at": 5, "label": "steady"}]}},
  "readout": {"labels": {"nerve": "NERVE"}, "token": "[[STATS]]", "entry_format": "**{label}** {value}", "separator": " · "}
}
```

- **Tiers** give the narrator a label, and optionally a `narration` line, for the band the
  value is in. Lint warns about an axis with no tiers (L06).
- **`costs`** switches pricing for the whole story. Authoring `costs` on any axis (`{"hull.holed":
  -12, "repair.patch": 4}`) means the model names events from that closed list and the engine
  does the arithmetic. It is all-or-nothing per story: an axis with no `costs` then moves only
  by `per_turn` drift. Without `costs` anywhere, the model reports deltas.
- **`readout`**: the model writes the token and the engine substitutes the real figures, so
  numbers never drift.

### Relationships: `scored_axis`

`registers` is required: the closed list of social beats and what each is worth, e.g.
`{"kept_her_secret": 8, "lied_to_her": -10}`. Optional:
- `axis: {negative, positive}`;
- `tiers: [{at, label, narration?}]`;
- `limit`: roster size. Past it, the character closest to neutral is forgotten. Authored
  characters never are.
- `scale`, `cap_per_window`, `unreciprocated_factor`.

### Inventory: `tagged_items`

`tags` (a closed tag vocabulary, optional) and `capacity`. Starting items are in
`protagonist.starting_inventory`.

### Progression: `spendable_ledger`

`label` (e.g. `"unlocks"`), `kinds` (e.g. `["skill", "access"]`) and `prompt_hint` (what counts
as a new entry).

### Pacing loop: `beat_counter`

`beats` is required: `{"<beat>": {"definition": "...", "feeds": "<counter>", "resets":
["<counter>"]}}`. The model classifies every turn into exactly one beat.

Optional:
- `counters`: starting values.
- `tie_break`: how to choose when two beats fit.
- `rules`: `{id, watch: "<counter>", threshold, threshold_by_act, max_deferrals,
  suppress_when: ["just_fired" | "threat_present"], directive, reduced_directive}`. The engine
  uses only the first rule. `directive` may use `{counter_value}`, `{deferrals}`,
  `{unspent_leverage}` and `{queued_reveal}`.

Keep beat definitions concrete and testable. Their wording is measured.

### Gates: `precondition`

`gates: [{"id", "target": "<location id>", "requires": {condition}, "refusal_hint": "..."}]`.
While `requires` fails, a move to `target` is refused and `refusal_hint` flavours the refusal.
Write the hint as a fragment, not a finished sentence, or it gets quoted verbatim.

### Fragments: `triggered_reveal`

`entries: [{"id", "title", "trigger", "content", "after": ["<id>"]}]`. A fragment is
information held back until something happens on the page: backstory, a memory, lore, a
secret or a clue.
- `trigger` is what the judge watches for: write an event a scene can show.
- `content` is what the narrator gets once revealed.
- `title` is for you.
- `after` sets an order: this fragment waits for the listed ones.

Conditions test a fragment with `{"revealed": id}`.

### Flags (no engine key)

`"flags": {"declared": [{"id": "lamp_nine_lit", "detect": "The protagonist lit lamp nine and it
stayed lit"}]}`. Declare every flag a condition names (L10). A flag with no `detect` can never
be set.

### Tracked entity (no engine key)

`{"name", "description", "pacing_note", "canon"}`. A presence the story counts encounters
with.

### Lore: `keyed_lore` (not built)

`max_active`, and `entries: [{"id", "priority", "keys": [...], "also_when", "unlock",
"sticky_turns", "content"}]`. An entry is injected when a key appears in the player's action or
the last scene, or when `also_when` holds; `unlock` keeps it dormant until earned. Keys must be
specific (L13).

### Bonds: `scored_bonds` (not built)

One-way scores between characters: A→B and B→A are separate.

```json
"bonds": {"engine": "scored_bonds",
  "axis": {"negative": "hostile", "positive": "devoted"},
  "registers": {"covered_for_them": 8, "betrayed_them": -15},
  "tiers": [{"at": 25, "label": "warm"}, {"at": -25, "label": "wary"}],
  "seed": [{"from": "Mira Venn", "to": "Salome Vence", "score": -20}],
  "max_generated_bonds": 12, "cap_per_window": {"delta": 12, "turns": 3}}
```

- `registers` is required.
- Tiers carry `at` and `label` only; a `narration` key is a schema error.
- A pair with no seed starts at 0.

### Side threads: `episodic_threads` (not built)

Short episodes the engine starts on its own between planned beats. They carry no waypoints and
can never affect an ending.

```json
"side_threads": {"engine": "episodic_threads",
  "max_active": 2, "cooldown_turns": 8, "max_turns": 30,
  "start_after_beats": ["respite"],
  "protected": ["Lark Ferris"],
  "recipes": [{
    "id": "unrequited",
    "cast": {"a": {"from": "any"}, "b": {"from": "any"}},
    "eligible_when": {"all": [{"bond": ["a", "b"], "tier_gte": "warm"}, {"bond": ["b", "a"], "lte": 0}]},
    "premise": "one of them has started to care, and the other hasn't noticed",
    "may_move": ["bond:a,b", "bond:b,a", "relationship:a"]}],
  "player_threads": {"max_active": 1, "confirm": {"reports": 2, "within_turns": 5},
                     "abandon_after_offers": 6, "may_move": ["relationship:cast"]},
  "vignettes": {"every": 4, "seeds": ["the depot at shift change"], "subjects": ["location", "character"]}}
```

- **`start_after_beats`** takes beat names from your pacing loop. Always write it.
- **`cast` slots.** A slot is a character by default: `{"from": "any" | "authored" |
  "generated" | "<name>" | "followed", "slot": "<followed slot>"}`. It can also be a place,
  `{"kind": "location", "id": "<location id>"}`, or an item,
  `{"kind": "item", "tag": "<tag>"}`. A slot never binds a `protected` character, and naming one
  is an error.
- **`eligible_when`** and `may_move` name slots, not people.
- **`may_move`** values: `bond:<slot>,<slot>`, `relationship:<slot>`, `stat:<axis>`,
  `item:<tag>`, `leverage:<kind>`. Each must exist in the story.
- **`follows`** makes a recipe a callback to an episode that ended: `{"recipe": "<id>" | "any"
  | "player_pursuit", "outcome": ["resolved", "failed", "expired", "finale", "abandoned"],
  "min_turns_since": 20}`.
- **`player_threads`** covers a pursuit the player chose off the rails. `confirm.reports` is at
  least 2. `may_move` uses `relationship:cast` in place of slots, and `bond:` isn't allowed.
  `player_pursuit` is a reserved recipe id.
- **`default_recipe`** is on unless set to `false`. It casts by bond strength, so it needs bonds.

### Derived values: top-level `derived` (not built)

Values fixed once when character creation completes. The first rule whose `when` holds wins:

```json
"derived": [
  {"when": {"creation": {"gender": "man"}}, "set": {"lark_is": "a woman", "lark_pron": "she/her"}},
  {"set": {"lark_is": "a man", "lark_pron": "he/him"}}
]
```

Write `{lark_is}` in any text the narrator sees. End with a rule that has no `when`.

Lint checks every combination of creation choices and flags:
- any `{name}` that would be unresolved;
- a rule that never applies;
- a name that clashes with a placeholder the engine already fills (`player_name`,
  `counter_value`, `deferrals`, `unspent_leverage`, `queued_reveal`).

---

## 11. Worked example

A complete small story that lints with 0 errors, loads for play and builds a narrator prompt.
It carries one intentional warning: *The Lamp Stays Lit rests on one thread*. A real story
would give that ending a second carrier.

```json
{
  "schema_version": 3,
  "story_version": "2026-09-26.1",
  "meta": {
    "title": "The Last Lamplighter",
    "genre": "Gaslamp mystery",
    "tone": "Quiet, rain-soaked, wry",
    "synopsis": "The city is switching to electric light. You light the last gas lamps on Vell Street, and one of them keeps going out on its own.",
    "content_rules": [
      "No graphic violence."
    ]
  },
  "narration": {
    "pov": "second-person",
    "option_pov": "first-person",
    "scene_length": {
      "min": 250,
      "max": 350
    },
    "style": [
      "Open in motion, never on scene-setting."
    ]
  },
  "world": {
    "setting_summary": "Harrowgate, a river city in the last winter before the electric grid reaches the old quarter.",
    "rules": [
      "Gas lamps must be lit by hand; nothing in the city lights them any other way."
    ],
    "locations": {
      "loc_vell": {
        "name": "Vell Street",
        "description": "Nine gas lamps, one of them wrong.",
        "connected_to": [
          "loc_depot"
        ]
      },
      "loc_depot": {
        "name": "The Lamp Depot",
        "description": "Ladders, wicks and a stove that never quite warms the room.",
        "connected_to": [
          "loc_vell"
        ]
      }
    },
    "factions": {
      "faction_grid": {
        "name": "The Harrowgate Electric Company",
        "goals": "Wire the old quarter before spring.",
        "relationship_to_player": "your replacement, politely"
      }
    },
    "characters": {
      "Ada Quill": {
        "name": "Ada Quill",
        "description": "The depot's night clerk. Keeps every ledger the city forgot to ask for.",
        "role": "the one who already knows about lamp nine",
        "first_contact": "brisk, busy, curious despite herself",
        "hook": "she has a ledger entry for lamp nine dated before the street existed",
        "canon": {
          "truth": "Ada's grandmother lit lamp nine; the flame in it is hers."
        }
      }
    }
  },
  "protagonist": {
    "default_name": "Wren",
    "traits": [
      "patient",
      "stubborn"
    ],
    "stats": {
      "nerve": 5
    },
    "starting_inventory": [
      {
        "id": "itm_001",
        "label": "a brass lighting pole",
        "tags": [
          "tool"
        ]
      },
      "a tin of matches"
    ]
  },
  "character_creation": [
    {
      "key": "years",
      "label": "Years on the job",
      "prompt": "How long have you lit Vell Street?",
      "options": [
        {
          "id": "new",
          "name": "One winter",
          "starting_stats": {
            "nerve": 3
          }
        },
        {
          "id": "old",
          "name": "Twenty winters",
          "starting_stats": {
            "nerve": 8
          }
        }
      ]
    }
  ],
  "mechanics": {
    "stats": {
      "engine": "bounded_counter",
      "floor": 0,
      "ceiling": 10,
      "axes": {
        "nerve": {
          "tiers": [
            {
              "at": 0,
              "label": "shaken",
              "narration": "hands unsteady; every shadow is a person"
            },
            {
              "at": 5,
              "label": "steady"
            },
            {
              "at": 8,
              "label": "unflappable"
            }
          ]
        }
      }
    },
    "relationships": {
      "engine": "scored_axis",
      "axis": {
        "negative": "cold",
        "positive": "trusting"
      },
      "registers": {
        "kept_her_secret": 8,
        "helped_with_the_work": 5,
        "brushed_her_off": -5,
        "lied_to_her": -10
      },
      "tiers": [
        {
          "at": 40,
          "label": "trusting"
        },
        {
          "at": -40,
          "label": "cold"
        }
      ],
      "limit": 12
    },
    "inventory": {
      "engine": "tagged_items",
      "tags": [
        "tool",
        "document"
      ]
    },
    "flags": {
      "declared": [
        {
          "id": "lamp_nine_lit",
          "detect": "The protagonist lit lamp nine and it stayed lit through the night"
        },
        {
          "id": "lamp_nine_sold",
          "detect": "The protagonist let the Electric Company take lamp nine down"
        }
      ]
    },
    "revelations": {
      "engine": "triggered_reveal",
      "entries": [
        {
          "id": "frag_0001",
          "title": "Her grandmother's hand",
          "trigger": "The protagonist reads the oldest page of the depot ledger",
          "content": "Lamp nine was first lit by a woman named Quill, forty years before the street had a name."
        }
      ]
    },
    "subplots": {
      "engine": "weighted_threads"
    },
    "endings": {
      "engine": "ending_funnel",
      "check_every": 6,
      "budget": {
        "open_until": 20,
        "narrow_until": 40,
        "commit_by": 60
      },
      "finale_turns": {
        "min": 2,
        "max": 4
      },
      "entries": [
        {
          "id": "ending_lit",
          "kind": "destination",
          "name": "The Lamp Stays Lit",
          "viable_while": {
            "not": {
              "flag": "lamp_nine_sold"
            }
          },
          "ready_when": {
            "all": [
              {
                "flag": "lamp_nine_lit"
              },
              {
                "waypoints_done": "all"
              }
            ]
          },
          "arc": {
            "title": "Keeping the Flame",
            "description": "The protagonist keeps lamp nine burning against the grid."
          },
          "criteria": "The protagonist has chosen the old flame over the new light, knowing what it costs.",
          "waypoints": [
            {
              "id": "ledger_read",
              "plant": "The depot ledger names who first lit lamp nine",
              "done_when": {
                "revealed": "frag_0001"
              }
            },
            {
              "id": "ada_trusts",
              "plant": "Ada tells the protagonist what the flame is",
              "done_when": {
                "relationship": "Ada Quill",
                "tier_gte": "trusting"
              }
            }
          ]
        },
        {
          "id": "ending_grid",
          "kind": "destination",
          "name": "Electric Spring",
          "ready_when": {
            "flag": "lamp_nine_sold"
          },
          "arc": {
            "title": "Letting Go",
            "description": "The street goes electric, and the protagonist lets the last flame out."
          },
          "waypoints": [
            {
              "id": "offer_made",
              "plant": "The Electric Company makes an offer for lamp nine",
              "detect": "The Electric Company offered to buy or remove lamp nine"
            }
          ]
        },
        {
          "id": "ending_broken",
          "kind": "terminal",
          "name": "Lights Out",
          "ready_when": {
            "stat": "nerve",
            "lte": 0
          },
          "arc": {
            "title": "Lights Out",
            "description": "The protagonist's nerve fails on a dark street."
          }
        }
      ]
    }
  },
  "plot": {
    "main_thread": {
      "title": "The Last Winter of Gas",
      "description": "Find out why lamp nine will not stay lit before the grid reaches Vell Street.",
      "acts": [
        {
          "act_number": 1,
          "title": "Lamp Nine",
          "description": "The lamp goes out; nobody else seems to care.",
          "completion_signals": [
            "The protagonist has seen lamp nine go out on its own"
          ]
        }
      ]
    },
    "subplots": {
      "sp_ledger": {
        "title": "The Old Ledger",
        "description": "Ada's ledgers go back further than the city does.",
        "starts_active": true,
        "priority": "high",
        "span": "single_act",
        "cast": [
          "Ada Quill"
        ],
        "delivers": [
          "ending_lit.ledger_read",
          "ending_lit.ada_trusts"
        ]
      },
      "sp_offer": {
        "title": "The Company Man",
        "description": "The Electric Company wants lamp nine gone by spring.",
        "activate_when": {
          "turn_gte": 5
        },
        "priority": "medium",
        "span": "single_act",
        "delivers": [
          "ending_grid.offer_made"
        ]
      }
    },
    "pacing": {
      "nudge_frequency": 6,
      "act_check_frequency": 12,
      "max_parallel_subplots": 3
    },
    "initial_scene": {
      "location": "loc_vell",
      "summary": "Dusk. Lamp nine has gone out again, and it is raining."
    },
    "opening_scene": {
      "narration_before_name": "The rain has found the gap in your collar again. Lamp nine is dark.",
      "narration_after_name": "{player_name}, the depot book says, lights Vell Street. Tonight it has one lamp too many to light."
    }
  }
}
```

Things to notice:
- The catch-all is `ending_grid`: no `viable_while`.
- `ending_lit` is pruned for good if the lamp is sold. It needs both waypoints done and the flag
  set.
- One waypoint is done in code (`done_when`), the other by judge text (`detect`).
- `sp_offer` starts on turn 5 (`activate_when`); `sp_ledger` starts at once.
- Neither thread writes `role`: both are spine.
- Ada's `role` and `canon` never reach the narrator. Her `first_contact` does, until the first
  scored interaction.
- `{player_name}` in the opening is filled in by the engine.

---

## 12. Lint messages and what they mean

| Id | What it checks |
|---|---|
| `L01` | The schema. The message names the path (e.g. `mechanics.stats.ceiling`) and often a hint. A key the schema doesn't know is an error, so check the spelling and that it's in the right section. |
| `L06` / `L07` | Stat tiers. L06 (warning): an axis with no tiers, or whose lowest tier sits above the floor. L07 (error): tiers out of order, or two at the same `at`. |
| `L08` | No catch-all ending. |
| `L09` | A waypoint with neither `done_when` nor `detect`. |
| `L10` | A name that doesn't exist: an undeclared flag, a stat axis the save won't have, a fragment, character, tier, creation step or option, thread, location, recipe, beat, tag or leverage kind. Also an unresolved `{name}`. |
| `L13` | A lore key that's too generic or shared. |
| `L16` | A dangling id: a `connected_to`, a gate target, the opening location, a fragment's `after`, a thread `cast`. |
| `structural` | Endings nothing leads to, uncarried waypoints, threads that never activate, a spine thread that carries nothing. |
| `arc` | An ending with no `arc`. |
| `flags`, `bonds`, `side_threads`, `derived` | Hygiene for those blocks: duplicates, setups that can never start anything, unused values. |

---

## 13. Done checklist

- [ ] `python3 scripts/lint_template.py <slug>` reports **0 errors**.
- [ ] `Loads for play: yes`, or you know exactly which unbuilt module refuses it and why that's
      intended.
- [ ] No `The board would rewrite ...` line.
- [ ] Every warning read, and either fixed or explained in a `_` note.
- [ ] No canon, twist or truth in a narrator-visible field (section 4).
- [ ] Every character name spelled identically everywhere.
- [ ] At least one catch-all destination. Every destination's waypoints are carried by a
      thread.
- [ ] `--write` run last, so the file is canonical and `story_version` is bumped.
- [ ] A private story is committed to the private submodule, never to this repository.

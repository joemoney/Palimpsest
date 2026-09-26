# Handoff: storyboard overhaul, state as of 2026-09-26

This is for the next session (and the author) picking up the storyboard-first overhaul after a
long cloud session. It lists what landed, what's open, the decisions waiting on the author,
and how to verify things. Read it after `CLAUDE.md`. Delete or trim it once its items are
done: it's a snapshot, not a spec.

**Branch:** `appV3`, pushed, clean. The last commit of the session is `7712a1e` (CR-14 on the
storyboard).

**Private stories submodule** (`stories/private`, repo `joemoney/palimpsest-stories`):
- The parent pins `ce430c0` ("Missing Core: make the two new endings CR-05 kinds").
- That commit is on the submodule's own **`appV3`** branch, pushed, and **not merged into its
  `master`** (master is `2b27813`).
- After cloning: `git submodule update --init`. To commit story edits, check out `appV3`
  inside `stories/private`.

---

## 1. Where things stand

The overhaul follows CLAUDE.md's build order: **the storyboard leads, the engine follows**.
Authoring surfaces are built ahead of the engines, and a story that uses an unbuilt one fails
loudly. The board now has an editor for every change request that has been designed, CR-01
to CR-14, except CR-07/08/09 (not designed yet). Engine work (Phase S5) is demand-driven:
build the piece real authoring needs next.

**Board tabs:** Diagram (lanes view), Matrix, Cast, Fragments, World, Side threads, Forms.

**What's on the board and where:**

| Area | Where on the board | Engine |
|---|---|---|
| Endings (destinations, catch-all, terminals, waypoints, budget, timeline) | Diagram → endings column; health panel → Ending funnel settings; Timeline | **Built** (`ending_funnel`): pruning, scoring, commits, forced commit, terminals |
| Threads (roles, activation, carries, fail, cast) | Diagram → lanes | Built: `activate_when`, `fail_when`, carrier prune. `cast` is author-only. |
| Acts / main thread | Diagram → acts strip | Built (`max_acts` not enforced) |
| Protagonist, creation, opening, name capture toggle | Diagram → Start column | Built |
| Derived values (CR-04) | Protagonist card | **Not built**: `mechanics.validate()` refuses a story with `derived` |
| Fragments | Fragments tab | Built (narrator heading is now neutral "REVEALED SO FAR") |
| World, locations, factions | World tab | Built |
| Lore (CR-06) | World tab → Lore | **Not built** (refuses load) |
| Cast, first contact, hook, canon, thread membership | Cast tab | Built |
| Bonds (CR-11) | Cast tab → Bonds | **Not built** (refuses load) |
| Side threads, recipes, vignettes (CR-11), player-started threads (CR-12) | Side threads tab | **Not built** (refuses load) |
| Stat tiers, on_enter (CR-01) | Diagram → stat bar → tier ladder | Tiers built; `on_enter` not built (warns) |
| Story clock / idle turns (CR-13) | Health panel → Ending funnel settings → Idle turns (also Forms → Pacing) | **Not built** (warns; every turn still counts) |
| Scene length by moment (CR-14) | Forms → Narration → Scene length | **Not built** (warns; `scene_length` default still used) |
| Everything else (narration, pacing, stats, relationships, inventory, progression, pacing loop, gates, tracked entity) | Forms tab, schema-driven | Built |

**Docs written this session:**
- `docs/Agent_Authoring_Manual.md`: writing `template.json` directly, for agents. Its worked
  example is pinned by `test/test_lint_template_cli.py`.
- `docs/Assistant_CoAuthor_Manual.md`: a project-knowledge file for an AI assistant helping a
  human on the board. It lists the board's exact labels, so keep it in sync when UI labels
  change.
- `docs/Story_Mechanics_Update.md`: CR-13 (story clock) and CR-14 (scene length by moment)
  added.
- `docs/analysis_and_plans/AUTHORING_TOOL/AUTHORING_TOOL_PHASES.md`: a "landed" note per
  piece, with the decisions made in passing.
- `README.md`: the 18-step storyboard guide, current.

**Tooling:** `python3 scripts/lint_template.py <slug | path> [--json] [--write]` is the
command-line Validate. It also reports whether the story loads for play, which modules aren't
built, whether the board would rewrite anything, and canonical formatting.

---

## 2. Decisions waiting on the author

Each was flagged in the session and not yet answered. Don't resolve them silently.

1. **CR-11 `start_after_beats` defaults to `respite`.** That encodes a creative decision in an
   engine constant, which CLAUDE.md rules out, and `example` has no `respite` beat. Lint
   warns for now. The proposal is to make it required when `episodic_threads` is built.
2. **The server-side leak check (D3).** The canon-leak check (L03) exists only as the board's
   client-side JS. D3 says checks run server-side. The recommendation is to move it into
   `author_lint` as part of S4, where the CR-03 leak test needs it anyway. The alternative is
   to accept it as a recorded exception.
3. **CR-11 decisions made in passing** (confirm or change):
   - recipe `eligible_when` is fail-**closed**;
   - callback outcomes are named `resolved` / `failed` / `expired` / `finale` / `abandoned`
     (`expired` is new).
4. **CR-13 open question.** Should idle turns also be free for the pacing nudge? It currently
   counts every turn, on purpose.
5. **Earlier S5 decisions recorded in AUTHORING_TOOL_PHASES.md** ("flag if wrong"): activation
   ignores `max_parallel_subplots`; nothing activates after endgame; failure runs before
   activation; waypoint keys are qualified; a blank budget boundary means that phase never
   begins; the carrier prune is the conservative reading; both judges are Tier C.

---

## 3. Open work, in suggested order

### 3.1 Story content (the author said: rework New Babel and example once the storyboard design is locked)

- **example** has 2 errors:
  - `schema_version` is still 2 (a board Save or `lint_template.py --write` stamps 3);
  - no endings at all (L08). It needs `mechanics.endings` with a catch-all (D6: the player
    can't end a story).
- **new_babel** has 4 errors:
  - `schema_version` 2;
  - no endings (L08);
  - `mechanics.stats.ceiling` is `null`;
  - `tracked_entity.canon.harvested_v1_record` is an object where the schema wants text.
    Move it to a `_` note to keep it.

  Its pacing directive also says "Deliver it as the protagonist experiencing it", which suits
  memory fragments, not lore fragments.
- **the_missing_core** (loads, 0 errors):
  - *The Handover*'s `ready_when` contradicts its `viable_while`.
  - None of the five endings has an `arc`, so each finale starts from the ending's name alone.
  - Two of The Seat's waypoints have no carrier.
  - CR-04's acceptance ("delete Lark's gender rule from `world.rules`") can't be met: its
    creation has no gender step.
- **Legacy gate `requires`** still use pre-CR-02 spellings. The evaluator accepts both; rewrite
  them when each template is next edited.

### 3.2 Storyboard design still to do (lock the design before the engine rework)

- **CR-07** (thread completion rewards: `on_complete.stat_events` exists on the thread panel),
  **CR-08** (one-shot directives on relationship tier transitions) and **CR-09**
  (tier-scoped narration exemplars). All three need design before they can be authored.
- **Phase S4, not started:**
  - `x-visibility` annotations in the schema (the board's visibility badges are hard-coded
    in JS);
  - the Preview tab (the real narrator prompt for a sample state);
  - the rest of the linter: L01, L06–L10, L13 and L16 exist, the other eight don't;
  - lore highlighting in the sample state;
  - the CR-11 eligible-castings preview (needs the engine's binding enumeration).
- **Schema gap noticed:** the engine reads `narration.option_count` (`story_engine.py`), but
  the schema doesn't define it, so the board can't author it.

### 3.3 Engine (Phase S5, demand-driven), roughly by likely demand

1. **Declared flags' `detect` text is never passed to the state-update pass**, so a declared
   flag is set only if the model happens to use its exact id. Every thread, act or ending
   gated on a flag depends on this. Top of the list.
2. **D5 / D6 retirement:** delete `failure_conditions` / `triggered_ending` and the player's
   end-story path (`END_STORY_PHRASES`, `handle_end_story_request`, the help entry). Keep
   `STATUS_LABELS` / `DEFAULT_STEP_ESTIMATE_SECONDS` mirrored (`test_status_labels.py`).
3. **Ending steering:** waypoints into act generation, `hint` / `plant` in nudges, drive
   nudges, carrier priority and early activation, texture-only generation. Also the
   `epilogue` display and `max_acts`.
4. **CR-01 `on_enter`**, then **CR-03 visibility enforcement** in the loader.
5. **CR-04 `derived`:** resolve when creation completes, store the values in the save,
   substitute `{name}` wherever prompts are built (including the pacing directive's
   `.format()` and the opening). Then lift the refusal in `mechanics.validate()`. The
   playable projection must also drop `derived` for preview and playtest.
6. **CR-06 `keyed_lore`**, **CR-11 `scored_bonds` / `episodic_threads`**, **CR-12 player
   threads** (and retire `player_driven_goals`: `plot_manager add-goal`, the goal list, the
   `PLAYER GOAL:` nudge).
7. **CR-13 story clock** and **CR-14 scene length by moment**: specs with acceptance criteria
   are in `Story_Mechanics_Update.md`.
8. **Deferred smaller ideas:**
   - derive `OPENROUTER_MAX_TOKENS` from the story's largest word maximum rather than a fixed
     4096;
   - log words per turn (CR-14's measurement);
   - a code-side scene-length trim at a paragraph boundary. The author declined adding it to
     CR-14; revisit only if asked.

Each engine piece, when built, must:
- register its engine and delete its "not built" warning in `mechanics.validate()`;
- drop the board's *not built* chip;
- add a fixture to `test/fixtures/` that omits the module, with a marker in
  `test_genre_conformance.py` (CLAUDE.md, Testing);
- stay within the observation budget (`scripts/measure_baseline.py`).

---

## 4. How to verify

```bash
python3 test/run_all.py                         # 66 files, all passing at handoff
python3 scripts/lint_template.py the_missing_core
python3 scripts/lint_template.py new_babel
python3 scripts/lint_template.py example
```

Running the board locally (it needs an author account in `data/`):
```bash
OPENROUTER_API_KEY=dummy GOOGLE_API_KEY=dummy AUTHOR_ENABLED=1 AUTHOR_USER_IDS=<user id> \
FLASK_SECRET_KEY=dev python3 -c "import sys; sys.path.insert(0,'backend'); import app; app.app.run(port=8123)"
```

**Checks this session ran by hand after every board change.** The scripts lived in the cloud
scratch space and are **not** in the repo, so recreate them if needed:
- **No-op check:** open each tab and panel for each story. Then
  `author_model.from_board_model(raw, getBoardModel())` must equal
  `from_board_model(raw, to_board_model(raw))`. Viewing must never change the template.
- **Contrast audit:** Playwright walks every tab and panel in light and dark schemes and
  reports any text below 4.5:1. The author treats contrast as a hard requirement ("dark
  background with black text is a no no").
- **Syntax check of the board script:** extract the `<script>` blocks from
  `frontend/author_board.html`, blank the Jinja expressions, and run `node --check`.

Gotchas:
- Flask caches templates, so restart the server after editing `author_board.html`.
- `pkill -f` with a pattern that matches your own shell kills the shell. Kill by PID instead.
- Playwright's Chromium is at `/opt/pw-browsers/chromium-1194/chrome-linux/chrome` in the cloud
  image. On the homelab, use whatever Playwright installs.

---

## 5. Working agreements observed this session

- **Flag conflicts with CLAUDE.md** and lay out options. Don't comply or refuse silently.
- **Author before engine.** A surface can land without its engine, as long as it fails loudly
  (refuses load) or visibly (warns, *not built* chip).
- **Commit messages** explain what and why. Each commit runs the full suite first. Push to
  `appV3`.
- **Private stories** stay in the submodule, never in this repo's history.
- **UI copy:** plain words, no jargon in labels. Every narrator-visible field shows who sees it.

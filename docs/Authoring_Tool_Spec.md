# Story Authoring Tool — Spec (v2, Storyboard-Centred)
 
**Status:** Rev A · **Date:** 2026-09-22  
**Companion:** `Design_Overhaul.md` (storyboard-first architecture), `STORY_MECHANICS_UPDATE.md` (CR-01–CR-10)  
**Scope decision:** single author (Joe). No multi-user accounts, no publishing workflow, no guardrails for untrusted authors.
 
---
 
## 0. Summary
 
The authoring tool is the storyboard. When you open `/author/<slug>`, you land on the diagram — the node-and-edge canvas that shows the shape of the story. Everything else — section editing, the linter, AI assist, the ending simulator, the playtest pane, the narrator preview — lives in tabs and panels accessible from the storyboard, never the other way around.
 
This inversion reflects the design overhaul: the storyboard is the source of truth; the schema serialises what the board expresses; the engine executes what the schema encodes. The tool's architecture follows the same order.
 
**Parts, in order of value:**
 
1. **The storyboard** — diagram, matrix and focus-mode views of the story's full structure, with direct manipulation of every structural field.
2. **A formal template schema** with visibility annotations. The board, the engine loader and the leak test all read it.
3. **A linter** — the health panel is the live linter; the CLI is its headless twin.
4. **Section-editing forms** — a tab in the inspector panel for editing fields the diagram doesn't surface (prose, rules, character details).
5. **AI assist per field**, with suggestions shown as diffs and never written without approval.
6. **Two test harnesses:** a code-only **ending simulator** and a **playtest pane** with a full turn trace.
---
 
## 1. Goals and non-goals
 
**Goals**
- Author a new story from a blank template or a copy, without hand-editing JSON for routine fields.
- See the story's structure — threads, endings, delivery chains, funnel timing — on one surface, at all times.
- Catch structural mistakes (leaks, stranding gates, unreachable endings, undeclared flags) before a playthrough does.
- Use the model to draft and critique, not to decide. Every suggestion is reviewed.
- See exactly what each prompt audience receives for a given state.
**Non-goals (for now)**
- Multiple authors, permissions, drafts per user, or a public creator flow.
- Image, audio or other media assets.
- Committing to git from the UI. The tool writes files; you commit from the story repo as today.
- Replacing the raw JSON. Every section keeps a raw view, because templates carry `_authored` notes and one-off keys a form shouldn't have to anticipate.
---
 
## 2. Architecture
 
- **Where it runs:** an `/author` Flask blueprint in the existing app, rendered with the same htmx patterns as `/play` (fragments, out-of-band swaps, no hand-written fetch code).
- **Entry point:** `/author/<slug>` serves the storyboard page. There is no separate "editor" route — the storyboard IS the editor.
- **Access:** enabled only when `AUTHOR_ENABLED=1`, and only for account ids listed in `AUTHOR_USER_IDS`. The app is reachable through the Cloudflare tunnel, and these routes write files and spend API credit, so they must not be reachable by other accounts even though there are none today.
- **Files:**
  - The tool edits `template.json` directly — there is no separate draft file. The board loads it into memory on open; nothing touches disk until Save.
  - **Save** validates, runs the linter, shows a diff against the on-disk `template.json`, then overwrites it and bumps `story_version` (`YYYY-MM-DD.N`). Git is the undo history.
  - **Save** also regenerates the story's `README.md` synopsis section from `meta.synopsis`, so the two never drift.
  - Errors block a save; warnings don't.
- **Story discovery:** `new_babel/README.md` says private stories now mount at `stories/private/<slug>/`, while the engine `README.md` and `state_store.list_stories()` still describe `stories/<slug>/`. The catalog should scan both, and the docs should be reconciled before the tool depends on either.
- **LLM access:** through the existing `call_llm` / `call_llm_json` seam, using an `AUTHOR_MODEL` setting (default: the judgment-tier model). Tests stub it through `test/_llm_stubs.py` as usual.
- **Engine reuse:** the linter, the simulator and the preview import the real engine functions (condition evaluator, tier resolution, funnel scoring, prompt assembly). They are not reimplemented, so the tool can never disagree with the engine.
---
 
## 3. Template schema
 
**File:** `schema/template.v2.schema.json` (JSON Schema 2020-12), in the engine repo.
 
**Custom annotations**
 
| Annotation | Values | Used by |
|---|---|---|
| `x-visibility` | `narrator` / `judge` / `author` | Engine loader allowlist (CR-03), leak test, editor badges, assist context rules |
| `x-assist` | Style rules for the field, e.g. `"fragment"`, `"event-not-meaning"`, `"second-person"`, `max_words` | Assist system prompt, linter |
| `x-ref` | What an id field points at, e.g. `"revelations.entries[].id"` | Editor pickers, dangling-reference lint |
| `x-sim` | Simulator hints, e.g. relative event frequency | Ending simulator (§8) |
 
**Rules**
- Keys starting with `_` are allowed anywhere and are always `author` visibility. That covers every existing `_authored`, `_threshold_note` and similar key without schema changes.
- Any other unknown key is an **error**. This is what catches a typo like `viable_whlie` that would otherwise be silently ignored.
- Module blocks (`mechanics.stats`, `endings`, `lore` …) are discriminated on `engine`, so each module version gets its own subschema.
- The condition grammar (CR-02) is one shared `$defs/condition`.
**Acceptance:** both current templates validate (after the mechanics-update migrations); a template with an unknown non-`_` key fails with the path to the key.
 
---
 
## 4. The storyboard
 
The storyboard is the tool's chrome. Every other component is accessed through it. It has three **canvas views** (tabs across the top) and an **inspector panel** (the right-hand column, with its own tabs).
 
### 4.1 Canvas views
 
All three views show the same model. Editing in one view updates the others.
 
#### 4.1a Diagram view (default)
 
The node-and-edge canvas. This is what opens when you land on `/author/<slug>`.
 
**Layout:** four lanes, left to right: Start · Threads open at start · Threads unlocked later · Endings, with failure endings in their own band under the destinations.
 
**Box shapes encode kind:**
- **Start:** a dark rounded block with the creation choices as chips.
- **Thread:** a plain card with a role chip (spine / personal / texture).
- **Destination ending:** a double outline in the ending's colour, plus one dot per waypoint (filled when a thread carries it, hollow amber when nothing does).
- **Failure ending:** a dashed card showing its stat trigger.
**Lines encode template fields.** The board has no data of its own; every line is a field in the template.
 
| Line | Template field |
|---|---|
| Start → thread, solid | `starts_active: true` |
| Start or thread → thread, dashed, labelled with the condition | `activate_when` |
| Thread → ending, in the ending's colour, labelled with waypoint ids | `delivers: ["<ending>.<waypoint>"]` (CR-10) |
 
**Interactions**
- Click a box to select it (opens its detail in the inspector panel) and highlight every path into and out of it; dim the rest.
- Drag boxes; pan and zoom the canvas.
- Drag from a box's port to connect. Dropping on an ending auto-assigns its first uncarried waypoint.
- Click a line or label to edit or remove the link.
- Auto-layout (BFS depth from start) snaps nodes to columns.
**Positions** live in an author-only `_storyboard` key, which the engine never reads (CR-03).
 
#### 4.1b Matrix view
 
The thread-by-waypoint grid. Each dot is a `delivers` entry. An empty column is a waypoint no thread carries; an empty row is a thread that delivers nothing.
 
**The matrix is an editing surface.** Clicking a dot toggles a `delivers` edge, which syncs back to the diagram. This is the fastest way to review coverage and fix gaps.
 
#### 4.1c Lore view
 
Cards for each lore entry with their `keys`, `also_when` conditions, `priority` and `sticky_turns`. When the sample-state bar is active, cards highlight which entries would be injected, with the `max_active` cutoff shown visually (top entries highlighted, rest greyed).
 
Editing a lore card updates the template's `lore` block. Add, remove and reorder from this view.
 
### 4.2 Inspector panel (right column)
 
The inspector panel is context-sensitive: its contents depend on what's selected on the canvas. It has its own tab bar across the top.
 
**Inspector tabs:**
 
| Tab | What it shows | When it appears |
|---|---|---|
| **Detail** | The selected node's or edge's editable fields (see §4.3) | Always (default when a node is selected) |
| **Forms** | Section-editing forms for template sections not on the canvas (§5) | Always |
| **Assist** | AI assist for the currently focused field (§7) | Always |
| **Lint** | Full linter output, scoped to the selected node or to the whole template (§6) | Always |
| **Preview** | Narrator / judge / state-update prompt assembly for a sample state (§4.5) | Always |
| **Simulator** | Ending simulator controls and results (§8) | Always |
| **Playtest** | Playtest pane with full turn trace (§9) | Always |
 
### 4.3 Detail tab (node/edge inspector)
 
What appears in the Detail tab depends on the selected element:
 
**Start node selected:**
- Creation choices (chips, add/remove/reorder)
- Derived variables list (CR-04)
- Opening scene narration and OPTIONS block
**Thread node selected:**
- Title, role chip (spine / personal / texture)
- `starts_active` toggle
- `activate_when` condition (CR-02 condition editor with plain-English rendering)
- `fail_when` condition
- `delivers` list (which waypoints this thread carries), with add/remove; each entry links to the target ending's waypoint
- `on_complete` rewards — stat event chips (CR-07)
- Acts list (add/remove/reorder), each with its pacing note
**Destination ending selected:**
- Title, description, colour
- Waypoint rows, each with:
  - `plant` text (narrator visibility badge)
  - `detect` text (judge visibility badge)
  - `done_when` condition
  - Carrier thread(s) — which `delivers` edges point here (read-only; edit from the thread or the matrix)
- `viable_while` condition
- `ready_when` condition
- `hint` text (narrator badge)
- `criteria` text (judge badge)
- `arc` text (narrator, post-commit badge)
- `epilogue` text (author/UI badge)
**Failure ending selected:**
- Title, stat trigger, `min_turn`
- `arc` text, `epilogue` text
**Edge selected:**
- Type (solid/dashed), condition label
- Source and target nodes (read-only; delete and redraw to change)
**Every text field shows its visibility badge** (narrator / judge / author). This is the single most useful affordance: before typing, you see whether the text will reach the model every turn.
 
**Condition fields:** edited with the CR-02 structured editor (dropdowns for leaf type, comparator, value; `all`/`any`/`not` nesting up to depth 3), with a plain-English rendering underneath and a live true/false against the sample state.
 
### 4.4 Sample-state bar
 
A persistent bar at the bottom of the storyboard (visible across all canvas views). Controls:
- Sliders for each stat axis (value + current tier shown)
- Toggle chips for flags
- Turn counter
- Revealed fragments
- Relationship values for named characters
- Ending state (which are pruned, which are ready, which are committed)
When active, conditions evaluate live across the board: true conditions show their edges in full colour, false ones dim. The bar turns the storyboard into a "what if" simulator without running the engine.
 
### 4.5 Preview tab
 
Pick or edit the sample state (syncs with the sample-state bar), then see:
- The assembled **narrator prompt**, **act-generator prompt** and **state-update prompt**, split by section with token counts, from the real prompt builders.
- Active tier narration lines per axis.
- Injected lore entries (and why: key match or condition).
- Steered waypoints and their `plant` texts.
- Pacing nudges.
This is how you check a tier line, a lore trigger or a waypoint's `plant` without playing. The preview is static — no LLM call needed.
 
A **"What the narrator sees"** button assembles just the narrator slice and shows token counts. A **leak check** (CR-03) runs live: if any author-visibility text appears in a narrator-visibility field, it flags it inline.
 
### 4.6 Health panel
 
A floating widget (bottom-left of the diagram view, collapsible). It shows a live subset of the linter focused on structural flow:
 
| Check | What it catches |
|---|---|
| Uncarried waypoint (hollow amber dot) | Every waypoint must have at least one `delivers` edge |
| Single-carrier destination | A destination resting on one thread is fragile |
| Thread never activates | No `starts_active` and no incoming unlock/open edge |
| Spine carries nothing | A `role: spine` thread with empty `delivers` |
| Texture carries something | A `role: texture` thread with non-empty `delivers` |
| No catch-all | No destination without `viable_while` |
| Delivery with no waypoint chosen | An edge to an ending that doesn't specify which waypoint |
 
Clicking a health issue selects the relevant node on the canvas and opens its Detail tab.
 
### 4.7 Timeline bar
 
A horizontal bar above or below the diagram (toggled), showing the turn budget divided into funnel phases:
- `0–open_until` (Open)
- `open_until–narrow_until` (Narrow)
- `narrow_until–commit_by` (Commit window)
- `commit_by+` (Forced / Finale)
Each destination ending projects onto the timeline as a horizontal band showing when it's steered (`steer_top` in Narrow phase) and when it could commit. Terminal endings show their `min_turn` as a vertical tick.
 
Draggable phase boundaries adjust `budget.open_until`, `budget.narrow_until` and `budget.commit_by` directly.
 
When simulator results (§8) are available, overlay commit-turn distributions as sparklines per ending.
 
### 4.8 Stat sidebar
 
A compact collapsible panel (left side of the diagram) listing all `bounded_counter` axes with their current tier (from sample state). Click an axis to open its **tier ladder widget**: a vertical strip with draggable tier boundaries, `on_enter` directive hooks shown as pins. Editing a tier updates the schema's `tiers` array.
 
---
 
## 5. Forms tab (section editing)
 
The Forms tab in the inspector panel provides schema-driven editing for every template section that doesn't have a direct canvas representation. This is where you edit prose, rules, character details and other non-structural fields.
 
**Sections** mirror the template:
- Meta & narration (title, synopsis, tone, style, `narration_rules`)
- World (setting, locations, factions, rules)
- Characters (per character: name, role, personality, dialogue notes, relationship registers)
- Protagonist & creation (background, attributes, creation choices — also editable from the Start node detail)
- Mechanics (stats & tiers — also accessible from the stat sidebar; relationships, pacing loop, gates, progression, revelations, tracked entity, declared flags)
- Plot (main thread, acts, subplots — also editable from thread node details)
- Endings (also editable from ending node details)
- Derived vars
**Form rendering:** Jinja macros driven by the schema: strings, long text, enums, integers, arrays of objects with add/remove/reorder, id pickers from `x-ref`, and condition fields.
 
**Every field shows its visibility badge** (narrator / judge / author).
 
**Form | JSON toggle:** every section has a raw JSON view (plain textarea validated server-side on blur). A vendored editor (CodeMirror) can come later.
 
**Two-way sync:** editing a field in the Forms tab that also appears on the diagram (e.g. changing a thread's `delivers` list, editing an ending's waypoints) updates the diagram in real time, and vice versa. The working copy is one model; the views are projections.
 
---
 
## 6. Linter
 
The health panel (§4.6) is the live linter on the canvas. The Lint tab in the inspector shows the **full** linter output, and the CLI (`python -m tools.lint_template <slug>`) is its headless twin. All three share the same checks.
 
| ID | Check | Severity |
|----|-------|----------|
| L01 | Schema validation, including unknown non-`_` keys | error |
| L02 | `x-assist: fragment` fields (hints, `refusal_hint`, `plant`) read as a finished sentence (capitalised start and terminal period, or more than one sentence) | warning |
| L03 | Canon leak: any 40+ character run of `author` text appears in a `narrator` or `judge` field | error |
| L04 | Criteria leak: any 40+ character run of `judge` text appears in a `narrator` field | error |
| L05 | Gate stranding: with every gate closed, some location is unreachable from the start location | error |
| L06 | A stat axis without tiers, or tiers that don't start at the floor | warning |
| L07 | Tiers unsorted or with duplicate `at` values | error |
| L08 | No catch-all destination (a destination without `viable_while`) | error |
| L09 | A waypoint with neither `done_when` nor `detect` | error |
| L10 | A condition referencing an undeclared flag, unknown stat, unknown fragment or unknown character | error |
| L11 | An unresolved `{var}` in a narrator field | error |
| L12 | A stat-event name mentioned in `world.rules` text that doesn't exist in any axis's `costs` | warning |
| L13 | A lore key that is too generic (under 3 characters, or a stopword) or shared by several entries | warning |
| L14 | Always-on narrator prompt (rules + style + tier lines + entity) over a token budget (default 2,500) | warning |
| L15 | `opening_scene` narration whose OPTIONS block doesn't parse (3 lines, `label || prose`) | error |
| L16 | Dangling `x-ref` ids (subplot, fragment or location ids that don't exist) | error |
 
L03 and L04 share their implementation with the CR-03 leak test.
 
The health panel (§4.6) runs a subset: L08, L09, L10, L16 and the structural flow checks. The Lint tab runs all of them, filterable by severity and by section.
 
---
 
## 7. AI assist
 
Accessed from the **Assist tab** in the inspector panel. The tab is context-sensitive: it operates on the currently focused field in the Detail tab or Forms tab.
 
**Endpoint:** `POST /author/<slug>/assist` with `{path, mode, instruction?}`, where `path` is a JSON pointer to the target field.
 
**Modes**
 
| Mode | Output | Example |
|---|---|---|
| `draft` | A value for an empty field | Write `tracked_entity.pacing_note` |
| `variants` | Three alternatives | Three `refusal_hint` fragments for a gate |
| `tighten` | A shorter rewrite with the meaning unchanged | Cut a 90-word rule to its always-true core |
| `critique` | Notes only, no replacement | "Does this tier ladder escalate evenly?" |
| `derive` | Structured content generated from other fields | See below |
 
**`derive` recipes** (the most valuable mode):
- **Rule prose → stat tiers:** turn a rule's bands into a `tiers` array (CR-01).
- **Long rule → lore entry:** propose `keys`, `also_when` and `content` for a rule that only matters when a character is present (CR-06).
- **Ending → waypoints:** from an ending's `arc`, `criteria` and the relevant canon, propose 2–4 waypoints with `plant` written as events, not meanings, plus `done_when` or `detect` (CR-05).
- **Ending description → `ready_when`:** a condition draft built from the story's actual stats, flags and fragments.
- **`plant` → `detect`:** the judge-side recognition text for a waypoint.
- **Subplot → completion reward:** pick stat events from the axis `costs` (CR-07).
- **Ending → carrier threads:** propose threads that would plant an ending's uncarried waypoints (CR-10).
- **Thread → deliveries:** suggest which existing waypoints a thread could plausibly carry.
**Context assembly**
- **Always included:** `meta`, `narration.style`, `world.setting_summary`.
- **Per target:** the containing section, plus an explicit related-fields map (e.g. waypoints ← that ending's `arc`, `criteria`, `protagonist.background`; tiers ← the axis's `costs` and the matching `world.rules` entry).
- **Canon is excluded by default.** For a `narrator` or `judge` target, canon fields are left out of the assembled context unless the author explicitly checks a **"use canon"** box on that request. The protection is on the input as well as the output: even when canon is included by request, every suggestion for a `narrator` or `judge` field still runs through the leak check (L03/L04) before it is shown, and a flagged suggestion displays the overlapping canon text.
**House rules in the assist system prompt:** the `x-assist` rules for the target field, plus the standing lessons from the templates:
- Hints and `plant`s are fragments, not finished sentences.
- Narrator-facing text states what happens, never what it means.
- Nothing may confirm what the protagonist has only guessed.
- Match the story's tone string.
**Output handling**
- The model returns JSON for the target's subschema. It is validated, then shown as a diff with **Accept / Edit / Discard**. Nothing is written without a click.
- Each call shows its token count and estimated cost.
- The last 40 suggestions per story are kept in `template.assist_log.json` (gitignored) so a discarded variant can be recovered.
---
 
## 8. Ending simulator
 
Accessed from the **Simulator tab** in the inspector panel.
 
**Question it answers:** given the authored costs, drifts, tiers and conditions, does every ending get a fair chance inside the budget, and does the funnel behave as intended?
 
**How it works (code only, no LLM)**
- Runs N playthroughs (default 2,000) of `budget.commit_by` turns each.
- **Each simulated turn:**
  - Samples 0–2 stat events per axis from `costs`, weighted by `x-sim` frequencies, and applies `per_turn` drift and clamping. Once real play data exists (from S5 onward — see §12.4), the sampling weights are calibrated from logged stat events per turn rather than left uniform.
  - Random-walks relationships for the named characters using their `registers`.
  - Reveals fragments with a configurable per-turn probability, and completes declared flags and waypoints with hazard rates (higher for steered destinations, matching the engine's steering).
- **Funnel:** runs the **real** CR-05 functions (pruning, scoring, steering, commit, forced commit) with a stub judge. The stub accepts the first ready destination and returns `null` with a configurable probability.
- **Terminals:** checked per turn, with a stub confirm probability.
**Report (shown in the Simulator tab):**
- **Per destination:** % pruned (and median prune turn), % ever ready, % committed, % force-committed, median commit turn.
- **Per terminal:** % fired, median turn.
- **Per stat axis:** time spent in each tier.
- **Budget sliders** for `open_until` / `narrow_until` / `commit_by` to re-run instantly (syncs with the timeline bar §4.7).
**Flags it raises automatically:**
- A destination that is never ready in any run.
- A catch-all that wins more than 50% of runs.
- A terminal that fires in more than 25% of runs before `open_until`.
- A tier that is never reached.
**Overlay on the canvas:** when results are available, ending nodes show commit-rate badges, and the timeline bar shows commit-turn distributions as sparklines. Thread nodes can show activation and failure rates.
 
**Replay mode:** runs the funnel over an actual save's recorded stat events and flags to show what the engine would have done. This is the same logged data used to calibrate the sampling weights above.
 
---
 
## 9. Playtest pane
 
Accessed from the **Playtest tab** in the inspector panel. Opens as a split view: the storyboard stays visible on the left, the playtest on the right, so you can watch the board react to each turn.
 
- **Save:** each playtest gets a scratch save under a reserved user id, `author:<slug>`, loaded from the storyboard's current **in-memory** model — including any unsaved edits — not from a separate draft file. Playtesting a change doesn't require saving it to `template.json` first.
- **Every turn played** appends its stat events to the story's event log (see §12.4), so playtests feed the same calibration data as real play, unless the author marks the run as a scratch run to exclude it.
- **Runs the real `take_turn`** with an optional cheaper narration model (`AUTHOR_PLAYTEST_MODEL`), so a playtest costs cents rather than dollars.
- **Turn trace:** alongside the scene, the pane shows:
  - The assembled prompt for each audience, by section, with token counts.
  - The state-update JSON as returned.
  - Stat deltas, tier changes and `on_enter` directives fired.
  - Lore entries injected, and why (key or condition).
  - Endings state: scores, steered set, waypoints hit, prunes, commits.
- **Live board sync:** the sample-state bar updates to match the playtest's current state, so conditions on the diagram light up in real time as you play.
- **State controls:** set stats, flags, fragments, relationships or turn number directly, to jump to the interesting part.
- **Autopilot:** play N turns picking options at random, with a cost estimate shown before starting.
**Engine change required:** `take_turn` returns an optional `trace` object when called with `trace=True`.
 
---
 
## 10. Engine changes this tool depends on
 
| Change | Also needed for |
|---|---|
| Template JSON Schema with `x-visibility` | CR-03 |
| Load a template from an in-memory model (unsaved board state), not just a file path | Playtest against unsaved edits (§9) |
| Pure, importable condition / tier / funnel functions | CR-01, CR-02, CR-05 |
| `take_turn(..., trace=True)` | Debugging real saves |
| Per-call model override | Playtest model; `AUTHOR_MODEL` |
| Story catalog scanning `stories/` and `stories/private/` | Reconciling the mount path |
| Per-turn stat event logging | Simulator calibration and replay mode (§8) |
| README synopsis generated from `meta.synopsis` on save | Keeps `README.md` and the template in sync (§2) |
 
---
 
## 11. Phasing
 
Phasing aligns with `DESIGN_OVERHAUL.md`'s S1–S6 build order. Each storyboard phase adds capabilities to the tool.
 
| Phase | Delivers | Maps to |
|---|---|---|
| **S1** | Template loader/writer (direct edit, no draft file), board mounted in Flask, schema stub, health panel wired to real data, position persistence, README synopsis generation on save | The storyboard reads and writes `template.json` |
| **S2** | Condition editor on the board, CR-02 evaluator (JS), sample-state bar, live condition evaluation | Conditions become real and testable |
| **S3** | Stat sidebar with tier ladder widget, timeline bar with draggable phase boundaries, terminal tick marks | Stats, tiers and funnel timing are visible and editable |
| **S4** | Lore view, visibility badges on all text fields, narrator preview tab, leak check, full linter (CLI + Lint tab) | The author can see what the narrator receives |
| **S5** | Engine implementation: condition evaluator (Python), tier resolution, ending funnel, thread carriers, keyed lore, visibility enforcement, creation-derived variables, per-turn stat event logging | The engine executes what the storyboard designs |
| **S6** | Ending simulator (tab + canvas overlay, calibrated from logged play data), playtest pane (tab + live board sync), AI assist (tab, canon opt-in, 40-entry log), thread completion rewards, relationship transitions, Forms tab for non-structural fields | The storyboard becomes the full authoring environment |
 
**S1 can start now.** The schema stub and linter CLI (the old T1) ship as part of S1, because the board's save endpoint validates against the schema.
 
---
 
## 12. Decisions and open questions
 
1. **Draft file vs direct edit — decided.** No draft file. The board holds edits in memory; Save writes `template.json` directly and overwrites the on-disk file. Git is the undo history.
2. **Assist log — decided.** Keep the last 40 suggestions per story in `template.assist_log.json`.
3. **Canon in assist context — decided.** The stricter alternative: canon is excluded from assist context for `narrator`/`judge` targets by default, included only when the author checks "use canon" for that request.
4. **Simulator calibration — decided.** Log stat events per turn from actual play (real saves and playtest runs alike, from S5 onward) and use that log to calibrate the simulator's sampling weights and to drive replay mode, rather than assuming uniform frequencies indefinitely.
5. **README.md sync — decided.** The tool generates the README's synopsis section from `meta.synopsis` on every save, rather than keeping two hand-maintained copies.
6. **Inspector panel sizing.** The playtest pane needs more width than the detail editor. Split-view mode (storyboard left, playtest right) may need a collapsible canvas. Test with the prototype in S1.
7. **Board as the only editor.** For S1–S4, the storyboard and its inspector panel are the primary editor. The full Forms tab (§5) is deferred to S6 unless the inspector's Detail tab proves insufficient for a field. The raw JSON escape hatch stays available from S1.
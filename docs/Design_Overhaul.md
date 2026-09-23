# Palimpsest Design Overhaul — Storyboard-First Architecture
 
**Status:** Draft for review · **Date:** 2026-09-22  
**Supersedes:** `STORY_MECHANICS_UPDATE.md` (CR-01 through CR-10) and `AUTHORING_TOOL_SPEC.md` remain valid as reference, but this document reframes the build order and the source of truth.  
**Premise:** The storyboard is not a visualisation of the schema. The schema is a serialisation of what the storyboard can express.
 
---
 
## 0. The inversion
 
The two companion documents were written schema-first: design a JSON structure (CR-01 through CR-10), then build a tool that edits it (AUTHORING_TOOL_SPEC). That order made sense as analysis — the templates had to be studied before anything could be drawn. But as a build plan it creates a problem: the schema is large, abstract and untested against a real authoring surface. If the storyboard prototype reveals that an author thinks about threads and endings differently from how CR-10 models them, the schema has to be reworked after it was already implemented in the engine.
 
The overhaul inverts the sequence:
 
```
OLD:  Schema design  →  Engine code  →  Authoring tool (renders schema)
NEW:  Storyboard tool  →  Schema (serialises what the board expresses)  →  Engine (executes what the schema encodes)
```
 
The storyboard is already a working prototype. It has nodes, edges, a matrix view, auto-layout, focus mode and a health panel. Every interaction it supports implies a schema field and an engine behaviour. The job now is to take each thing the author can *do* on the board and derive the JSON and the engine rule from it, rather than the reverse.
 
This does not discard the CR documents. Every CR maps to something the storyboard needs to express. What changes is the order: the board interaction is designed and tested first, and the schema follows.
 
---
 
## 1. What the storyboard already decides
 
The v2 prototype (artifact, 2026-09-22) supports these operations. Each one implies a schema field and an engine behaviour.
 
### 1.1 Nodes
 
| Board action | Implied schema | Implied engine behaviour |
|---|---|---|
| Place a **Start** node | `creation` block with choice chips | Opening scene generation; derived variables (CR-04) |
| Place a **Thread** node with a role chip (spine / personal / texture) | `plot.subplots.<id>` with `role` field | Pacing nudge selection; generation limits per role (CR-10) |
| Place a **Destination ending** node | `endings.entries[]` with `kind: "destination"` | Ending funnel: scoring, steering, commitment (CR-05) |
| Place a **Terminal ending** node with a stat trigger | `endings.entries[]` with `kind: "terminal"` | Per-turn code check + judge confirmation (CR-05) |
| Drag a node to a column | `_storyboard.positions` (author-only) | No engine effect; layout is authoring metadata |
| Auto-layout snaps depth | BFS depth from start via opens/unlocks edges | Validates that every node is reachable |
 
### 1.2 Edges
 
| Board action | Implied schema field | Engine behaviour |
|---|---|---|
| Solid line: Start → Thread | `subplot.starts_active: true` | Thread is live from turn 1 |
| Dashed line: Node → Thread, with condition label | `subplot.activate_when` (CR-02 condition) | Thread activates when condition is met |
| Coloured line: Thread → Ending waypoint row | `subplot.delivers: ["ending.waypoint"]` (CR-10) | Steering: waypoint's `plant` rides the thread's pacing nudge |
| Click a matrix dot | Same as above (adds/removes a `delivers` entry) | Same |
| Drag from port to ending, auto-assign first uncarried waypoint | `delivers` with first hollow-dot waypoint | Same |
 
### 1.3 Ending detail (side panel)
 
| Panel field | Schema path | Engine behaviour |
|---|---|---|
| Waypoint rows inside the ending box | `endings.entries[].waypoints[]` | Waypoint detection in state-update pass; steering in act generation |
| Waypoint `plant` text | `waypoints[].plant` (narrator visibility) | Injected into act-generation prompt as a setup event |
| Waypoint `detect` text | `waypoints[].detect` (judge visibility) | Passed to state-update pass for recognition |
| Waypoint `done_when` condition | `waypoints[].done_when` (CR-02) | Code-only check, no LLM needed |
| `viable_while` condition | `endings.entries[].viable_while` | Pruning at each funnel check |
| `ready_when` condition | `endings.entries[].ready_when` | Commitment eligibility |
| `hint` text | `endings.entries[].hint` (narrator) | Diegetic fragment in pacing nudges |
| `criteria` text | `endings.entries[].criteria` (judge) | Commit-judge prompt; never reaches narrator |
| `arc` text | `endings.entries[].arc` (narrator after commit) | Finale narration direction |
| `epilogue` text | `endings.entries[].epilogue` (author/UI only) | Shown after THE END |
 
### 1.4 Health panel
 
| Health check | What it validates | Schema invariant |
|---|---|---|
| Uncarried waypoint (hollow amber dot) | Every waypoint must have at least one `delivers` edge | Structural completeness |
| Single-carrier destination | A destination resting on one thread is fragile | Authoring warning, not a schema error |
| Thread never activates | No `starts_active` and no incoming unlock/open edge | Dead node; `activate_when` is unreachable |
| Spine carries nothing | A `role: spine` thread with empty `delivers` | Role mismatch |
| Texture carries something | A `role: texture` thread with non-empty `delivers` | Role mismatch |
| No catch-all | No destination without `viable_while` | Load-time error (CR-05) |
 
### 1.5 Matrix view
 
The matrix is the same data as the diagram edges, displayed as a thread-by-waypoint grid. Each dot is a `delivers` entry. The matrix makes coverage gaps visible at a glance — an empty column is a waypoint no thread carries; an empty row is a thread that delivers nothing.
 
**The matrix is also an editing surface.** Clicking a dot toggles a `delivers` edge, which syncs back to the diagram. This means the matrix is not read-only reporting; it is a second way to edit the same model.
 
### 1.6 Focus mode
 
Selecting a node and enabling focus hides everything outside its connected subgraph. This is how an author checks one ending's full delivery chain without the rest of the board interfering.
 
---
 
## 2. What the storyboard does not yet express
 
These are the CR features that have no board interaction yet. Each needs a storyboard-level design before the schema is locked.
 
### 2.1 Stat axes and tiers (CR-01)
 
**What the author needs to see:** which stat thresholds matter to which endings, and where tier transitions fire directives.
 
**Proposed board expression:**
- Stat axes appear as a **compact sidebar or overlay**, not as nodes. They are properties of the world, not story-flow elements.
- Each ending's `ready_when` and `viable_while` conditions reference stat thresholds. The side panel already shows conditions; it should render the stat references as linked chips that, on hover, show the tier ladder and highlight where the condition sits.
- Terminal endings show their stat trigger directly on the node (already implemented: "FRAME = 0", "TRACE >= 90").
- A **tier ladder widget** in the side panel when editing a stat: vertical strip with tier bands, `on_enter` directive hooks shown as pins. Drag thresholds to adjust values.
**Schema follows:** the tier array shape from CR-01 is unchanged; the storyboard just provides a visual way to set `at` values and preview which tier a sample state falls in.
 
### 2.2 Conditions (CR-02)
 
**What the author needs to see:** plain-English conditions wherever they appear, with live evaluation against a sample state.
 
**Proposed board expression:**
- Edge labels on dashed (unlock) lines already show condition text. Replace free-text with the CR-02 condition grammar, rendered as plain English ("REACH >= 20" → `{stat: "reach", gte: 20}`).
- The side panel's condition editor renders each leaf as a row with dropdowns (stat/flag/relationship/revealed + comparator + value), with `all`/`any`/`not` nesting up to depth 3.
- A **sample-state bar** at the bottom of the board lets the author set stat values and flags. Conditions evaluate live: true conditions show their edges in full colour, false ones dim. This turns the storyboard into a "what if" simulator without running the engine.
### 2.3 Visibility enforcement (CR-03)
 
**What the author needs to see:** which text reaches which audience.
 
**Proposed board expression:**
- Every text field in the side panel carries a **visibility badge** (narrator / judge / author), as already specified in AUTHORING_TOOL_SPEC §4.
- The storyboard doesn't need to visualise visibility on the diagram itself — it's a property of fields, not of flow.
- A **"What the narrator sees"** preview button in the side panel assembles the narrator prompt for the selected node's context (its thread's `plant`, the active tier narrations, lore) and displays it with token counts.
### 2.4 Lore entries (CR-06)
 
**What the author needs to see:** which lore entries trigger in which situations, and when they overlap.
 
**Proposed board expression:**
- Lore entries are not flow elements and don't belong on the diagram. They're contextual — they activate based on keywords and conditions.
- A **Lore panel** (separate tab alongside Diagram and Matrix) showing all entries as cards, with their keys, `also_when` conditions, priority and `sticky_turns`.
- When the sample-state bar is active, lore cards highlight which would be injected, and the `max_active` cutoff is shown visually (top 3 highlighted, rest greyed).
### 2.5 Funnel phases and budget (CR-05 timing)
 
**What the author needs to see:** how the story's pacing budget maps to the funnel phases (Open → Narrow → Commit → Finale).
 
**Proposed board expression:**
- A **timeline bar** above or below the diagram, showing the turn budget divided into phases: `0–open_until` (Open), `open_until–narrow_until` (Narrow), `narrow_until–commit_by` (Commit window), `commit_by+` (Forced / Finale).
- Each destination ending projects onto the timeline as a horizontal band showing when it's steered (top `steer_top` in Narrow phase) and when it could commit. Terminal endings show their `min_turn` as a vertical tick.
- When simulator results (AUTHORING_TOOL_SPEC §6) are available, overlay commit-turn distributions as sparklines per ending.
- Draggable phase boundaries adjust `budget.open_until`, `budget.narrow_until` and `budget.commit_by` directly.
### 2.6 Relationship tiers and transitions (CR-08)
 
**Proposed board expression:**
- Relationships are character properties, not flow elements. They live in the character editor (side panel when a character-linked thread is selected).
- Transition hooks (`drifting_out` with `sets_flag`) link to the storyboard through the flag system: the flag they set can be a `viable_while` condition on an ending, which is already visualised.
- The storyboard shows the downstream effect (ending pruned when flag is set) without needing to draw the relationship itself.
### 2.7 Thread completion rewards (CR-07)
 
**Proposed board expression:**
- On a thread node, a **reward chip** showing the stat event(s) that fire on completion.
- A thread's `on_complete` overrides are set in the side panel.
- The health panel warns if a reward references a stat event that doesn't exist.
---
 
## 3. Build order
 
The storyboard-first order groups work by what the author needs to do, not by schema feature.
 
### Phase S1 — The board becomes the source of truth
 
**Goal:** the storyboard reads and writes real `template.json` files, not a demo model.
 
| Task | Details |
|---|---|
| **Template loader** | Read a `template.json` into the storyboard's node/edge model. Every thread, ending, waypoint and edge already has a 1:1 mapping (§1). |
| **Template writer** | Export the storyboard model back to `template.json`. Round-trip: load → save → load produces identical output. |
| **Position persistence** | Store `_storyboard` positions in the template (author-only key, CR-03 safe). |
| **Schema stub** | A minimal JSON Schema that validates what the storyboard can express *today*: nodes with kinds, edges with types, waypoints with `plant`/`detect`/`done_when`, conditions as JSON. Not the full CR schema — just enough for round-trip validation. |
 
**What this unlocks:** the author can open a real story, rearrange its structure, add/remove `delivers` edges, and save. The storyboard is no longer a prototype; it's an editor.
 
### Phase S2 — Conditions and sample state
 
**Goal:** conditions on the board become real CR-02 grammar, evaluable against a sample state.
 
| Task | Details |
|---|---|
| **Condition editor** | Replace free-text condition labels with a structured editor (dropdowns for leaf type, comparator, value). Renders as plain English on the diagram. |
| **Condition evaluator** | Pure JS port of the CR-02 evaluator (stat/flag/relationship/revealed/waypoints_done leaves, all/any/not combinators, proximity scoring). |
| **Sample-state bar** | Bottom bar with sliders for each stat axis, toggle chips for flags, turn counter. Conditions evaluate live and edges dim/brighten. |
| **Schema extension** | The condition grammar definition (`$defs/condition`) becomes part of the schema. `activate_when`, `viable_while`, `ready_when`, `done_when` all validate against it. |
 
**What this unlocks:** the author can ask "what happens if SYNC is at 85 and Lark has departed?" and see the board react — which endings are pruned, which threads activate, which waypoints are done. This is the storyboard-as-simulator, before the engine exists.
 
### Phase S3 — Stats, tiers and the timeline
 
**Goal:** stat axes and the ending funnel's budget are visible and editable on the board.
 
| Task | Details |
|---|---|
| **Stat sidebar** | Compact panel listing all `bounded_counter` axes with their current tier (from sample state). Click to open the tier ladder editor. |
| **Tier ladder widget** | Vertical strip with draggable tier boundaries, `on_enter` hooks as pins. Editing a tier updates the schema's `tiers` array. |
| **Timeline bar** | Horizontal bar showing Open / Narrow / Commit / Finale phases. Draggable boundaries write `budget` values. Ending bands show when each destination is steered. |
| **Terminal tick marks** | Terminal endings show `min_turn` on the timeline. |
| **Schema extension** | `bounded_counter.tiers` array, `endings.budget` block, `endings.check_every`, `endings.steer_top` — all from CR-01/CR-05, now editable from the board. |
 
**What this unlocks:** the author designs the story's pacing and stat progression visually. Tier thresholds, funnel timing and ending readiness are all on one surface.
 
### Phase S4 — Lore, visibility and the narrator preview
 
**Goal:** the author can see what the narrator actually receives.
 
| Task | Details |
|---|---|
| **Lore tab** | Third view alongside Diagram and Matrix. Cards for each lore entry with keys, `also_when`, priority, `sticky_turns`. Sample-state highlighting shows which would be injected. |
| **Visibility badges** | Every text field in every side panel carries a narrator/judge/author badge. Colour-coded. |
| **Narrator preview** | "What the narrator sees" button assembles the prompt for the current sample state: active tier narrations, injected lore, `plant` texts from steered waypoints, pacing nudges. Token count shown. |
| **Leak check** | Live CR-03 check: if any author-visibility text appears in a narrator-visibility field, flag it in the health panel. |
| **Schema extension** | `x-visibility` annotations on every schema field. `lore` block from CR-06. |
 
**What this unlocks:** the author can verify that nothing leaks before ever running the engine. The narrator preview is a static prompt — no LLM call needed.
 
### Phase S5 — Engine implementation
 
**Goal:** the engine executes what the storyboard designs.
 
Only now does engine code get written, because by this point:
- The schema is complete and tested by the storyboard's round-trip validation.
- The condition evaluator exists (ported from the board's JS to Python).
- The tier resolver, funnel scorer and steering logic have been exercised by the sample-state bar and timeline.
- The narrator prompt assembly has been verified by the preview.
| Task | Details |
|---|---|
| **Condition evaluator (Python)** | Port from S2's JS evaluator. Shared by gates, endings, lore, tiers. |
| **Tier resolution** | After stat deltas, resolve to highest tier with `at <= value`. Inject one line per axis into narrator prompt. |
| **Ending funnel** | Open/Narrow/Commit/Finale phases, waypoint tracking, scoring, steering, commit judge, forced commit. All from CR-05, validated against the storyboard's timeline. |
| **Thread carriers** | `delivers`, `activate_when`, `fail_when`, role-based generation limits. All from CR-10, validated against the storyboard's edges and matrix. |
| **Keyed lore** | Key matching, `also_when`, stickiness, priority-based injection. From CR-06, validated against the lore tab. |
| **Visibility enforcement** | Per-section allowlists from `x-visibility`. Leak test in CI. |
| **Creation-derived variables** | `derived` list, `{var}` interpolation. From CR-04. |
 
### Phase S6 — Simulator, playtest and AI assist
 
**Goal:** the storyboard becomes the full authoring environment from AUTHORING_TOOL_SPEC.
 
| Task | Details |
|---|---|
| **Ending simulator** | Code-only Monte Carlo (AUTHORING_TOOL_SPEC §6). Results overlay on the timeline as sparklines and on ending nodes as commit-rate badges. |
| **Playtest pane** | Full turn trace with real engine, cheaper model. State controls for jumping to interesting states. |
| **AI assist** | Per-field drafting, critique, tightening. The `derive` recipes (rule→tiers, ending→waypoints, thread→deliveries) are the most valuable. |
| **Thread completion rewards** | Reward chips on thread nodes; `on_complete` stat events in side panel. CR-07. |
| **Relationship transitions** | Character panel with tier hooks, `sets_flag` linking to storyboard via condition system. CR-08. |
| **Narration exemplars** | CR-09, if A/B testing justifies the token cost. |
 
---
 
## 4. Schema evolution principle
 
The schema is never designed in isolation. Every field in `template.v2.schema.json` must trace to one of:
 
1. **A storyboard interaction** — something the author does on the board (place a node, draw an edge, edit a side panel field, drag a tier threshold).
2. **An engine contract** — something the engine must know to execute (a `detect` text for the state-update pass, a `criteria` for the commit judge).
3. **Authoring metadata** — something only the author sees (`_storyboard` positions, `canon`, `_authored` notes).
A field that doesn't fit any of these categories is a field that shouldn't exist. This prevents the schema from accumulating speculative structure that no tool edits and no engine reads.
 
**Version bumps:** the schema version (`schema_version`) increments when a storyboard phase ships and the round-trip changes. Each version is additive: the loader accepts all prior versions and migrates forward.
 
---
 
## 5. What stays from the companion documents
 
Both `STORY_MECHANICS_UPDATE.md` and `AUTHORING_TOOL_SPEC.md` remain accurate as analysis. The CRs describe real problems and real solutions. What changes:
 
| From | Status | Notes |
|---|---|---|
| CR-01 (Tiers) | Valid. Built in S3. | Tier ladder widget drives the schema shape. |
| CR-02 (Conditions) | Valid. Built in S2. | Condition editor on the board is the reference implementation. |
| CR-03 (Visibility) | Valid. Built in S4. | Badges and leak check on the board, then enforced in engine at S5. |
| CR-04 (Derived vars) | Valid. Built in S5. | Lightweight; no board interaction needed. |
| CR-05 (Ending funnel) | Valid. Split across S1 (nodes/edges), S2 (conditions), S3 (timeline/budget), S5 (engine). | The funnel's design is the storyboard's core structure. |
| CR-06 (Lore) | Valid. Built in S4. | Lore tab on the board. |
| CR-07 (Thread rewards) | Valid. Built in S6. | Reward chips on thread nodes. |
| CR-08 (Relationship transitions) | Valid. Built in S6. | Character panel, flag linkage. |
| CR-09 (Exemplars) | Deferred pending measurement. | Not a storyboard feature. |
| CR-10 (Threads as carriers) | Valid. Already expressed in S1 prototype. | `delivers` edges and the matrix are the CR-10 interface. |
| AUTHORING_TOOL_SPEC §3 (Schema) | Absorbed into S1–S4. | Schema grows with the board. |
| AUTHORING_TOOL_SPEC §4 (Editor) | Absorbed into S1–S6. | The storyboard IS the editor; section forms are its side panels. |
| AUTHORING_TOOL_SPEC §4a (Storyboard) | Superseded by this document. | The storyboard is no longer §4a of the editor. It is the whole tool. |
| AUTHORING_TOOL_SPEC §5 (AI assist) | Absorbed into S6. | Assist works on storyboard fields. |
| AUTHORING_TOOL_SPEC §6 (Simulator) | Absorbed into S6. | Results overlay on the board. |
| AUTHORING_TOOL_SPEC §7 (Linter) | Split: health panel (S1+), leak check (S4), CLI lint (S5). | The health panel is the live linter. |
| AUTHORING_TOOL_SPEC §8 (Playtest) | Absorbed into S6. | Playtest pane alongside the board. |
 
---
 
## 6. Architecture: where the storyboard lives
 
The prototype is a single HTML file with inline JS. For S1, it moves into the Flask app.
 
| Component | Location | Notes |
|---|---|---|
| **Storyboard page** | `/author/<slug>/board` in the existing Flask app | htmx for load/save; the canvas, diagram, matrix and panels remain vanilla JS + inline SVG (no framework). |
| **Template API** | `/author/<slug>/api/load`, `/save`, `/validate` | JSON endpoints. Load returns the model; save validates, diffs and writes `template.json`. |
| **Condition evaluator (JS)** | `static/js/conditions.js`, vendored | Pure functions, no dependencies. Also used by the sample-state bar and the matrix. |
| **Schema** | `schema/template.v2.schema.json` in the engine repo | Single source of truth for validation. The storyboard's save endpoint validates against it. |
| **Condition evaluator (Python)** | `backend/conditions.py` | Port of the JS evaluator. Shared by engine, linter and simulator. |
| **Storyboard positions** | `_storyboard` key in `template.json` | Author-only, engine-ignored. The only board-specific persistence. |
 
The prototype's `localStorage` persistence (for the demo model) is replaced by the template API. Everything else — rendering, drag, pan, zoom, SVG edges, matrix, focus mode, auto-layout — carries over as-is.
 
---
 
## 7. Open questions (updated)
 
Carried forward from both companion documents, reframed for the storyboard-first order:
 
1. **Draft file vs direct edit.** S1 writes `template.json` directly (git is the undo). If this is too risky for a single author, add `template.draft.json` later. Decision: start without it.
2. **Budget values.** `open_until 40 / narrow_until 90 / commit_by 140` — the timeline bar (S3) makes these easy to experiment with. Set initial values, tune with the simulator (S6).
3. **Permanent vs revivable pruning.** Start permanent. If a story needs revival, add `revive_when` to the schema; the storyboard shows it as a dashed re-entry edge.
4. **Carrier fallback order (CR-10).** Generate spine threads only as a last resort. The matrix makes uncarried waypoints visible enough that the author can fix them before the engine has to improvise.
5. **Commit judge model.** Use the flagship tier. Commitments are irreversible and happen 1-3 times per run. The timeline shows when they can happen; the author budgets for them.
6. **Simulator calibration.** Start with uniform event frequencies. Log stat events per turn in real saves from S5 onward, and feed them into replay mode in S6.
7. **Board as the only editor.** For S1-S4, the storyboard and its side panels are the primary editor. Section-form pages (AUTHORING_TOOL_SPEC §4 left rail) are deferred unless the board's side panels prove insufficient. The raw JSON escape hatch stays.
---
 
## 8. First steps
 
S1 requires:
 
1. **Template loader/writer** — parse `template.json` into the storyboard's `{nodes, edges, endings, model}` shape, and serialise back. This is the most important piece: it makes the round trip real.
2. **Mount the prototype in Flask** — `/author/<slug>/board` serves the storyboard HTML. Load and save buttons call the template API.
3. **Schema stub** — validates the fields the storyboard can currently edit: node kinds, edge types, `delivers` arrays, waypoint `plant`/`detect`/`done_when`, `viable_while`, `ready_when`. Unknown non-`_` keys are errors. This is the seed of `template.v2.schema.json`.
4. **Health panel wired to real data** — the prototype's health checks already work on the demo model. They need to run on the loaded template's data.
Once these ship, the storyboard is the tool. Everything else extends it.
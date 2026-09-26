# Story Mechanics Update — Schema v2.1 / Engine Design
 
**Status:** Draft for review · **Revised:** 2026-09-23 (r2: endings redesigned as a funnel · r3: CR-10 threads as carriers · r4: CR-11 side threads and bonds; endings mandatory · r5: CR-11 beyond characters, CR-12 player-started side threads)
**Suggested path:** `docs/analysis_and_plans/ENGINE_V2/STORY_MECHANICS_UPDATE.md`
**Inputs:** `the_missing_core/template.json` (story_version 2026-09-17.1), `new_babel/template.json` (2026-09-04.1), Wrtn Crack creator guide (stats, keyword book, endings, story settings)
**Companion:** `Authoring_Tool_Spec.md` (the editor that produces these templates)
 
---
 
## 0. Summary
 
The Missing Core is the most demanding template so far, and it shows where the v2 module set runs out. Its `_authored` notes repeatedly describe the same failure: *a promise prose alone cannot keep*. The template then either builds an engine module for the promise (gate, delta_block, QUORUM tiers) or leaves it in `world.rules`, where it costs tokens every turn and gets diluted.
 
This update takes the remaining prose-carried mechanics and gives each an engine home. Crack's creator tooling supplies precedent for most of them.
 
**Decision recorded in r2 (2026-09-22):** endless continuation is retired for any story that authors endings. Playtesting showed that with no destination, generated acts compound drift, because each one is conditioned on the last plus a lossy summary. Stories now carry an authored set of endings that **steer the middle of the story**, narrow as play proceeds, and are **committed silently** by the engine. This supersedes the "There is no built-in stopping point" section of `docs/Narrative_Engine_Spec.md` for templates with an `endings` block. The `example` story, which has none, keeps today's behaviour.
 
**Superseded in r4 (2026-09-23):** the player can no longer end the story (Authoring Tool Spec decision D6). Every story must author `mechanics.endings` with a catch-all, including `example`, and the "Player end story" touch point in CR-05 is withdrawn.
 
| ID | Change | Crack precedent | Priority |
|----|--------|-----------------|----------|
| CR-01 | Tiers on every stat axis, current tier only in prompt | Stat level bands | P0 |
| CR-02 | Shared condition grammar (gates, endings, lore, derived vars) | Ending rule groups (AND/OR) | P0 |
| CR-03 | Visibility enforcement: author / judge / narrator fields + leak test | — (template gap) | P0 |
| CR-05 | **Ending funnel** (`ending_funnel`): authored destinations steer acts, prune, commit silently | Ending settings | **P0** |
| CR-04 | Creation-derived variables and `{var}` interpolation | Per-start settings | P1 |
| CR-06 | Triggered lore (`keyed_lore`), max 3 active | Keyword book | P1 |
| CR-07 | Thread completion rewards | — (template gap) | P2 |
| CR-08 | Relationship tier transitions (one-shot directives) | Stat level bands | P2 |
| CR-09 | Narration exemplars, tier-scoped | Example dialogues | P2 |
| CR-10 | **Threads as carriers**: subplot roles, `delivers`, `activate_when`, failure; generation limited to texture | — (follows from CR-05) | **P0** |
| CR-11 | **Side threads and NPC bonds**: one-way NPC-to-NPC scores; a parallel track of AI-written episodes, started and ended by state; r5 adds location and item casts, stat/item/leverage `may_move`, vignettes and callbacks | — (template gap) | P1 |
| CR-12 | Player-started side threads: detected pursuits, confirmed in code; replaces `player_driven_goals` | — (template gap) | P2 |
| CR-13 | **Story clock**: turns that move nothing don't spend the story's budget, up to an authored free streak; then the options lean forward and the world pushes | — (template gap) | P1 |
| CR-14 | Scene length by moment: the word range follows what the turn is (a question, a quiet beat, a fired directive, the finale) instead of one range for every scene | — (template gap) | P2 |
 
None of these adds a per-turn LLM call. CR-11 adds an occasional generation call, only when a side thread starts. CR-05 adds a judge call only at commitment (typically one to three per playthrough) and when a terminal ending's code gate trips. Waypoint detection rides on the existing state-update pass.
 
**Visibility caveat:** the synced `Palimpsest` master still shows the v1 `story_engine.py`. It contains no `_section_tracked_entity`, `_character_record`, `_unspent_leverage_text` or module registry, even though both templates reference them. This document is therefore written against the **template contract**, not the current v2 code. Check the "Engine behaviour" sections against the real module registry before implementing.
 
---
 
## 1. What the templates already do well
 
Modules in use, as authored:
 
- **Threads and relationships:** `weighted_threads` subplots; `scored_axis` relationships with named registers, `unreciprocated_factor` and narrated tiers.
- **Items and stats:** `tagged_items` inventory; `bounded_counter` stats with priced `costs` events, `per_turn` drift, a visible readout and a `delta_block`. Tiers exist on **QUORUM only**.
- **Reveals and pacing:** `triggered_reveal` fragments; `beat_counter` pacing loop with threshold rules, deferrals and a `reduced_directive`.
- **Progression and access:** `spendable_ledger` progression (`leverage` / `unlocks`); `precondition` gates using fragment `refusal_hint`s.
- **Entity and canon:** `tracked_entity`, with `canon` blocks that are author-facing by convention.
The discipline in these notes is the engine's strongest asset: measured thresholds, fragment-not-sentence hints, and canon kept out of per-turn prompts. Every CR below is designed to keep it intact.
 
---
 
## 2. Findings: mechanics still carried by prose
 
Evidence from `the_missing_core`. `new_babel` has the same gaps in simpler form.
 
| # | Where | What it asks prose to do | Problem |
|---|-------|--------------------------|---------|
| F1 | `world.rules` TRACE rule | Four bands (0 / 25 / 55 / 80) with different world behaviour | Sent in full every turn; the narrator must read the stat and select the band itself. QUORUM got a tier module and TRACE didn't. |
| F2 | `world.rules` REACH rule | Five bands of physical range | Only the ≥20 threshold is enforced (the `tally_reach` gate); the other four bands are prose. |
| F3 | `world.rules` SYNC rule | Escalating symptoms as SYNC rises | No escalation is expressed anywhere; "always takes something" has no scale. |
| F4 | `world.rules` Lark gender rule | A conditional on gender × class, resolved by the narrator every turn | Deterministic logic run by an LLM; fragile and long, and needed only when Lark is on the page. |
| F5 | `world.rules` "crew near neutral is leaving" | Detect a relationship falling from warm back to ~0, then stage an exit | Needs history (was warm, now isn't), which the narrator can't see. |
| F6 | `world.rules` "THE SYSTEM ISSUES THE WORK" | Thread completion pays out restoration *in the same scene* as a stat_event | Completion is detected after narration, so the payment can't be enforced. |
| F7 | `characters.Lark Ferris.canon.the_dilemma`, `protagonist.background.the_missing_core` | The whole endgame (stay the core / hand over Lark / find the core) | No engine path reaches it. The only ending is the player typing "end story", which generates an arc from the summary and ignores the authored ones. Worse, nothing steers the acts *toward* any of it, so long runs drift away from the designed endgame entirely. |
| F8 | `canon` blocks and `_authored` notes | "Engine reads exactly N keys — everything else is inert and must stay that way" | Enforced by convention and comments, not by code or tests. |
| F9 | `new_babel` `attention_level` | Rising entity attention | Has costs but no tiers, so the narrator gets a bare number. |
 
---
 
## 3. Change requests
 
### CR-01 — Tiers on every stat axis (P0)
 
**Crack precedent:** up to 4 value bands per stat, each carrying a short behaviour prompt. Only the active band steers the model.
 
**Schema.** Generalise the existing QUORUM `tiers` shape to every `bounded_counter` axis. Nothing about QUORUM changes.
 
```json
"trace": {
  "per_turn": -1, "per_turn_interval": 5,
  "costs": { "...": "unchanged" },
  "tiers": [
    {"at": 0,  "label": "junk",    "narration": "The Garland ignores the Ninth-Hand. It reads as ordinary salvage junk."},
    {"at": 25, "label": "noticed", "narration": "Other crews and the Board have noticed the ship runs better than its papers say. Expect questions, not action."},
    {"at": 55, "label": "filed",   "narration": "The Meridian Ossuary has a file. Its people can appear anywhere the ship docks."},
    {"at": 80, "label": "loud",    "narration": "The loudest thing in the Garland. Something that is not a faction is listening.",
     "on_enter": {"once": true, "directive": "the belt goes quiet around the ship in a way no instrument can explain"}}
  ]
}
```
 
**Engine behaviour**
- After stat deltas are applied and clamped, resolve each axis to its highest tier with `at ≤ value`.
- The narrator prompt receives one line per axis: `TRACE (filed): <narration>`. It never receives the band table.
- `on_enter` fires only on an upward crossing, is recorded in `state.mechanics.stats.tier_log`, and respects `once`. The directive is fragment-style, injected for one turn, and follows the same hint discipline as `refusal_hint`.
- `tier_log` feeds CR-02 conditions (e.g. "reached `loud` at any point").
**Migration**
- TRACE, REACH and SYNC band prose moves out of `world.rules` into tiers. Each rule shrinks to its always-true core, e.g. *"TRACE is how loud the vessel is. The System reports it accurately and without comment."*
- `new_babel` gains tiers on `attention_level`, `neural_load` and `health` (sketch in §4.2).
**Cost:** net negative. The band prose leaves every turn; one line per axis replaces it.
 
**Acceptance**
- The prompt for a TRACE=60 state contains the `filed` narration and none of the other tiers.
- A 54→56 crossing fires `on_enter` exactly once; 56→54→56 does not refire when `once` is set.
- Existing QUORUM tier behaviour (including `clause_max_words`) is byte-identical.
---
 
### CR-02 — Shared condition grammar (P0)
 
Gates, endings, waypoints, lore unlocks, derived vars and relationship transitions all need the same "is X true about the state?" check. Define it once.
 
```json
{"all": [
  {"stat": "sync", "gte": 70},
  {"revealed": "frag_0002"},
  {"any": [{"relationship": "Lark Ferris", "tier_gte": "warm"}, {"flag": "lark_aboard"}]},
  {"not": {"tier_reached": ["trace", "loud"]}}
]}
```
 
**Leaves**
- **Stats:** `stat` + `gte`/`lte`/`between`; `tier` (current tier label); `tier_reached` (ever).
- **Story state:** `revealed` (fragment id); `flag`; `relationship` + `tier_gte`/`tier_lte`/`peak_gte`/`between`; `leverage_kind` / `leverage_label_matches`.
- **Setup and progress:** `creation` (step key → option id); `turn_gte`; `act_gte`; `subplot_status`.
- **Endings:** `waypoints_done` (`"all"`, a count, or a list of ids, scoped to the ending being evaluated).
**Declared flags.** Today's flags are free-form names the state-update pass invents, so a condition can't rely on one appearing with the right name. Any flag a condition references must be declared once, at `mechanics.flags.declared: [{"id": "lark_departed", "detect": "Lark has left the story for good"}]`. The loader passes declared flags' `detect` texts to the state-update pass (the same way CR-05 passes waypoints), and a condition naming an undeclared flag is a load-time error.
 
**Combinators:** `all`, `any`, `not`. Nesting depth is capped at 3, since Crack's builder stops at groups of rules.
 
**Partial evaluation:** the evaluator also returns a **proximity** in [0, 1] (the fraction of leaves true, with stat leaves scored by distance to threshold). CR-05 uses it to rank destinations; the authoring tool's simulator uses it for reports.
 
**Compatibility:** the existing gate `requires` (`{"stat": {"axis", "at_least"}}`, `{"item_tag": ...}`) becomes sugar the loader rewrites into this grammar. No template edits are needed.
 
**Acceptance:** evaluator is pure and code-only (no LLM), unit-tested per leaf, and the two existing gates pass unchanged.
 
---
 
### CR-03 — Visibility enforcement (P0)
 
Both templates rely on "this key is inert, and must stay inert". CR-05 introduces new audiences (the commit judge and the act generator), so this needs to be structural before anything else is added.
 
| Visibility | Reaches | Examples |
|---|---|---|
| `narrator` | Narration prompt, **and anything whose output reaches the narrator** | `world.rules`, current tier narration, active lore, ending `hint`, waypoint `plant`, ending `arc` once committed |
| `judge` | State-update / act-check / commit-judge prompts only, never echoed into narrator-facing output | Ending `criteria`, waypoint `detect`, revelation `trigger`s |
| `author` | Nothing | `canon`, `background`, every `_`-prefixed key, `harvested_v1_record`, ending `epilogue` (UI only) |
 
**The act-generator rule:** the act generator is a judge-tier call, but the act descriptions it writes are sent to the narrator every turn. Anything fed to it is therefore effectively `narrator` visibility. That is why waypoint `plant` text is classed `narrator` and must be written as a setup, never as the truth behind it.
 
**Engine behaviour**
- The template loader builds an explicit per-section allowlist of narrator and judge fields. The allowlist is generated from `x-visibility` annotations in the template JSON Schema (see `AUTHORING_TOOL_SPEC.md` §3), so the editor, the loader and the leak test share one source of truth.
- Everything else is copied into `state.authoring`, which no prompt builder can import.
- This replaces "the engine happens to read three keys" with "the engine can only see three keys".
**Leak test**
- Walk every `author`-visibility string in every story template, taking substrings of 40+ characters as sentinels. Add `judge` strings as sentinels for narrator and act-generator prompts.
- Assemble narrator, act-generator and judge prompts across a matrix of sample states (every tier of every axis, every ending viable/committed, lore saturated).
- Assert that no sentinel appears where it shouldn't. This extends the existing `full_transcript` sentinel pattern in `test_full_transcript.py`.
**Acceptance:** the test fails if someone pastes `canon.truth` into `plot_notes`, or an ending's `criteria` into a waypoint `plant`.
 
---
 
### CR-05 — Ending funnel (`ending_funnel`) (P0)
 
**Problem (F7 + drift):** both stories have a designed endgame, nothing steers toward it, and nothing can reach it.
 
**Decisions**
- Endings are authored; the engine selects among them. It never invents one for a template that authors them.
- **Commitment is silent.** The player is never shown ending names, candidates or hints in the UI. They experience the finale beginning.
- Endings are **attractors, not just exits**: their setup requirements (waypoints) feed act generation from early on.
**Crack precedent:** a minimum turn count, periodic checks, AND/OR stat rules, and a model check that the ending fits the moment. The funnel keeps all of that for the final commit. The waypoints and the narrowing are Palimpsest's own addition; Crack doesn't need them because its stories are shorter.
 
#### Two kinds of ending
 
| Kind | Purpose | Steers acts? | Can be pruned? | When it can fire |
|------|---------|--------------|----------------|------------------|
| `destination` | The story's designed conclusions | Yes, via waypoints | Yes, when `viable_while` goes false | From `budget.open_until` onward |
| `terminal` | Failure or interruption (death, catastrophic attention) | Never | No | Any turn after its `min_turn` |
 
**Load-time invariant:** at least one destination has no `viable_while` (a **catch-all**), so the funnel can never empty. A template without one fails to load.
 
#### Schema
 
```json
"endings": {
  "engine": "ending_funnel",
  "check_every": 6,
  "budget": {"open_until": 40, "narrow_until": 90, "commit_by": 140},
  "steer_top": 2,
  "finale_turns": {"min": 3, "max": 8},
  "entries": [
    {
      "id": "the_handover",
      "kind": "destination",
      "name": "The Handover",
      "viable_while": {"not": {"flag": "lark_departed"}},
      "ready_when": {"all": [
        {"relationship": "Lark Ferris", "peak_gte": 55},
        {"stat": "quorum", "gte": 65},
        {"waypoints_done": "all"}
      ]},
      "waypoints": [
        {"id": "lark_aboard",       "plant": "Lark comes aboard the Ninth-Hand, for work or otherwise",
         "done_when": {"flag": "lark_aboard"}},
        {"id": "descant_unsettled", "plant": "the System reacts to Lark in a way it will not account for",
         "detect": "Descant withholds, watches, or turns colder specifically in Lark's presence"},
        {"id": "doors_answer",      "plant": "a Consonance door or system answers to Lark faster than to the operator",
         "detect": "a Consonance mechanism responds to Lark unprompted"}
      ],
      "hint": "the ship is quieter with Lark aboard, and not in a way that feels like rest",
      "criteria": "JUDGE-ONLY. The operator understands what Lark is and what seating them would cost...",
      "arc": {"title": "The Handover", "description": "NARRATOR-FACING once committed. ..."},
      "epilogue": "UI-only text shown after THE END."
    },
    {
      "id": "dead_weight",
      "kind": "terminal",
      "min_turn": 10,
      "ready_when": {"stat": "frame", "lte": 0},
      "criteria": "JUDGE-ONLY. The scene just narrated was lethal, not merely damaging.",
      "arc": {"title": "Dead Weight", "description": "..."},
      "epilogue": "..."
    }
  ]
}
```
 
**Waypoint fields**
- `plant` (`narrator`): what the act generator should set up. Written as an event on the page, never as its meaning.
- `done_when` (condition) **or** `detect` (`judge` text): how completion is recognised. Prefer `done_when` wherever a flag or stat can carry it.
- A waypoint shared by two destinations is written once under each; the engine deduplicates by `plant` text when steering.
#### Phases
 
1. **Open** (`turn < open_until`)
   - Every viable destination is live.
   - Each act generation receives up to two unplanted waypoints, drawn round-robin across viable destinations, so the early story sets up several futures at once.
   - Terminals are armed after their `min_turn`.
2. **Narrow** (`open_until ≤ turn < narrow_until`)
   - Every `check_every` turns, code scores each viable destination: `0.6 × waypoint completion + 0.4 × ready_when proximity` (CR-02).
   - Only the top `steer_top` destinations feed act generation, pacing nudges and hints. The rest stay viable and **can still be committed if they become ready**: steering follows the player, and the player's choices can overtake it.
3. **Commit window** (from `open_until`)
   - At each check, if one or more destinations are `ready_when`, one judge call chooses among the ready set. It may return `null` ("not now: mid-climax"); after two consecutive nulls the highest-scoring ready destination is committed without asking again.
   - From `narrow_until`, pacing nudges become **drive** nudges: they name the leader's missing waypoints as the scene's priority.
   - At `commit_by`, a **forced commit** takes the highest-scoring viable destination even if it isn't ready. The judge returns the arc plus a bridging note that compresses the leader's missing waypoints into the finale.
4. **Finale:** the existing endgame machinery runs with the committed `arc`. `finale_turns.max` caps it; after the cap, a directive requires `THE END` on the next scene. The UI shows `epilogue` after conclusion.
#### Pruning
 
- `viable_while` is evaluated in code at every check. A destination whose condition goes false is **pruned permanently** and logged with its turn.
- Pruned waypoints drop out of steering immediately. Any waypoint already planted stays in the story as texture; nothing is retconned.
#### Terminal endings
 
- Their `ready_when` is checked in code **every turn** (it's cheap), because a death shouldn't wait for the next check.
- When it trips, one judge call confirms against `criteria`. On "no", the terminal enters a cooldown of `check_every` turns so it doesn't re-ask on every scrape.
#### Acts become bounded
 
- `plot.main_thread.max_acts` (default 6, not counting the finale). Once reached, no new act is generated and play continues in the last act until commitment.
- `check_and_advance_act` receives the steering waypoints under a `PLANT (choose 1–2):` heading. It never receives ending names, arcs, criteria or hints.
#### Other engine touch points
 
- **State-update pass:** gains `waypoints_hit: [ids]`, with the `detect` texts of up to six pending waypoints from steered destinations. This adds no call; the prompt is bounded.
- **Hints (silent-compatible):** a steered destination's `hint` may join a pacing nudge at most once per nudge cycle, as a diegetic fragment. It is never shown in the UI.
- **Player "end story":** triggers an immediate commit, choosing among ready destinations first, then the highest-scoring viable one with a bridging note. The generated-arc path remains only for templates with no `endings` block.
**State**
```json
"endings_state": {
  "pruned": {"the_search_ends": 57},
  "waypoints_done": {"lark_aboard": 33},
  "scores": {"tacet": 0.41, "the_handover": 0.62, "still_flying": 0.30},
  "steered": ["the_handover", "tacet"],
  "judge_nulls": 0,
  "terminal_cooldown": {},
  "committed": null
}
```
`committed` becomes `{"id", "turn", "forced": bool}`. Regenerate is already covered by the pre-turn snapshot.
 
#### Proposed ending set: The Missing Core
 
| id | Kind | Shape | `viable_while` | `ready_when` (sketch) | Waypoints (sketch) |
|----|------|-------|----------------|------------------------|--------------------|
| `tacet` | destination | Operator stays the core, knowingly | not `flag: operator_refused_seat` | SYNC ≥ 85, QUORUM ≥ 65, frag_0002 | lost hours noticed; a skill that isn't theirs; Descant refuses a question about the hours |
| `the_handover` | destination | Lark seated; operator released | not `flag: lark_departed` | Lark peak ≥ 55, QUORUM ≥ 65, all waypoints | see schema above |
| `the_search_ends` | destination | The core is found and it is not an object; both seats refused | not `flag: lark_departed` | REACH ≥ 60, frag_0006, Lark ≥ warm | a buyer at Tally who pays too well; a lane that isn't on present charts; a record naming a bloodline |
| `still_flying` | destination (**catch-all**) | An arrangement between operator and Descant; the question is left open | — | QUORUM ≥ 40, `turn_gte` 100 | licence renewed or forged; Descant named aloud once |
| `loud_enough` | terminal | The Auditor arrives as subtraction | — | TRACE ≥ 90 for 2+ consecutive checks | — |
| `dead_weight` | terminal | FRAME fails in the belt | — | FRAME ≤ 0 (min_turn 10) | — |
 
- `criteria` for `tacet` and `the_handover` must stay judge-only. Anywhere the narrator can see it, it is the twist on turn one.
- Every waypoint `plant` above is phrased as an event, not its meaning. "A buyer at Tally who pays too well" rather than "evidence of the bloodline".
**Acceptance**
- With no destination ready and `turn < commit_by`, zero commit-judge calls are made.
- A pruned destination's waypoints never appear in an act-generation prompt again.
- A template without a catch-all destination fails to load with a clear error.
- The forced commit at `commit_by` happens exactly once.
- Act-generation and narrator prompts never contain ending names, `arc` (before commit), `criteria` or `detect` text (enforced by CR-03).
- The `example` story, which has no `endings` block, behaves exactly as today, including "end story".
---
 
### CR-04 — Creation-derived variables (P1)
 
**Problem (F4):** Lark's gender is a pure function of two creation choices, but the narrator is asked to compute it every turn from a long conditional rule.
 
**Schema:** a top-level `derived` list, evaluated once when the last creation step completes; first match wins.
 
```json
"derived": [
  {"when": {"creation": {"gender": "man"}},      "set": {"lark_pron": "she/her", "lark_is": "a woman"}},
  {"when": {"creation": {"gender": "woman"}},    "set": {"lark_pron": "he/him",  "lark_is": "a man"}},
  {"when": {"creation": {"trade": "coil_hand"}}, "set": {"lark_pron": "he/him",  "lark_is": "a man"}},
  {"when": {"all": []},                          "set": {"lark_pron": "she/her", "lark_is": "a woman"}}
]
```
 
Any narrator-visible string may use `{var}`, e.g. in a lore entry (CR-06): *"Lark Ferris is {lark_is} ({lark_pron}), fixed for the whole story."* Unresolved `{var}` is a load-time error, not a runtime blank.
 
**Crack parallel:** Crack scopes stats and endings per start setting. `derived` gives the same power without duplicating the template per start.
 
**Acceptance:** the 3×3 gender × trade matrix produces the specified pronouns; the old rule is deleted from `world.rules`.
 
---
 
### CR-06 — Triggered lore (`keyed_lore`) (P1)
 
**Crack precedent:** a keyword book of up to 20 notes; at most 3 active at once, with higher-ranked notes winning.
 
**Problem:** `the_missing_core` has 20+ `world.rules`, several of them long, and many relevant only when a specific character or place is on the page (Lark's gender and reputation, the Board's competence, the comedy exceptions around The Auditor).
 
**Schema**
```json
"lore": {
  "engine": "keyed_lore",
  "max_active": 3,
  "entries": [
    {
      "id": "lark_fixed",
      "priority": 90,
      "keys": ["Lark", "Ferris", "the diver"],
      "also_when": {"any": [{"subplot_status": {"subplot_005": "active"}}, {"relationship": "Lark Ferris", "between": [-100, 100]}]},
      "sticky_turns": 3,
      "content": "Lark Ferris is {lark_is} ({lark_pron}), fixed for the whole story. Nine years diving Consonance wrecks alone and never hurt; everyone calls it nerve or luck, Lark included. Never explain it."
    }
  ]
}
```
 
**Engine behaviour**
- **Triggers:** case-insensitive key match on the player's action and the last narration, plus `also_when` conditions.
  - `also_when` is essential. Key matching alone misses the turn where the narrator introduces a character unprompted, which is exactly when the fixed facts matter most.
- **Stickiness:** `sticky_turns` keeps an entry active for N turns after its last trigger, so it doesn't flicker mid-scene.
- **Selection:** rank by priority and inject the top `max_active` under a `LORE:` heading.
- **Unlocks:** an optional `unlock` condition keeps an entry dormant until met. This is the path for *staged* narrator knowledge, e.g. Descant's unease about Lark once QUORUM ≥ 65, without exposing canon.
**Migration triage for `the_missing_core` world.rules**
 
| Rule | Destination |
|------|-------------|
| Consonance tech can't be built | stays in rules |
| THE SYSTEM BECOMES A PERSON | stays (short form) + QUORUM tiers |
| SYNC / REACH / FRAME / TRACE / QUORUM band prose | CR-01 tiers |
| Every gain paid on the page | stays |
| Descant withholds, never lies | stays |
| Crew near neutral is leaving | CR-08 |
| Salvager, not soldier | stays (and terminal `dead_weight`) |
| Lark gender, Lark reputation | lore `lark_fixed` (+ CR-04) |
| Nobody knows why the Consonance fell | stays |
| Old scale is background | stays |
| Present-day powers are small | lore, keyed on faction names |
| Comedy exceptions | stays |
| Readout clause is Descant speaking | stays |
| Name not available below QUORUM 40 | QUORUM tier narration (already partly there) |
| System issues the work | stays (short form) + CR-07 |
 
**Cost:** bounded by `max_active` × entry length. The expected net effect is negative once rules shrink.
 
**Acceptance**
- A Lark-introduction turn with no "Lark" in the player action still injects `lark_fixed` (via `also_when`).
- A fourth triggered entry is dropped by priority.
- An `unlock`ed entry never injects early.
---
 
### CR-07 — Thread completion rewards (P2)
 
**Problem (F6):** "a directive completed is paid in restoration, in the same scene". Completion is only known after narration, so the engine can't enforce it.
 
**Schema**
```json
"subplots": {
  "engine": "weighted_threads",
  "completion_rewards": {"high": ["section.reclaimed"], "medium": ["node.relit"], "low": []},
  "near_completion_margin": 15
}
```
Authored subplots may override with `"on_complete": {"stat_events": ["lattice.rejoined"]}`. Generated subplots inherit from `completion_rewards` by priority.
 
**Engine behaviour: pre-arm, then settle**
- When a thread's progress is within `near_completion_margin` of its threshold, the narrator gets a one-line pre-arm: *"'<title>' may resolve this scene; if it does, something of the vessel comes back on the page."*
- On completion, the engine applies the reward's stat events itself; the extraction pass doesn't have to remember.
- If the scene that crossed the threshold didn't narrate the payoff (the judge reports this in the existing state-update JSON as `reward_narrated: false`), the next turn gets a one-shot directive to pay it.
**Acceptance:** completion always produces the reward delta exactly once, including on regenerate (the pre-turn snapshot already covers this).
 
---
 
### CR-08 — Relationship tier transitions (P2)
 
**Problem (F5):** the "exit that costs something" rule needs history the narrator doesn't have.
 
**Schema:** extend `scored_axis` tiers with transition hooks and track per-character `peak`.
```json
"transitions": [
  {"id": "drifting_out", "when": {"relationship_self": {"peak_gte": 25, "between": [-10, 10]}},
   "once_per_character": true,
   "directive": "{name} is leaving this story: give them an exit that costs something, then let them be gone",
   "sets_flag": "{id}_departed"}
]
```
**Engine behaviour:** evaluated after relationship deltas. When it fires, it injects a one-turn directive naming the character and marks the character `exiting`. After the exit scene, the state-update pass may set `departed`, which removes them from the relationship roster prompt and sets the flag (e.g. `lark_departed`), which CR-05 uses to prune destinations.
 
**Acceptance:** fires once per character; never fires for a character whose peak stayed below 25; a departure prunes any destination whose `viable_while` depends on that character.
 
---
 
### CR-09 — Narration exemplars (P2)
 
**Crack precedent:** up to 3 example exchanges per story, which the guide treats as the main tool for steering voice and as a guideline for players.
 
**Problem:** Descant's voice drifts, and the templates document the drift (the readout rendered on 9 of 14 turns). A word cap enforces terseness but can't show register.
 
**Schema:** `narration.exemplars`, a list of `{id, when, text}`. At most 2 are injected, and only those whose `when` holds. Each text is 60–120 words, written in-house.
 
For The Missing Core, write one exemplar per QUORUM tier, showing a readout clause *inside* a scene, not before OPTIONS.
 
**Measure before adopting:** A/B on readout placement and on first-person leakage below QUORUM 40, using the existing classifier scripts. Drop the change if the delta is inside noise, since this is the only CR that adds prompt tokens on every turn.
 
---
 
### CR-10 — Threads as carriers (P0)
 
**Problem:** with CR-05, waypoints steer the act generator toward endings, while `weighted_threads` subplots steer pacing nudges toward whatever has the highest priority. Two steering systems compete for the same scene. `generate_new_subplot` also invents a replacement from the summary whenever a thread completes, which is the same copy-of-a-copy drift CR-05 removes from acts.
 
**Principle:** endings say *what* must happen (waypoints); threads say *where, with whom, and while doing what*. Waypoints are planted through threads the player is already following.
 
**Schema** (additions to `plot.subplots.<id>`)
```json
"subplot_005": {
  "title": "The Diver",
  "role": "personal",
  "activate_when": {"subplot_status": {"subplot_003": "progressed"}},
  "delivers": ["the_handover.lark_aboard", "the_handover.descant_unsettled", "the_handover.doors_answer"],
  "fail_when": {"flag": "lark_departed"}
}
```
 
| Role | Purpose | Generated? |
|---|---|---|
| `spine` | Carries waypoints toward endings | Authored. Generated only as a fallback (below), and then it must carry the waypoint it was generated for |
| `personal` | A relationship arc; may carry waypoints | Authored only |
| `texture` | Episodic breathing room: jobs, deadlines, local trouble | May be generated freely, but single-act, at most one active, carries nothing, and must close |
 
- `delivers` holds ids only and is `author`/engine visibility. It never reaches a prompt, so it can't leak what an ending means (CR-03).
- `activate_when` (CR-02 condition) replaces `starts_active: false` + manual activation. `starts_active: true` is unchanged.
- `fail_when` (condition) or a `detect` text for the state-update pass adds a `failed` status, which today doesn't exist.
**Engine behaviour**
- **Carrier selection:** when a steered destination (CR-05) has an unplanted waypoint, the engine looks for a thread that `delivers` it:
  1. An active carrier gets its priority raised for pacing nudges, and the waypoint's `plant` is attached to that thread's nudge line.
  2. Otherwise, an authored carrier that isn't active yet is **activated early**, even if its `activate_when` doesn't hold yet, but only from the Narrow phase on.
  3. Only if no authored thread carries it is a spine thread generated, with the waypoint's `plant` as its brief and `delivers` pre-filled.
- **Failure prunes:** when a thread fails, any destination whose uncompleted waypoints are carried *only* by failed threads is pruned. This is how "Lark walks out" removes `the_handover` in code.
- **Generation is limited to texture** except for the fallback above. The pool stays full, but free invention only happens where drift is harmless.
- **Act advancement:** once `max_acts` bounds acts (CR-05), advancement follows waypoint progress on steered destinations. A completed spine or personal thread counts; a completed texture thread doesn't.
**Mapping for The Missing Core** (all five existing subplots fit a role)
 
| Thread | Role | Activates | Delivers |
|---|---|---|---|
| Behind the Bulkhead | spine | start | `tacet.lost_hours`, `tacet.borrowed_skill`, `still_flying.named_once` |
| A Second Pair of Hands | spine | start | `the_handover.lark_aboard` |
| Third Class, Six Weeks | spine | start | `still_flying.licence` |
| What Tally Remembers | spine | REACH ≥ 20 | `the_search_ends.tally_buyer`, `the_search_ends.bloodline_record` |
| The Diver | personal | after A Second Pair of Hands progresses | `the_handover.*` |
 
Gaps the storyboard shows today: `tacet.refused_question` and `the_search_ends.uncharted_lane` have no carrier; `tacet` and `the_search_ends` each rest on a single thread.
 
**Acceptance**
- A steered waypoint with an active carrier never triggers subplot generation.
- A generated subplot always has `role: texture` or carries the waypoint it was generated for.
- Failing a destination's only carrier prunes it at the next check.
- The `example` story, whose subplots have no roles, keeps today's behaviour (treated as `spine` with no `delivers`, generation unrestricted).
---
 
### CR-11 — Side threads and NPC bonds (P1)
 
**Problem:** with CR-05 and CR-10 in place, nothing in the story moves unless it serves an ending. Two gaps follow:
- **Authored characters relate only to the protagonist.** Relationships between NPCs don't exist anywhere in state.
- **The model's own inventions are used once and forgotten.** Generated NPCs appear in one scene and nothing brings them back.
 
The story needs a life of its own between the planned beats, driven by numbers the engine already tracks.
 
**Principle:** side threads share the world's numbers, never the story's structure. The engine decides **when** and **who** from state (P-7); the model writes **what happens**.
 
**Decisions**
- **A separate track, not a subplot.**
  - Side threads are not in `plot.subplots` and have no `delivers`, `activate_when` or `ties_to_main_plot`.
  - They never count toward act advancement and never appear in the act-check prompt.
  - They have their own pool (`max_active`), so a side thread never takes a slot from an authored thread.
- **Its own lifetime.** A side thread runs until its own end condition. Acts advance and threads start and finish around it.
- **Bonds are one-way.** A→B and B→A are separate scores, so unrequited feeling is representable.
- **Amends CR-10.**
  - Generated texture moves here entirely. `generate_new_subplot` keeps only CR-10's spine fallback.
  - The `texture` role stays for *authored* episodic subplots.
- **Amends CR-02.** Adds one leaf, `bond`.
**Two engines.** Each contributes at most one observation field, so the pair is one field over what a single engine could claim.
 
#### Bonds (`mechanics.bonds`, engine `scored_bonds`)
 
```json
"bonds": {
  "engine": "scored_bonds",
  "axis": {"negative": "hostile", "positive": "devoted"},
  "registers": {"covered_for_them": 8, "confided_in_them": 7, "worked_alongside": 3,
                "undercut_them": -7, "betrayed_them": -15},
  "tiers": [{"at": 55, "label": "devoted"}, {"at": 25, "label": "warm"},
            {"at": -25, "label": "wary"}, {"at": -55, "label": "hostile"}],
  "seed": [
    {"from": "Mira Venn", "to": "Salome Vence (the Advocate)", "score": -20},
    {"from": "Salome Vence (the Advocate)", "to": "Mira Venn", "score": -10}
  ],
  "max_generated_bonds": 12,
  "cap_per_window": {"delta": 12, "turns": 3}
}
```
 
- **Opening a bond.**
  - Bonds between authored characters open at 0 on their first event, or at their `seed` value. They are **never evicted**.
  - A bond involving a generated NPC opens on its first event. These count against `max_generated_bonds` and are evicted closest to neutral, the same rule as `scored_axis`.
  - When a generated character is evicted, their bonds go with them.
- **Observation field:** `bond_events: [{"from", "to", "event", "mutual"?}]`.
  - `event` must be a register; an unknown one is dropped.
  - `mutual: true` applies the event in both directions. This is one field with a richer type, not two fields.
- **Prompt (narrator):** a `BETWEEN THEM` block, rendered only for pairs where **both** characters are in the current scene.
  - Tier labels only, never numbers, and at most 4 lines. Each direction is shown separately when the two directions differ in tier: `Salome → Mira: warm · Mira → Salome: wary`.
- **Tiers carry `at` and `label` only.** No `narration` lines; see *Decided* below.
- **CR-02 leaf:** `{"bond": ["<from>", "<to>"], "gte"|"lte"|"between"|"tier_gte"|"tier_lte": …}`, directional.
  - A bond that hasn't opened yet reads as **0**, not as unknown, because pairs open lazily.
  - An unknown character name is lint error L10.
#### Side threads (`mechanics.side_threads`, engine `episodic_threads`)
 
```json
"side_threads": {
  "engine": "episodic_threads",
  "max_active": 2,
  "cooldown_turns": 8,
  "start_after_beats": ["respite"],
  "max_turns": 30,
  "protected": ["Lark Ferris"],
  "default_recipe": true,
  "recipes": [
    {
      "id": "unrequited",
      "cast": {"a": {"from": "any"}, "b": {"from": "any"}},
      "eligible_when": {"all": [
        {"bond": ["a", "b"], "tier_gte": "warm"},
        {"bond": ["b", "a"], "lte": 0}
      ]},
      "premise": "one of them has started to care, and the other hasn't noticed",
      "may_move": ["bond:a,b", "bond:b,a", "relationship:a", "relationship:b"],
      "may_create_npc": false
    }
  ]
}
```
 
**Cast slots.** `from` is one of:
- `any`
- `authored`
- `generated`
- a character name
A slot never binds a `protected` character. A recipe that names one explicitly is a load-time error.
 
**Lifecycle**
1. **Start.** Considered after any turn whose classified beat is in `start_after_beats` (default `respite`). All of these must hold:
   - fewer than `max_active` threads are live;
   - `cooldown_turns` have passed since the last start;
   - no ending is committed (CR-05).

   The engine then enumerates every (recipe, cast) binding whose `eligible_when` holds, and picks one deterministically:
   - first, casts not used by the last three concluded threads;
   - then, the largest total |bond| among the bonds the condition names;
   - then, recipe order.

   If nothing is eligible and `default_recipe` is on, the default recipe applies. It casts the directed pair with the largest |score| among characters seen in the last five turns.
2. **Generate.** One call, **Tier C unless the module records why not**.
   - **The generator receives:**
     - the recipe `premise`;
     - the cast's narrator-visible fields (`description`, `first_contact`);
     - the tier labels of the bonds between the cast;
     - the titles of the last three concluded side threads;
     - the existing-character list.
   - **It never receives** canon, judge text, ending names or waypoints (CR-03).
   - **It returns:**
     - `title`;
     - `premise` (≤ 40 words, narrator);
     - 2–4 short `beats`;
     - `resolves_when` and optional `fails_when`, conditions that may reference **only** `may_move` values (the engine nulls a condition that references anything else);
     - `detect` (judge text);
     - an optional `new_character`, only when `may_create_npc` is set.
   - **New NPCs** go through `insert_character` with `origin: "side_thread"`.
   - **Status labels.** The call's `_timed` label is added to `STATUS_LABELS` and `DEFAULT_STEP_ESTIMATE_SECONDS`.
3. **Run.** An active thread is offered to the narrator as one optional line (`SIDE THREAD, if it fits this scene: <title>: <next beat>`), under these rules:
   - it is offered only on turns where no pacing rule is armed and no CR-05 drive nudge is active, so threat scenes belong to the main story;
   - with several active threads, the offer rotates, one line per turn at most.
   - **Observation field:** `side_thread_progress: [{"id", "advanced", "detect_hit"}]`, for the thread offered this turn.
4. **End.**
   - `resolves_when` and `fails_when` are evaluated in code every turn, with **closed** polarity (Authoring Tool Spec decision D2). A `detect_hit` also resolves the thread.
   - At `max_turns`, the engine adds a closing line to the next prompt **whether or not a rule is armed**, and concludes the thread after that turn. Every thread ends; the engine keeps that promise, not the model.
**Endings interplay**
- **No new side threads once an ending commits.** Active threads get a wrap-up line on their next offered turn.
- **At the finale's first turn,** every active thread is concluded silently, with outcome `finale`.
**Crossing, stated honestly**
- **Structural isolation.** A side thread has no structural output: no waypoints, no flags of its own, no subplot status.
- **Normal observation still applies.** A side-thread scene goes through the normal observation pass like any other scene. If the scene genuinely moves a relationship or sets a declared flag, that change is real.
- **The protection is upstream, not after the fact:**
  - the generator is never told about endings or flags;
  - `protected` characters are never cast;
  - the premise and end conditions are confined to `may_move`.
- **Generated NPCs reach endings only indirectly.** Ending conditions can name only authored characters (L10). A side thread can affect an ending only through values an ending already reads. For example, a scene moves the protagonist's relationship with Mira through a priced register, and a `ready_when` reading Mira sees the new score.
**Pinning.** A character cast in an active side thread is exempt from `scored_axis` and bond eviction until the thread concludes.
 
**Generated characters start unscored.** `insert_character` creates a relationship entry at `null`, not 0, so `first_contact` (Authoring Tool Spec decision D7) shows until the first scored interaction.
 
**State**
```json
"bonds": {"Salome Vence (the Advocate)": {"Mira Venn": {"score": 28, "peak": 28, "opened_turn": 14}}},
"side_threads": {
  "active": [{"id": "st_003", "recipe": "unrequited", "cast": {"a": "Salome Vence (the Advocate)", "b": "Mira Venn"},
              "title": "The Advocate's Late Hours", "premise": "...", "beats": ["..."], "beat_index": 1,
              "resolves_when": {"bond": ["Mira Venn", "Salome Vence (the Advocate)"], "tier_gte": "warm"},
              "fails_when": {"bond": ["Salome Vence (the Advocate)", "Mira Venn"], "lte": 0},
              "detect": "...", "started_turn": 31, "max_turns": 30, "last_offered_turn": 33}],
  "concluded": [{"id": "st_002", "title": "...", "cast": {}, "turns": [12, 24], "outcome": "resolved"}],
  "last_started_turn": 31
}
```
`concluded` grows on disk. Only its last three titles and casts reach a prompt: the generator's, for picking and to avoid repeats.
 
**Worked example: New Babel**
 
1. **Seed.** Mira → Salome is −20 and Salome → Mira is −10. Each watches the protagonist, and each distrusts the other's motives.
2. **Turns 14–30.** The protagonist keeps putting them in rooms together. Two `worked_alongside` and two `covered_for_them` events move Salome → Mira to +28 ("warm"). Meanwhile Mira → Salome, moved by other events, sits at −5.
3. **Turn 31 (respite).** `unrequited` is eligible, and the engine casts Salome as `a` and Mira as `b`. The model writes *"The Advocate's Late Hours: Salome keeps finding reasons to review Mira's audit slate after midnight."*
4. **Turns 32–60.** Two acts advance and a subplot completes around it. The side thread is unaffected: it resolves if Mira → Salome reaches "warm", fails if Salome → Mira drops back to 0, and ends by turn 61 regardless.
5. **Cross-effect.** Any ending that reads the protagonist's relationship with Mira sees whatever these scenes did to it. No link was authored for that.
#### Beyond characters (r5, 2026-09-23)
 
Three amendments. All of them reuse the recipe, condition and lifecycle machinery above, and none adds an engine or an observation field.
 
**A. Wider casts and `may_move`.** A cast slot has a `kind`, defaulting to `character`:
 
| `kind` | Binds | Example |
|---|---|---|
| `character` | As above | `{"from": "generated"}` |
| `location` | A `world.locations` id | `{"kind": "location", "id": "loc_tally"}` |
| `item` | A held inventory record carrying a tag | `{"kind": "item", "tag": "salvage"}` |
 
`may_move` gains three prefixes. Each moves only through the owning engine's existing observation field and vocabulary:
- **`stat:<axis>`.** Via `stat_events` in a priced story, or `stat_changes` in a delta-map story, whichever the story authored (all-or-nothing, per the stats invariant). It never introduces an axis.
- **`item:<tag>`.** Gain or expend items with that tag through `tagged_items`' `inventory` field. Capacity refusal applies as usual.
- **`leverage:<kind>`.** Through `spendable_ledger`'s `leverage` field. `kind` must be one of the ledger's authored `kinds`.
The honesty rule is unchanged: `may_move` confines the premise and the end conditions, and the observation pass is not filtered.
 
*Example (The Missing Core):*
```json
{"id": "papers_please",
 "cast": {"a": {"from": "generated"}, "place": {"kind": "location", "id": "loc_tally"}},
 "eligible_when": {"stat": "trace", "tier": "noticed"},
 "premise": "someone at Tally has started asking about the Ninth-Hand's paperwork",
 "may_move": ["stat:trace", "item:document", "leverage:access"],
 "may_create_npc": true}
```
The `noticed` tier here assumes CR-01's TRACE tiers.
 
**B. Vignettes.** A vignette is a single scene of texture, with no thread, no end condition and **no generation call**:
```json
"side_threads": {
  "vignettes": {
    "every": 4,
    "seeds": ["the hold at shift change", "Descant logging hull noise it will not explain"],
    "subjects": ["location", "character", "item"]
  }
}
```
- **When.** On a turn where all of the following hold:
  - no pacing rule is armed;
  - no CR-05 drive nudge is active;
  - no side thread is being offered;
  - at least `every` turns have passed since the last vignette.
- **What.** The narrator gets one optional line: `TEXTURE, if the scene has room: <subject>`.
  - The subject is picked deterministically: authored `seeds` in rotation, alternating with the least-recently-featured authored location, known character or held item.
  - Auto-picked subjects never include a `protected` character. An authored seed may name one, because a single scene gives no arc.
- **Priority.** At most one texture line per turn; a side-thread offer wins over a vignette.
- **Cost.** State is `last_vignette_turn` plus the last 8 subjects, so vignettes add no observation field.
- **Visibility.** `seeds` are narrator-visible and go through the CR-03 leak check.
**C. Callbacks.** A recipe may follow a concluded side thread:
```json
{"id": "the_favour",
 "follows": {"recipe": "any", "outcome": ["resolved"], "min_turns_since": 20},
 "cast": {"a": {"from": "followed", "slot": "a"}},
 "premise": "someone from an old episode needs the favour repaid",
 "may_move": ["relationship:a", "leverage:bond"]}
```
- **Binding.** `from: "followed"` binds the same character the followed thread had in that slot.
- **Eligibility.** The recipe is ineligible if that character has since been evicted: the world forgot them, and that is acceptable.
- **At most once.** Each concluded thread can be followed once; `followed_by` is recorded on it.
- **Generator input.** The generator also receives the followed thread's `title`, `premise` and `outcome`, all narrator-visible already.
- **Not a CR-02 leaf, on purpose.** A condition leaf would let an ending's `ready_when` read side-thread outcomes, which breaks the rule that side threads never reach structure. `follows` is local to recipes.
**Acceptance (r5)**
- A recipe's `may_move` naming an axis, tag or ledger kind the story lacks is a load-time error (L10).
- Vignettes: never more than one texture line in a turn; none while a rule is armed; with `vignettes` absent, prompts are byte-identical.
- Callbacks: a concluded thread is followed at most once; an evicted character never binds `from: "followed"`; no ending condition can reference a side thread.
**Authoring tool**
- **Cast tab:** a directed bond grid (rows are `from`, columns are `to`) with seed values, and a `protected` toggle on each card.
- **Side threads panel:** recipe cards. The sample-state bar shows which (recipe, cast) bindings would be eligible.
- **Lint:** L10 covers bond and cast references. A new warning covers a recipe that can never bind, e.g. `from: generated` in a story with no generation.
- **Simulator (S6):** start frequency, typical duration and outcome mix.
**Acceptance**
- **Track isolation:**
  - a side thread never counts toward act advancement and never appears in the act-check prompt;
  - advancing an act or completing a subplot never changes an active side thread's status.
- **Closure:**
  - every side thread concludes by `started_turn + max_turns + 1`;
  - none starts after an ending commits, and none survives the finale's first turn.
- **Pinning:** a character cast in an active side thread is never evicted.
- **Direction:** an event on A→B leaves B→A unchanged unless it is `mutual`.
- **Leaks:** the CR-03 leak test covers the generator prompt; no canon or judge sentinel reaches it.
- **Absent modules:** with neither `bonds` nor `side_threads` declared, every prompt is byte-identical to before (P-2).
- **Budget:** each engine adds exactly one observation field, measured against `core + 7` by `scripts/measure_baseline.py`.
- **CR-10 amendment:** `generate_new_subplot` never produces a `texture` thread.
**Decided (2026-09-23)**
1. **The default recipe is built in and on by default.**
   - Omitting `default_recipe` means `true`, so a story that declares `side_threads` with no recipes still gets side threads.
   - A story opts out with `"default_recipe": false`.
2. **Bond tiers carry labels only.**
   - A bond tier accepts `at` and `label`. A `narration` key is a schema error (L01), not ignored, until a measurement shows labels aren't enough.
   - That measurement uses the existing classifier scripts, the same test CR-09 applies before adding per-turn tokens.
---
 
### CR-12 — Player-started side threads (P2)
 
*Not the `CR-12` in `story_engine.py`'s `emerging_themes` comment. That comes from the older `SCHEMA_COVERAGE_CRD` numbering, which also reuses CR-04 and CR-06.*
 
**Problem:** CR-11 side threads start only when the engine decides. When the player chooses something off the rails, such as running errands for a Tally bar owner or fixing up a hulk for its own sake, nothing gives the pursuit shape or an end. The save's `player_driven_goals` only half covers it:
- entries are added only by hand (`plot_manager add-goal`);
- they render as a bare `PLAYER GOAL:` nudge line;
- they have no lifecycle, so they never end unless someone deactivates them.
**Principle:** the player chooses **what**; the engine confirms it is real and gives it the same guarantees as any side thread.
 
**Schema** (inside `mechanics.side_threads`)
```json
"player_threads": {
  "max_active": 1,
  "confirm": {"reports": 2, "within_turns": 5},
  "abandon_after_offers": 6,
  "may_move": ["relationship:cast", "item:salvage", "leverage:bond"]
}
```
Absent `player_threads` means the feature doesn't exist (P-2). `relationship:cast` means the relationships of whoever the pursuit ends up casting.
 
**Detection: no new observation field.** `episodic_threads`' single field gains an optional entry: `side_thread_progress: {"offered": [...], "pursuit": null | {"summary", "continues"}}`. That is one field with a richer type, as the invariant prefers.
- **What the observation context shows:**
  - the current candidate, if any;
  - the titles of active authored threads and active side threads.
- **What the model is asked:** report a pursuit only if the **protagonist chose it** and it matches none of the listed threads. `continues: true` means "the same pursuit as the candidate".
- **Confirmation in code.**
  - A pursuit opens a thread once it has been reported `confirm.reports` times within `confirm.within_turns`.
  - A new `summary` replaces the candidate and restarts the count.
  - This deterministic count is what stops one curious scene from becoming a thread.
**Opening**
- **Generation.** The same generation call as CR-11, with a synthetic recipe `player_pursuit`:
  - premise: the confirmed `summary`;
  - cast: characters present in the reporting scenes;
  - `may_move`: from `player_threads`.
- **Stored as** a side thread with `origin: "player"`, counted against `player_threads.max_active`. The engine pool never blocks it.
- **Protected characters.** If the pursuit's cast would include a `protected` character, no thread opens. That arc belongs to the authored thread, and the main story carries it.
**Running and ending.** As CR-11, with two differences:
- **Rotation priority.** A player thread takes priority in the offer rotation.
- **An extra end condition.** A thread offered `abandon_after_offers` times without advancing concludes with outcome `abandoned`: the player lost interest.
All three CR-11 guarantees still apply: `resolves_when` / `fails_when`, `detect`, and the `max_turns` backstop. The player cannot end the story (Authoring Tool Spec decision D6); a pursuit like "leave the belt for good" becomes, at most, a side thread.
 
**Replaces `player_driven_goals`.**
- `plot_manager add-goal` opens a player thread directly, skipping detection.
- The `PLAYER GOAL:` nudge line and the list are removed.
**Risk:** detection is a judgement call. The worst case is a side thread that duplicates a main thread's topic. That is bounded:
- side threads have no structural outputs;
- the duplicate ends by `max_turns`;
- the thread-title listing makes it rare.
**Acceptance**
- **Confirmation:** a single report never opens a thread; `reports` reports within the window always do.
- **Protected characters:** a pursuit whose cast includes a `protected` character never opens a thread.
- **Abandonment:** a player thread not advanced on `abandon_after_offers` offers concludes as `abandoned`.
- **Budget:** the observation field count is unchanged from CR-11.
- **Absent module:** with `player_threads` absent, the `pursuit` key does not appear in the observation schema (P-2).
**Deferred texture ideas** (not specced; revisit after side threads are measured in play)
- **Faction life.** `world.factions` has goals and a stance toward the player but no scores. Feuds and standing need a faction axis, a new engine along the lines of `scored_bonds`.
- **Off-screen world news.** Events that happen without the protagonist and surface as one line through NPCs. This needs a world clock or triggers and a per-story news pool.

---

### CR-13 — Story clock: idle turns don't spend the budget (P1)

*Not the `CR-13` in `story_engine.py`'s `ties_to_main_plot` comment, which comes from the older `SCHEMA_COVERAGE_CRD` numbering (see the note under CR-12).*

**Problem:** every exchange counts as a turn, and the ending budget (`open_until`, `narrow_until`, `commit_by`), act checks, `per_turn` drift and side-thread limits all run on that count. A player who stops to ask questions - about a character, the place, what something means - spends the story's budget without the story moving. At about 500 words a turn, a budget sized from a novel's length (see the estimate that prompted this: `commit_by` 205 ≈ 100,000 words) then ends the story early in *plot* terms for a curious player, and on time for one who never asks anything. Curiosity should be free, up to a point.

**Principle:** the player may linger; the story may not stall. What counts as "nothing happened" is decided in code from what the state-update pass already reports (P-7), never by asking a model "was this idle?". Every story must still end: the free allowance is bounded, and the bound is the engine's, not the prompt's.

**Schema** (`plot.pacing.story_clock`; absent means the feature does not exist and every turn counts, as today - P-2):

```json
"pacing": {
  "nudge_frequency": 8,
  "act_check_frequency": 12,
  "story_clock": {
    "free_idle_streak": 3,
    "push_directive": "Someone knocks at the depot door: the Company man, early, with papers."
  }
}
```

- `free_idle_streak` (required, ≥ 1): how many idle turns in a row cost nothing. A creative decision, so authored; there is no engine default.
- `push_directive` (optional, narrator-visible): the one-turn push once the streak is used up, in the story's own voice. Without it, only the options lean forward.

**Two clocks.** `pacing.turn_count` keeps counting every exchange. A new `pacing.story_clock` counts turns that moved the story, plus every idle turn past the free streak.

| Reads the **story clock** | Stays on **`turn_count`** (every exchange) |
|---|---|
| Ending budget phases and forced commit (`open_until`, `narrow_until`, `commit_by`), funnel `check_every`, terminal `min_turn` | Pacing nudge cadence (`nudge_frequency`): a player stuck asking questions is exactly who needs one |
| Act check cadence (`act_check_frequency`) | Flag staleness window, summary rollover, recent-turns window (context bounds, CLAUDE.md) |
| `turn_gte` condition leaves | Relationship and bond `cap_per_window` (rate limits on exchanges) |
| Stat `per_turn` drift (asking questions takes little in-world time) | Reveal, flag and character timestamps (the save's history) |
| Side-thread `cooldown_turns` / `max_turns` / callback `min_turns_since` (CR-11), player-thread confirmation window (CR-12) | The finale's own length (`finale_turns`): the finale always counts |

**Idle, defined in code.** A turn is idle when the state-update pass reports **none** of:
- a thread moved past `touched`;
- a flag set;
- a waypoint hit, or a `done_when` newly true;
- a fragment revealed;
- a stat event or change;
- a social or bond event;
- an item gained or used;
- a leverage entry;
- a location change;
- a new named character;
- side-thread progress.

No new observation field and no new LLM call: it is a function of the observations every turn already produces. A turn inside the finale is never idle.

**The streak.** `idle_streak` counts consecutive idle turns and resets on any non-idle turn.
1. **Free.** While `idle_streak <= free_idle_streak`, an idle turn does not advance the story clock.
2. **Lean forward.** From the turn after the streak is reached, and for as long as the streak holds, the options instruction adds: offer choices that act, commit or go somewhere, not further questions. The player can still type anything; this is guidance, not the guarantee.
3. **Push.** On the first of those turns only, `push_directive` (if authored) joins the narration prompt for one turn, like a fired pacing rule. It does not fire again until the streak has reset and been used up again.
4. **Pay.** Every further idle turn advances the story clock in full. This is the guarantee: a streak can delay the budget by at most `free_idle_streak` turns per run of idleness, and never stop it.

**Interplay.**
- **Pacing loop (beat_counter):** unchanged. It classifies every turn and reads `turn_count`. An inquiry turn usually classifies as the quiet beat and feeds its counter, which is how a rule designed to break long lulls still fires.
- **Armed rules win.** If a pacing rule fires on the same turn as the push, the rule's directive is used and the push is not (at most one pacing directive per turn). The push is then spent for this streak.
- **Ending funnel:** the Timeline bar and `budget` are read in story-clock turns. The engine's forced commit is unchanged in kind, it just arrives later for a curious player.
- **Regenerate** restores the pre-turn snapshot, which includes `story_clock` and `idle_streak`.

**State** (in `state.pacing`, created only when the story authors `story_clock`):
```json
"story_clock": 41, "idle_streak": 2, "push_fired_for_streak": false
```

**Authoring tool.**
- Ending funnel settings (Story health panel) get an **Idle turns** section: the free streak and the push directive. The Forms tab's Pacing section edits the same fields.
- The Timeline bar and budget fields say their turns are story-clock turns when the clock is authored.
- The Sample state's **Turn** sets the story clock when the story authors one.

**Acceptance**
- **Absent module:** with no `story_clock`, every prompt and every counter behaves exactly as today (P-2).
- **Idle detection:** a turn whose observations report nothing on the list is idle; any single item makes it not idle; a finale turn is never idle.
- **Free streak:** `free_idle_streak` idle turns in a row leave the story clock unchanged; the next one advances it.
- **Bound:** for any sequence of turns, `story_clock >= turn_count - free_idle_streak * (number of idle runs)`, and a run of k idle turns costs max(0, k - free_idle_streak).
- **Push:** fires once per exhausted streak, never when a pacing rule fired that turn, never in the finale.
- **Lean forward:** the options instruction carries the line exactly while the streak is exhausted.
- **Two clocks:** each consumer in the table reads the clock its column names.
- **Budget:** no new observation field, no new LLM call; the push line is covered by the narration prompt budget.

**Open question.** Should an idle turn be free for the pacing nudge as well? Leaving the nudge on `turn_count` means a curious player is nudged sooner in story-clock terms, which is the intent. Revisit if nudges feel pushy in play.


---

### CR-14 — Scene length by moment (P2)

**Problem:** every scene is asked for the same word range (`narration.scene_length`, 470–500 in all three stories). A question gets 500 words, most of them scenery nobody asked for; a crisis gets the same 500. Padding is the visible symptom: the model fills the range whether or not the moment has anything to fill it with. A token cap (`max_tokens`) does not fix this. The model does not write to fit it; it is cut off by it, which loses the options block and costs a follow-up call (see `story_engine.py`'s notes on `OPENROUTER_MAX_TOKENS` and `finish_reason: "length"`). The token cap stays a runaway guard only; length stays authored in words.

**Principle:** the length follows the moment, chosen in code from what is known *before* the scene is written. What is only known after (this turn's beat, whether it was idle) cannot choose this turn's length; the one exception, a turn that is only a question, is left to the narrator as an authored instruction, because a scene that runs long is a slightly worse scene, not a broken promise (P-7's test).

**Schema** (`narration.scene_length_by_moment`; absent means every scene uses `narration.scene_length`, as today - P-2):

```json
"narration": {
  "scene_length": {"min": 470, "max": 500},
  "scene_length_by_moment": {
    "beats": {"respite": {"min": 250, "max": 350}, "threat": {"min": 450, "max": 550}},
    "directive": {"min": 450, "max": 550},
    "finale": {"min": 500, "max": 650},
    "inquiry": {"min": 120, "max": 220}
  }
}
```

Every key is optional; each is `{min, max}` in words.

**Choosing the range** (in code, before narration; first match wins):

| Order | Moment | Known before narration because | Range used |
|---|---|---|---|
| 1 | The finale (an ending is committed) | `endgame` state | `finale` |
| 2 | A pacing directive fires this turn, or CR-13's push fires | the directive is chosen before the prompt is built | `directive` |
| 3 | Otherwise, by the **previous** turn's classified beat | `beat_counter` recorded it last turn | `beats.<that beat>` |
| 4 | Otherwise | — | `narration.scene_length` |

Row 3 uses the previous beat because a scene's register carries over: the turn after a quiet scene usually continues quietly. The pacing loop exists to break that when it runs too long (row 2 then takes over).

**Inquiry (narrator-judged).** When `inquiry` is authored, the length line gains one sentence: *"If the player's action is only a question or a look around, answer it in {inquiry.min}-{inquiry.max} words instead, and end on something they can act on."* Never on a directive or finale turn. This is deliberately not decided in code: telling a question from an action before narration would need a model call (rejected: no new per-turn call) or a keyword heuristic on the player's text, which misreads "I ask the guard to open the gate".

**Interplay.**
- **CR-13:** independent, and complementary. CR-13 makes idle turns cost no budget; CR-14 makes them short to read. An idle streak's push turn is a directive turn (row 2).
- **Options block:** unchanged; the range governs the scene only, as today.
- **`max_tokens`:** unchanged, a ceiling well above the largest authored `max`. Worth deriving from it later rather than keeping one fixed constant, but that is not this CR.
- **Stats readout / delta block:** unaffected; they are placed by token, not counted in the scene.

**State:** none new. Row 3 reads the beat `beat_counter` already records.

**Authoring tool.** A **Scene length by moment** section beside Narration in the Forms tab (schema-driven, with the beat names offered from `mechanics.pacing_loop.beats`). Lint: a `beats` key naming a beat the pacing loop doesn't define is L10; `min > max` is an error; a `beats` block in a story with no pacing loop is a warning (row 3 can never apply).

**Acceptance**
- **Absent module:** with no `scene_length_by_moment`, every narration prompt is byte-identical to today's (P-2).
- **Selection:** each row of the table selects its range, in order; a moment with no authored range falls through to the next row.
- **Inquiry:** the inquiry sentence appears exactly when `inquiry` is authored and the turn is not a directive or finale turn.
- **Budget:** no new observation field, no new LLM call.
- **Measured:** words per turn by moment are logged, so authored ranges can be checked against what the model actually wrote (the same classifier-first discipline as beat definitions).

---
 
## 4. Worked migrations
 
### 4.1 The Missing Core — before/after prompt shape
- **Before:** ~20 rules (≈2.4k tokens) every turn, plus stats as bare numbers, and acts generated with no destination.
- **After:**
  - ~11 short rules.
  - 5 tier lines, one per axis.
  - ≤3 lore entries, each keyed or conditional.
  - Acts generated with 1–2 `PLANT` waypoints from the steered destinations.
  - An optional diegetic hint or drive nudge on nudge turns.
  - One-shot directives only when something crossed a line.
- The narrator stops doing band lookups and conditional pronoun logic; code does both. The act generator stops inventing where the story goes; the template decides the destinations and the player decides which one.
### 4.2 New Babel — tiers and endings sketch
- **Tiers:**
  - **`attention_level`** — 0 "unremarked" · 25 "sampled" (small wrongnesses, repeated strangers) · 55 "triangulated" (NPCs who know what they're looking at keep their distance) · 80 "answered" (every non-trivial cast risks a reply). `on_enter` at 80 is a strong candidate for a tracked-entity pacing cue.
  - **`neural_load`** — 0 "clear" · 40 "hot" (headache, dropped words) · 70 "redlining" (tremor, lost seconds) · 90 "cooking" (any further cast costs health).
- **Endings:** the canon endgame question, *whether to finish the sentence*, maps onto two destinations plus a catch-all.
| id | Kind | Shape | Waypoints (sketch) |
|----|------|-------|--------------------|
| `completion` | destination | The term returns to the proof | an incomplete proof that waits for the protagonist; the Architect speaks inside their thoughts; a record of an earlier draft of them |
| `unfinished` | destination | Refused, at a price | someone offers to scrape the memory again; the Null Choir teaches a proof for breaking a link |
| `asset_of_uncertain_value` | destination (**catch-all**) | Cordon files you permanently; you live as an unanswered question | the intake file is reopened; a Cordon handler names a price |
| `flatline` | terminal | Health reaches 0 in a scene the judge confirms was lethal | — |
| `answered` | terminal | Attention overflows; something answers a cast | — |
 
---
 
## 5. Phasing and cost
 
| Phase | CRs | New LLM calls | Per-turn prompt |
|-------|-----|---------------|-----------------|
| A | CR-02, CR-03, CR-01 | none | ↓ (band prose removed) |
| B | CR-05, CR-10 | commit judge (≈1–3 per run); terminal confirm (rare); fewer subplot-generation calls | + waypoints in act-gen and extraction prompts; + drive/hint lines on some nudge turns |
| C | CR-04, CR-06 | none | ↓ net (rules shrink, lore capped) |
| D | CR-07, CR-08, CR-09 | none | + one-shot lines; CR-09 measured first |
| E | CR-11, then CR-12 | side-thread generation (only when one starts, at most `max_active` live) | + ≤4 bond lines when both characters are in the scene; + one side-thread line on turns with no armed rule |
| — | CR-13 (independent; any time after CR-05) | none | + one options line while an idle streak is exhausted; + one push line, once per exhausted streak |
| — | CR-14 (independent; beat lengths need a pacing loop) | none | ± the length line changes numbers; + one inquiry sentence when authored. Narration itself gets shorter on quiet turns. |
 
CR-05 moved from Phase C to B in r2 because it addresses the drift directly. It still waits for CR-02 (it's built from conditions) and CR-03 (it adds two new prompt audiences that canon could leak into).
 
The authoring tool (companion spec) can start in parallel with Phase A. Its schema work is the same work CR-03 needs.
 
---
 
## 6. Open questions
 
1. **Commit judge tier.** Use `JUDGMENT_MODEL` (flagship) or the state-update model? A commit is irreversible and happens one to three times per run, so the flagship tier seems justified.
2. **Budget values.** `open_until 40 / narrow_until 90 / commit_by 140` are guesses. What length should a full run of The Missing Core be? The authoring tool's simulator (companion spec §6) can check how often each destination is ready inside the budget, but the target length is your call.
3. **Permanent pruning.** Should any destination be revivable (e.g. a departed Lark returning)? Permanent is simpler and makes consequences stick; revivable needs a `revive_when` condition.
4. **Ending collection.** Crack issues collectible cards per ending reached. With silent commitment, an "endings reached" record per account would be the only place the player ever sees an ending's name. It's cheap (one table, no prompt impact) and fits after-the-fact.
5. **Structural rhyme between stories.** Both stories' canon resolves to *the protagonist is a component of a larger mind, and the ending is whether to complete it.* That works for each story on its own terms, but a player who finishes both will feel the rhyme. Decide whether that is a signature or a coincidence to break before the third story.
6. **Carrier fallback order (CR-10).** When a steered waypoint has no active carrier, the draft activates an authored carrier early before generating one. Keep that order, or never generate spine threads at all (strictly authored)?
7. **`sticky_turns` default.** 3 is a guess; measure against scene-to-scene character persistence on existing saves.
**Resolved in r2**
- *Visible vs hidden hints:* silent. Hints are diegetic only, via pacing nudges, and never shown in the UI.
- *`fallback` for "end story":* no generated endings for templates that author endings. The catch-all destination plus forced commit replace it.
---
 
## Appendix A — Crack features not adopted
 
- **100-character band prompts.** A guardrail for mass-market creators. Palimpsest's tier narration is authored and measured, so no cap is needed.
- **Player-maintained notes for memory.** Crack's biggest public complaint is memory. Palimpsest's compressed summary, pinned flags and triggered lore are the better design.
- **Markdown status window.** Covered by `readout` and `delta_block`, which are already stricter.
- **Visible ending hints and rarity tiers.** Superseded by silent commitment; revisit only with Q4.
## Appendix B — Sources
 
- Crack creator guide: [Story settings](https://help.crack.wrtn.ai/guide/user/tutorial/story/2) · [Stats](https://help.crack.wrtn.ai/guide/user/tutorial/story/4) · [Keyword book](https://help.crack.wrtn.ai/guide/user/tutorial/story/6) · [Endings](https://help.crack.wrtn.ai/guide/user/tutorial/story/8)
- `palimpsest-stories`: `the_missing_core/template.json`, `new_babel/template.json`, `new_babel/README.md`
- `Palimpsest`: `CLAUDE.md`, `docs/Narrative_Engine_Spec.md`, `backend/story_engine.py` (synced master; see caveat in §0)
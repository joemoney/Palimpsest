# Engine v2 / schema v3 — phased implementation plan

**Written for whoever picks this up, including a fresh agent.** `docs/ENGINE_V2_SPEC.md`
says what to build and why. This file says in what order, what each phase has to prove
before the next one starts, and what will quietly go wrong if the order is ignored.

Primary sources, in priority order when they conflict:

1. `CLAUDE.md` — engine invariants. Read first; not optional context.
2. `docs/ENGINE_V2_SPEC.md` — the design. Section references below are to it.
3. `docs/SCHEMA_V2_SPEC.md` — P-1…P-7, all still binding.
4. This file.

**Status: phases 0–4 complete** — all five ports landed; see the phase 4 gate below for
what it measured and the two items it did not close. Phase 5 (relocate `pacing_loop` and
`progression`) is next.

---

## How to read a phase

Each phase carries **Goal / Work / Gate / Risk**. The Gate is not a suggestion — a phase
that cannot pass its gate is a phase that has found something the design got wrong, and the
correct response is to amend the spec, not to proceed with a note.

Two rules hold across every phase:

- **The suite stays green at every phase boundary.** `python3 test/run_all.py`, all files.
  38 at the time of writing.
- **No phase may grow the observation field count without saying why** (§5.1). Phase 0
  exists to make that measurable.

---

## Phase 0 — Baseline measurement

**Goal.** Have numbers, so that later claims about prompt size and field count are
checkable rather than asserted.

**Status: done.** `scripts/measure_baseline.py`, results below.

**Work.** All of it landed as a script rather than a one-off, because phases 2, 4 and 7
each need to re-run the same measurement and diff against this one. It is offline in the
same sense `test/` is — it reuses `test/_llm_stubs`, needs no API key and no network, and
captures the observation prompt by monkeypatching `call_llm_json` exactly as
`test_genre_conformance.py` does. Every target is built through `freeze`/`new_save_state`,
so running it never creates or touches a save under `data/saves/`, and the stubs' tmp
redirect keeps it out of the real `data/perf_stats.json`.

- Observation field count per target — exact, extracted from the built prompt rather than
  from a hand-kept list, so it cannot drift from what the engine actually asks.
- `--hashes` (added in phase 1) prints a sha per assembled prompt. This is the executable
  form of the "byte-identical" and "behaviour identical" phase gates: run it before a
  change and after, and diff.
- Assembled `build_system_prompt` size per target.
- `data/perf_stats.json` p50 per `_timed` label.
- `--markdown` emits the tables below; `--json` is for diffing a later run against this one.

**Gate.** Met — the numbers exist and are committed.

**Risk.** None. This was the cheapest phase and the only one that makes the others
falsifiable. Skipping it is how §5.1 becomes a slogan.

---

## Phase 1 — Registry and pipeline, no behaviour change

**Goal.** The architecture exists and does nothing yet.

**Work.**
- `backend/mechanics/` — flat imports like the rest of `backend/` (`import state_store`),
  entry points already put `backend/` on `sys.path`.
- The `MechanicEngine` contract of §3.1, with do-nothing defaults for every method except
  `resolve`.
- Registry keyed by `(slot, name)`; `state_store.load_state` resolves `mechanics` into bound
  engines once, at load, and hangs them off `ctx`. An unknown engine name fails **at load**,
  loudly (§3.2).
- The `Effect` type and its application step (§6.1), plus `resolve_order` (§6.2).
- Event-log plumbing: `state.events`, appended to but read by nothing yet (§8.2).
- **No mechanic is ported.** Every existing `.get("mechanics", ...)` code path stays exactly
  where it is.

**Status: done.** `backend/mechanics/`, `test/test_mechanics_registry.py`.

**Gate.** Met. Suite green (39 files), and all 12 assembled prompts — narration and
observation, across three stories and three fixtures — byte-identical to a worktree built
at the pre-registry commit. `scripts/measure_baseline.py --hashes` is the committed form of
that check; phases 2 and 5 need it again.

**The rule that made this landable without touching a single existing code path:** a
`mechanics` entry is registry-managed *only* if it carries an explicit `"engine"` key. No
template that ships today has one, so nothing binds, and `story_engine`'s existing
`.get("mechanics", ...)` paths keep owning every mechanic exactly as before. Porting means
adding `"engine": "<name>"` to the template and deleting the old path in the same change —
never one without the other.

**The gate found a real bug before it could find a leak.** The first byte-identical run
failed on three targets, and the cause was not the registry: `_existing_character_names`
did `list()` over a set, so `EXISTING CHARACTERS (do not repeat)` — interpolated into the
state-update, subplot-generation, steering and act-check prompts — rendered in a different
order on every process. Same context, different prompt bytes, which defeats prompt caching
and makes any prompt regression-untestable. Every other caller of `_all_character_names`
already sorted; this was the one that didn't. Fixed as a prerequisite, then the gate passed
clean. Worth recording because it is the argument for the gate: a phase that asserts
"nothing changed" is the only phase that can *detect* something that was already wrong.

**Risk.** Was scope creep — the temptation to port `stats` "while you're in there." Avoided;
zero engines exist. Phase 1's value is that it isolates registry bugs from porting bugs, and
phase 2 now starts from a seam that is already exercised.

---

## Phase 2 — Port `stats` → `bounded_counter`

**Status: done.** `backend/mechanics/resource.py`, `scripts/equivalence_probe.py`.

**Goal.** Prove the registry can host a real mechanic. This was the stop-gate, and it
tripped before a line was written — see *What the stop-gate caught* below.

**What shipped.**
- `stats` / `bounded_counter`: bounds and their defaults, the clamp, the "never a new axis"
  invariant, the `visible` dial, all three footer wordings, and the P-7 readout.
- **Declare-to-bind.** A story authors `mechanics.stats.engine = "bounded_counter"` or gets
  no stat mechanic at all. The implicit `STAT_FLOOR = 0` is deleted.
- `the_missing_core` and the `survival` fixture gained the declaration; `new_babel` gained
  `{"engine": "bounded_counter", "floor": 0, "ceiling": null}`, which is exactly what it was
  silently getting from the old constant. `example` has no stats and was untouched.
- `_stat_readout_cfg` / `render_stat_readout` / `apply_stat_readouts` survive as thin
  delegations, so nothing else in the codebase had to move.
- **No schema cutover** (§8.3), and stats stay at `state.protagonist.stats`.

**Gate.** Met, and more strictly than specified:
- `scripts/equivalence_probe.py --saves`: byte-identical stat behaviour on all 8 targets —
  6 templates plus 3 real saves — across a delta sequence crossing both bounds and poking
  an undeclared axis.
- All 12 assembled prompts byte-identical to phase 1. The gate only asked for "diff limited
  to intended changes"; the diff is empty.
- Full suite green (39 files).

**Not met, because it was never possible:** "`example`'s observation field count comes out
lower." `example` has no stats, so it never had a `stat_changes` field to lose. Counts are
unchanged everywhere (11/15/15/8/8/9) and that is correct for this phase: converting
`stat_changes` into an E-3 event vocabulary changes the prompt, which this phase's own gate
forbids. **The field-count reduction belongs to phase 4.**

### What the stop-gate caught

Phase 2 as originally written could not be executed, and the plan's rule — a phase that
cannot pass its gate has found a design error, so amend rather than proceed — is what
produced the shape above. Three errors, all from writing the phase against the spec instead
of against the templates:

1. **"Rewrite `stories/example`'s `mechanics.stats`"** — `example` has no stats anywhere.
2. **"`example`'s field count must come out lower"** — it never had `stat_changes`.
3. **"The three stat tests pass unmodified" contradicted "delete the old path."** All three
   author `mechanics.stats` with no `engine` key, and `test_stat_bounds` asserted the
   no-block fallback outright. Both halves could not hold.

The underlying discovery is worth keeping: **`stats` is the least module-shaped mechanic in
the system, not the most.** Every other mechanic is keyed on its template block; stats were
keyed on *state* (`stat_changes` appears iff `protagonist.stats` is non-empty) while
`mechanics.stats` merely configured behaviour that was already on. That is why it had an
always-on default at all, and why it was the wrong thing to call the cleanest first port.

**The gate that replaced it generalises.** "These test files do not change" fails for any
port that moves where configuration lives, so it would have failed again at phase 4.
`equivalence_probe.py` exercises behaviour instead of pinning a test's shape, and runs
against real saves. Use it for every remaining port.

**One new failure mode, guarded.** Declare-to-bind means a story can seed
`protagonist.stats` and never declare the engine, leaving the stats inert — no bounds, no
prompt line, no `stat_changes`. `mechanics.validate` now prints a warning for exactly that
shape. A warning, not a raise: it is an authoring smell, not broken content.

---

## Phase 3 — Conformance fixtures

**Status: done.** `test/test_genre_conformance.py`, checks (5)–(7).

**Goal.** Prove the registry is generic, and get the guard in place *before* four more
engines are ported against an untested genericity claim.

**Corrected while doing it: "three disjoint engine sets" was an overstatement.** `CLAUDE.md`
and P-6 both ask that each fixture use "a deliberately different subset" of the optional
modules, not that the subsets be disjoint — `revelations` will legitimately appear in two
fixtures once it is an engine. Disjointness was never the claim and is not achievable.

**Also corrected: only one engine exists.** Phase 3 was written as if several did. What
landed is the machinery plus the one real case, with a standing rule rather than a
placeholder:

> **Every phase 4 port adds its engine to `EXPECTED_ENGINES` and to at least one fixture in
> the same commit**, or nothing is guarding P-2 for it.

That keeps the guard growing with the ports, which is what the original ordering wanted.
Writing three speculative fixture sets now would have tested nothing.

**What shipped.**
- `EXPECTED_ENGINES` per fixture, **written out in the test**, never derived — same rule as
  `EXPECTED_ABSENT`, so deleting a declaration fails loudly instead of shrinking coverage.
- Check (5): the registry binds exactly what a fixture declares.
- Check (6): an unbound slot contributes no `prompt_sections` entry — P-2 at the registry
  level, where it is structural rather than a matter of `.get()` discipline.
- Check (7): every bound engine declares a real `prompt_budget`.
- P-4 through the registry: the minimal template binds nothing, contributes nothing, and
  survives the whole turn pipeline without creating an event log.
- §5.4 became structural in `mechanics.prompt_sections`: an engine that contributes prompt
  text and declares no budget now **raises**. Leaving `prompt_budget` at 0 and calling it
  unlimited is how a bounded prompt stops being bounded. `bounded_counter` declares 600
  against a largest real section of 433.

**Gate.** Met, and verified by deliberately breaking it three ways rather than by assuming:
- removing `survival`'s `engine` key → fails (`authors stats but 'Stats (' never reached
  the narration prompt`)
- giving `regency` an engine it should not have → fails (`expected NOT to author stats`)
- lying in `EXPECTED_ENGINES` → fails (`binds ['stats'], expected []`)

The third matters on its own: the first two were caught by the pre-existing marker checks,
so without it the new binding assertion could have been dead code that passed forever.

**Risk.** Was ordering. Discharged — the guard exists before phase 4, and the standing rule
above is what keeps it honest as engines land.

---

## Phase 4 — Port the remaining state-carrying engines

**Status: done.** All five ports landed — `backend/mechanics/` `social.py`, `items.py`,
`reveal.py`, `failure.py`, `threads.py`. The gate was met on field count; it left §12.5 and
the `stat_changes` conversion open, both recorded under *Phase 4 gate* below.

**Goal.** The observation pass stops being a state diff.

**Work.** In this order, each landing green before the next:
1. ~~`relationship` / `scored_axis` (§7.2) — and the scale stops being hardcoded `±100`.~~
   **Done** — `backend/mechanics/social.py`; see *Step 1* below.
2. ~~`inventory` / `tagged_items` (§7.3) — items become records; `items_lost`'s exact-string
   match goes away.~~ **Done** — `backend/mechanics/items.py`; see *Step 2* below.
3. ~~`revelation` / `triggered_reveal` (§7.5).~~ **Done** — `backend/mechanics/reveal.py`;
   see *Step 3* below.
4. ~~`failure` / `triggered_ending` (§7.6) — effect unchanged: set `endgame.requested`, build
   `final_arc` from `ending_prompt`, route into the existing endgame machinery.~~ **Done** —
   `backend/mechanics/failure.py`; see *Step 4* below.
5. ~~Subplot progress (§2.1) — the model classifies how materially a beat advanced a thread,
   the engine prices it.~~ **Done** — `backend/mechanics/threads.py`; see *Step 5* below.
   `check_and_advance_act` was not touched, and neither was `check_subplot_status` itself —
   only the progress arithmetic moved. See the step note for why the rest stayed.

**Gate.** Per-engine tests plus absent-engine tests for each. **Each port also adds its
engine to `EXPECTED_ENGINES` and to at least one fixture in the same commit** (phase 3's
standing rule). Field count measured against phase 0 after each port — and this is the phase
where it must actually fall, since phase 2 deliberately kept `stat_changes` v2-shaped and
deferred the reduction here. Any increase carries a stated reason in the commit message.
Re-measure prompt size here — it is the input to phase 7's go/no-go.

**Also settle §12.5 here.** Once the observation pass is pure classification, run it on
Tier C and on Tier AB over the same held-out turns and compare misclassification rate
against the latency and cost delta. §5.5 has the argument in both directions and says
plainly that it cannot be settled on paper; this is the phase where the ported pass first
exists to measure. Tier C stands until the numbers say otherwise.

**Risk.** The highest-volume phase and the one where P-2 regressions hide. An engine that is
absent must leak nothing; the phase 3 fixtures are what catch it, which is why they come
first.

### A sixth item the gate implies and the work list did not name

Phase 2 recorded that "the field-count reduction belongs to phase 4" because it kept
`stat_changes` v2-shaped. Read strictly, that means converting `stat_changes` into the E-3
event vocabulary of §5.1 — and that is **not** one of the five ports above, because it is not
a port at all. `stats` is already an engine. What is missing is authored `costs` tables
(§8.1's `axes.*.costs`) in every story that has stats, plus a shared `effort` event (§7.1),
and until those exist the conversion would delete a working mechanic rather than move it.

Tracked here rather than smuggled into a port: **the five ports are the phase, and
`stat_changes` → events is its own item, to be scheduled once the ports show what the field
count actually does.** Phase 4's gate is met by the ports falling, not by this one.

### Step 1 — `relationships` / `scored_axis` *(done)*

**What shipped.** `backend/mechanics/social.py`. The scale, the price list, the tiers, the
per-window cap and the eviction rule all moved out of `story_engine`; `relationship_changes`
became `social`, and three hand-written copies of `-100 hostile to +100 devoted` became one
`axis_hint()`.

**The slot stays `relationships`, not §8.1's `relationship`** — the same call phase 2 made
for `stats` over `resource`, and for the same reason.

**The observation contract became real, which is the part that outlives this port.** Phase 2
let `bounded_counter` reach the prompt through a bespoke `schema_field()` that `story_engine`
called by name. That does not survive a second engine, so `ObservationField` (schema line,
context line, instruction paragraph) and `MechanicEngine.events()` now exist, `bounded_counter`
was moved onto them, and `update_progress_from_turn`'s hand-sequenced per-mechanic apply
blocks collapsed into one `mechanics.run_observation_pipeline(ctx, diff)` at the end.
§6.2's "order is declared, not incidental" is true of the code now and was not before.

**`registers` is required, not defaulted.** A story declaring `scored_axis` with no price
list raises. A default table would be the engine authoring the story's social physics, which
is exactly what §7.1's implicit `STAT_FLOOR = 0` turned out to be.

**Gate.**
- Suite green (39 files, same two pre-existing environment failures noted below).
- `scripts/equivalence_probe.py`: byte-identical on every target, i.e. moving
  `bounded_counter` onto the new contract changed no stat behaviour at all.
- Field count unchanged everywhere — 11/8/8/9, exactly phase 0. Correct for this step:
  `relationship_changes` → `social` is one field for one field. The reduction is step 2's
  (`items_gained` + `items_lost` → one), and the gate is measured at the end of the phase,
  not after each step.
- **Observation prompt grew, and that is the honest number**: `example` 7,053 → 7,587 chars
  (+7.6%), `regency` 4,263 → 4,680. The cost is the register vocabulary itself — E-3 cannot
  ask the model to classify into a vocabulary without showing it the vocabulary. `courtroom`
  and `survival`, which author no relationships, are byte-identical, which is the P-2
  evidence that the growth is confined to stories that bought something with it.
  Narration prompts are flat to within ~20 chars.

**What the step found.** Two things, neither in the design:

- **An empty roster used to render `Relationships: {}`.** v2 gated the PLAYER-line fragment
  on the *template* authoring the module, so a story with relationships and no discovered
  characters yet — every story, for its first turns — sent a zeroed header every turn. That
  is precisely what P-2 forbids, and it had been doing it since the module existed. The
  engine omits the section instead, which is why `test_genre_conformance`'s narration marker
  for this module had to move from `"Relationships:"` to the KNOWN CHARACTERS scale clause.
- **Eviction had to move inside `resolve()` to stay correct.** v2 applied deltas, then
  evicted; both were statements in one function and their order was load-bearing but
  unstated. As an engine it has to be explicit, because a newcomer scored this turn is not
  yet in the roster when the roster is measured — get it backwards and a strong new bond is
  dropped on arrival. There is now a test that fails if the two are reordered.

### Step 2 — `inventory` / `tagged_items` *(done)*

**What shipped.** `backend/mechanics/items.py`. Items are records (`id`, `label`, `tags`,
optional `uses`) rather than free strings; `items_gained` and `items_lost` became one
`inventory` field; expenditure cites a minted `itm_NNN` id instead of matching a stored
string character for character. Capacity is owned and refuses rather than evicts.

**The field-count reduction landed here, and the bigger half was not the one §7.3
predicted.** Merging two fields into one saves one field. Making inventory a *declared
module* saves two, for every story that has no inventory concept — and `items_gained` /
`items_lost` had been unconditional since v1, asked of a courtroom drama and a comedy of
manners every turn. That was a standing P-2 violation that nothing could catch, because
inventory had no module to be absent from. Measured against phase 0:

| | `example` | `courtroom` | `regency` | `survival` |
|---|---|---|---|---|
| Fields, phase 0 → now | 11 → **10** | 8 → **6** | 8 → **6** | 9 → **8** |
| Observation prompt | 7,053 → 7,772 | 3,494 → **3,324** | 4,263 → **4,233** | 3,356 → 3,795 |

**Prompt size moved both ways, and the direction says what it should.** Stories that shed a
module got smaller. `example` grew ~10%, and that is the honest price of E-3: a model that
classifies into a vocabulary has to be shown the vocabulary, and `example` now carries both
a register list and a tag list. `survival` grew because it *gained* an inventory module it
never had (capacity, tags, `uses`), so its row is not like for like. Both new instruction
blocks were tightened after the first measurement — worth doing, since the observation
prompt was already the larger of the two prompts at phase 0.

**What the step found.** Capacity and §6.2's declared order collide, and neither can simply
win. §6.2 records "gains before losses so a gain cashed in on the same turn resolves" — free
while inventory was unbounded. With a capacity, gains-first refuses "put the rope down, take
the axe", and losses-first breaks the same-turn pickup-and-spend. `resolve()` therefore runs
three passes: expenditures of things already held, then gains against the room that freed,
then whatever expenditures are left (which can only be of something gained this turn). Both
properties hold, and the ordering is stated rather than implied by statement order — which is
the argument for §6.2 in miniature.

**Also fixed on the way through:** `new_save_state` shallow-copied `starting_inventory`
straight out of the frozen template. Harmless while items were strings; with records it
would have seeded a save with `FrozenDict`s, so spending a use would raise on the first turn
and only until the save round-tripped through disk — about the worst shape a bug can have.
It thaws now.

**Gate.** Suite green. `equivalence_probe.py` identical to step 1 on every target. Field
count fell on all four targets, which is the first time phase 4's own gate has been met
rather than deferred.

### Step 3 — `revelations` / `triggered_reveal` *(done)*

**What shipped.** `backend/mechanics/reveal.py`. The engine owns both ends of the pipe -
the unrevealed triggers the observation pass sees and the revealed content the narrator sees
- plus §12's placement queue and the 12-fragment display cap. `memory_fragments_revealed`
and `revelations_eligible` became one `revelations` field. `mechanics.revelations` was a
bare list, which has nowhere to hang an `"engine"` key, so it becomes
`{"engine": ..., "entries": [...]}` like every other slot.

**The first engine with a cadence (§5.2).** A story whose clue chain is exhausted now asks
nothing at all. §5.2 attaches a constraint to that privilege — an engine that may skip turns
must phrase its question over a window — and this engine satisfies it the easy way: the skip
is *structural*, not temporal. It stops asking when there is nothing left to ask about,
never because it decided to wait, so its question stays about exactly the turn it is asked
on.

**`after` is ordering without an expression language.** §7.5 wants triggers upgraded from
prose to predicates over engine state; the predicate evaluator belongs to `gate` (§7.4/§7.9),
which is phase 6, and building it here would be building phase 6 early in the wrong module.
`after: [ids]` buys the specific thing §7.5 names — a clue chain that cannot fire out of
sequence — for one list per entry and no evaluator at all. `requires` is phase 6's to add
once `gate` brings the evaluator. **This engine is ported; its triggers are not yet upgraded,
and those are two different claims.**

**Gate.** Suite green; `equivalence_probe.py` identical. Field counts are flat on all four
available targets (10/6/6/8) and that is measurement reach, not a null result: the merge only
pays on a story with **both** a `pacing_loop` and revelations, and no available template has
both — `example` has the loop and no revelations, `courtroom` and `regency` the reverse. The
two that do are in the private submodule, which this working copy cannot check out. Measured
directly instead, on `example` with a revelation block patched in:

| | engine fields |
|---|---|
| chain live | 3 (`social`, `inventory`, `revelations`) — was 4 under v2 |
| chain exhausted | 2 — the cadence |

Observation prompts fell again for the two fixtures that carry revelations: `courtroom`
3,324 → 3,102, `regency` 4,233 → 4,079.

**One test outside its own port changed:** `test_pacing_loop.py`'s §12 placement section
asserted on `revelations_eligible` by name. Phase 5's gate is that that file passes
*unmodified*, and this is worth flagging rather than burying — but the assertions touched are
about the revelation field's name, not about pacing behaviour, and every pacing assertion in
the file is untouched. If phase 5 wants its gate read strictly, this is the one prior edit to
account for.

### Step 4 — `failure_conditions` / `triggered_ending` *(done)*

**What shipped.** `backend/mechanics/failure.py`. The engine owns which conditions are
askable and which one fired. §7.6 requires the *effect* to be unchanged, so
`test_failure_conditions.py` passing on the same assertions is most of this port's gate.

**The handler lives in `story_engine`, and that seam is the point.** §7.6 says a failure
routes into the *existing* endgame machinery rather than a new code path. That machinery is
`_begin_endgame`, shared with the player's own "end the story" request. Duplicating it inside
the engine to make the module self-contained would trade a real invariant — one ending path —
for a cosmetic one, so the engine emits `Effect("failure.trigger", ...)` and `story_engine`
registers what applies it. The engine decides *that* the story ends; it does not own *how*.
Same shape as §7.4's refusal rule, one layer down.

**`resolve_order = 90` replaced "it is the last block in the function".** v2 applied failure
conditions after everything else, deliberately, so a failing turn's items, standing and
progress all landed first — encoded as a comment plus a statement position. That is exactly
what §6.2 exists to turn into declared data, and this is the first port where the declaration
does real work rather than restating an ordering nothing depended on. There is now a test
that fails if the ordering regresses, which v2 had no way to write.

**Its cadence is a decision, not a caller convention.** v2 emptied the condition list at the
call site before building the prompt; the engine now reads `endgame.requested` itself. §7.6
also wants it to ask only about *reachable* conditions, and reachability is a predicate over
engine state — `gate`'s evaluator, phase 6. Same split as `triggered_reveal`.

**`prompt_budget = 0` turned out to be legitimate, and check (7) was wrong about it.** The
conformance fixture asserted every bound engine declares a positive budget, which held only
while every engine happened to contribute narration text. `triggered_ending` contributes
none — an ending is entered through the endgame machinery, which writes its own act. The
check is now the pairing in both directions: text implies a budget, no budget implies no
text. Asserting `> 0` unconditionally would force a made-up number onto an engine that spends
nothing, which is how a budget stops meaning anything.

**Gate.** Suite green; `equivalence_probe.py` identical. Field counts flat at 10/6/6/8 — a
1-for-1 port, as expected. Observation prompts down slightly again for the two fixtures that
carry conditions (`courtroom` 3,102 → 3,092, `survival` 3,795 → 3,785).

### Step 5 — `subplots` / `weighted_threads` *(done)*

**What shipped.** `backend/mechanics/threads.py`. `subplot_progress: {id: <integer 0-100>}`
became `subplot_beats: {id: "touched" | "advanced" | "decisive" | "resolved"}`, and the
engine prices the classification. This is the port §2.1 spends a page *refusing* to make an
exception for, so the reasoning is worth having to hand: act advancement keeps its verdict
with the model because "has this act resolved" has no ground truth for `resolve()` to
compute, while `subplot_progress` was arithmetic performed by the model — the same shape as
`stat_changes` and `relationship_changes`.

**`resolved` is priced structurally rather than from the table.** It completes the thread,
whatever its threshold and wherever it had got to. It is the one classification the model
cannot express as a number without doing the engine's arithmetic for it.

**Weights are absolute, which is what keeps `span: multi_act` meaningful.** A `multi_act`
thread's threshold is 250 rather than 100 and a decisive beat is worth the same in both, so
the longer thread genuinely takes more beats. Scaling the weights to the threshold would have
quietly undone the only lever that distinguishes the spans.

**CR-08's mechanism went; its concern stayed.** CR-08 added `[progress/threshold]` to the
prompt so the model could tell a nudge from a finishing blow. Showing a running total to a
model that is no longer allowed to add is an invitation to start again, so it now sees a band
— *just begun*, *under way*, *close to resolution*. `test_subplot_progress_prompt.py` is
still CR-08's test; it asserts the band instead of the numbers.

**Unlike `scored_axis`, this engine ships a default ladder.** The difference is real and
worth stating, because "required vs defaulted config" has now gone both ways in one phase: a
register price list is a story's social physics and therefore P-3 creative, while "how much
of a thread does a decisive beat settle" is structural pacing that reads the same in every
genre. A story overrides `weights` if it disagrees; none has to invent one to get a working
mechanic.

**The third unconditional field.** `subplot_progress` was asked of every story every turn,
including `courtroom`, which authors no threads at all and is the deliberately single-thread
fixture. That is `items_gained`/`items_lost` again, and it goes the same way.

**What deliberately stayed.** `check_subplot_status` is still `story_engine`'s: completion
detection is called from the turn loop *and* from `subplot_manager`'s manual path, and it
drives act advancement, regeneration and `completed_subplots`. §2.1 scopes this port to the
progress arithmetic. The merged seed+runtime subplot view did move into `threads.py`, but as
a **module function** rather than an engine method, with `story_engine._subplot_view`
delegating to it — because `check_subplot_status`, `generate_new_subplot`, act advancement
and `subplot_manager` all need that view, and none of them may go dark because a story did
not declare an engine.

---

## Phase 4 gate

**Met on the field count, which is the one §5.4 actually budgets.** Measured with
`scripts/measure_baseline.py`, against phase 0:

| | `example` | `courtroom` | `regency` | `survival` |
|---|---|---|---|---|
| Observation fields | 11 → **10** | 8 → **5** | 8 → **6** | 9 → **8** |
| Observation prompt (chars) | 7,053 → 7,979 | 3,494 → **2,794** | 4,263 → 4,286 | 3,356 → 3,992 |
| Narration prompt (chars) | 5,434 → 5,416 | 3,055 → 3,034 | 3,396 → 3,330 | 3,700 → 3,680 |

**Where the reduction came from is not where §7 predicted.** The catalogue expected merges —
two inventory fields into one, two revelation fields into one. Those happened, but the larger
saving was that three fields were *unconditional*: `items_gained`, `items_lost` and
`subplot_progress` were asked of every story every turn, including a courtroom drama with no
inventory and no threads. P-2 had no way to catch that, because none of the three had a
module to be absent from. Declare-to-bind gave them one.

**Prompt size moved both ways, and the shape of that is the real finding.** `courtroom` — the
fixture that sheds the most modules — fell 20%. `example` rose 13%, and that is E-3's price
stated plainly: a model that classifies into a vocabulary must be shown the vocabulary, and
`example` now carries four (registers, item tags, subplot beats, and its pre-existing pacing
beats). Broken down, its engine fields cost 2,455 chars of the 7,979: `social` 1,224,
`subplot_beats` 633, `inventory` 598. Every instruction block was tightened once after
measurement, which recovered ~400 chars across the three.

**Phase 7's go/no-go, now decidable.** The two flagship stories were unmeasurable while
the private submodule sat unpushed; it was pushed on 2026-09-13 and both converted to the
v3 shapes, so here are the numbers the decision was waiting on:

| | phase 0 | now |
|---|---|---|
| `new_babel` (turn 0 / post-creation) | 14 / 15 | **12 / 13** |
| `the_missing_core` | not tabled | **12 / 13** |

The ports removed exactly the two they should have (`items_gained` + `items_lost` → one,
`memory_fragments_revealed` + `revelations_eligible` → one); the other three were 1-for-1.
`new_babel`'s 13 breaks down as:

- **3 core** — `flags_set`, `scene_update`, `new_characters`
- **5 ported engines** — `stat_changes`, `social`, `inventory`, `revelations`, `subplot_beats`
- **5 not yet ported** — `entity_interaction`, `beat_type`, `intensity`, `leverage_gained`,
  `leverage_spent`

Phase 5 merges `beat_type`/`intensity` into one and `leverage_gained`/`leverage_spent` into
one, which lands a fully-ported flagship at **11 against a budget of 10** — over, but by one,
and only on the two flagships. So **phase 7 is indicated rather than merely conditional, and
it is not urgent**: one field over budget on two stories does not justify concurrent
sharding before phase 5 has actually produced that number. Re-measure after phase 5 and
decide there.

**Phase 0's "around 9 or 10 fully ported" projection was optimistic by one to two, and it is
worth knowing why**, because the same reasoning would mislead again. It assumed revelations
would cadence-gate to "usually none" — but `new_babel` authors 19 entries and
`the_missing_core` 6, so the chain stays live for most of a playthrough and the field is
present nearly every turn. And it never counted `entity_interaction`, which belongs to no
§7 catalogue entry and so was invisible to a walk through the catalogue. **A projection made
by walking the port list misses every field the port list does not mention.**

**One number nobody was watching.** `the_missing_core`'s *narration* prompt is 19,795 chars
(~4,950 tokens) — 58% larger than `new_babel`'s 12,534 and 3.7× `example`'s. Phase 0 tabled
observation prompts because §5.1 was aimed there, and this one has been the bigger prompt all
along. Nothing in engine v2 caused it (its narration prompt is flat across phase 4), and
sharding the observation pass would not touch it. Worth a look on its own terms before
phase 7 optimises the smaller half.

**What the gate did not close, and neither item is hidden:**

- **§12.5 (is Tier C still right for the observation pass?) is not settled.** The phase asked
  for the ported pass to be run on Tier C and Tier AB over the same held-out turns and the
  misclassification rates compared. That needs live API calls against real turns; the whole
  test suite here is offline by design and this working copy has no key. The pass now exists
  in the form the experiment needs, which was the blocker — the measurement is schedulable
  work, not design work. **Tier C stands until the numbers say otherwise**, per §5.5.
- **`stat_changes` → event vocabulary is still v2-shaped.** Phase 2 deferred "the field-count
  reduction" here and this phase delivered it from the other four ports; the `stat_changes`
  conversion itself needs authored `costs` tables (§8.1) in every story with stats plus a
  shared `effort` event (§7.1), which is content work for a mechanic that is already ported.
  Tracked as its own item above rather than folded into a port it does not belong to.

**Also worth recording: four of the five ports found something the design did not
anticipate.** `Relationships: {}` had been a zeroed header since the module existed;
eviction's ordering relative to scoring was load-bearing and unstated; capacity and §6.2's
declared order genuinely conflict and needed a third pass to satisfy both; and
`prompt_budget = 0` turned out to be legitimate, which the conformance fixture's check (7)
had assumed away. None of those were visible before the mechanic had a module.

---

## Phase 5 — Relocate `pacing_loop` and `progression`

**Status: done, in two steps.** `backend/mechanics/pacing.py` (`beat_counter`) and
`backend/mechanics/ledger.py` (`spendable_ledger`). **5a** relocated both with zero edits to
`test_pacing_loop.py`; **5b** then merged each engine's two observation fields into one, which
is the edit 5a existed to avoid making by accident. **Phase 7's number is now measured: a
fully-ported flagship sits at 11 against a budget of 10** — see *Phase 5b* below.

**Goal.** Move the two mechanics that are already the right shape, and change nothing about
them.

**Work.** Both behind the registry, behaviour identical. `pacing_loop` already has the model
classifying while the engine owns counters, resets, effective thresholds and arming
(§1.3); `progression` already has the model emitting labels and kinds while the engine owns
numbering, spend-matching and eviction.

**Gate.** `test_pacing_loop.py` passes **unmodified**. If hosting them requires distorting
them, the registry is wrong — same logic as phase 2, later and cheaper to act on.

**Risk.** Low, and that is the point: these are the control group.

### Phase 5 gate

**Met, and met literally.** `test_pacing_loop.py` is byte-for-byte unchanged, which is the
whole claim: a relocation that needed its own test rewritten would not have been a
relocation. Suite green at 42 files.

**The observation prompt is the same bytes in a different order.** Measured on `example`,
before and after: 8,008 chars both times, and the diff is one block moving. `BEAT TYPES` and
`CURRENT <LABEL>` used to be appended after `CURRENT SCENE` by hand; they are now the
`context` of the fields they belong to, so they travel with them into the `engine_context`
block near the top. Nothing was added or dropped. This is worth recording rather than
waving through: phase 1's gate was byte-identical prompts, and this is the first phase to
break that deliberately. It breaks it because the §3.1 contract owns placement now, which is
the point of having the contract.

**Field counts are flat, and that is the result, not a null one.** `new_babel` and
`the_missing_core` stay at 12/13, `example` at 10. The two fixtures that gained a module
moved for that reason alone (`regency` 6 → 8 with `progression`, `survival` 8 → 10 with
`pacing_loop`), not because the port cost anything.

### What phase 5 did not deliver

**The §5.4 merge — deferred to 5b above, not skipped.** As shipped in 5a both engines
contributed **two** fields where §5.4 says one
- `beat_type`/`intensity` and `leverage_gained`/`leverage_spent`, each the textbook case for
the richer type that `revelations` already became in phase 4. They were not merged here
because this phase's gate is `test_pacing_loop.py` passing unmodified and that file asserts
on all four names, in the prompt and as diff keys. **The two goals are incompatible and the
plan asserted both** - this section said "change nothing", while *Phase 4 gate* above said
"phase 5 merges" the pairs. That contradiction is resolved in favour of the phase's own
stated gate, and the merge is now its own step.

**That left phase 7's go/no-go waiting on a number**, which 5b then produced. Read 5a's flat
counts as evidence of nothing: they are what "relocated and changed nothing" looks like.

### Phase 5b — the §5.4 merge

**`beat_type`+`intensity` became `beat: {type, intensity}`, and
`leverage_gained`+`leverage_spent` became `leverage: {gained, spent}`**, following the shape
`revelations` already took in phase 4. Each pair was always one question asked in two halves:
what kind of scene this was and how hard it landed; what the ledger gained and what it spent.

**The measurement phase 7 was waiting on, finally taken:**

| | phase 0 | after phase 4 | after 5b |
|---|---|---|---|
| `new_babel` (turn 0 / post-creation) | 14 / 15 | 12 / 13 | **10 / 11** |
| `the_missing_core` | not tabled | 12 / 13 | **10 / 11** |
| `example` | 11 | 10 | **8** |
| `regency` / `survival` | 8 / 9 | 8 / 10 | **7 / 9** |

The projection was exactly right: **11 against a budget of 10, over by one, and only on the two
flagships.** `example` and every fixture are inside it.

**It cost 19 characters.** Observation prompts moved `example` 7,979 → 7,998, `new_babel`
11,117 → 11,136, `the_missing_core` 10,151 → 10,170 — the nesting wrapper, near enough free.
That is worth stating precisely because the first cut of the merge cost **144**: a separate
`instruction` paragraph explaining that both halves are lists. Folding that sentence back into
the schema line recovered all of it. A merge that buys a field reduction with prompt characters
is a poor trade and an easy one to make without noticing, since §5.4 budgets fields and nothing
was watching the other number.

**Phase 7's go/no-go, decidable now and still not urgent.** One field over budget, on two
stories, with every other target inside it. §5.1 wants sharding when the observation pass is
carrying more questions than one call should hold; 11 is not that, and the remaining overage is
a single field. The two candidates for closing it are both real and neither is sharding:
`entity_interaction` belongs to no §7 catalogue entry and has never been ported, and
`stat_changes` is still v2-shaped pending the `costs` tables §8.1 wants. **Either would land the
flagships at 10 without any concurrency work at all**, which is the cheaper experiment and
should be run first.

**Two shims retired, as planned.** `story_engine._pacing_rule` and `LEVERAGE_LIMIT` existed only
because 5a's gate called them by name; the gate test now calls `mechanics.pacing.ENGINE.rule` and
`mechanics.ledger.LIMIT` directly and both shims are gone. `_rule_effective_threshold` went with
them - the directive builder calls the engine.

---

### What hosting them actually required

**One thing moved that is not a mechanic: `all_acts`/`current_act`.** `beat_counter` resolves
a rule's effective threshold against the current act (§13), an engine may not import the
module that imports it, and duplicating an act lookup is how two act lookups drift apart. So
both readers now live in `mechanics/__init__.py` and `story_engine._all_acts`/`_current_act`
delegate to them - one implementation, and every existing caller (`app`, `plot_manager`,
`subplot_manager`, three test files) keeps the name it already used. **§7.9 still holds**:
what it keeps out of the registry is act *advancement*, i.e. owning the verdict on when an
act ends. Reading which act is current is not owning that.

**Two names stayed in `story_engine` only because the gate needs them.**
`_pacing_rule` and `LEVERAGE_LIMIT` are now one-line delegations to the engines that own
them, because `test_pacing_loop.py` calls both by name and the gate is that it does not
change. They are aliases of a single definition rather than second copies, and the merge step
is the natural place to retire them.

**One test outside its own port changed**, flagged here rather than buried, exactly as phase
4 flagged the one it had to touch: `test_triggered_reveal.py`'s fake context authored
`pacing_loop` without an `engine` key, and reveal placement is gated on the *declared*
engine now rather than the bare block. The fixture gained the declaration; not one assertion
moved.

**Fixtures, per phase 3's standing rule.** `survival` authors the pacing loop and `regency`
the ledger — split across two fixtures rather than piled onto one, so P-6's "deliberately
different subsets" survives. Both engines are now guarded in both directions.

---

## Phase 6 — `gate`, and authored act preconditions

**Goal.** The world can refuse the player, and an authored act gets a floor.

**Work.**
- `gate` / `precondition` (§7.4): the predicate evaluator, and the pre-action check in the
  turn pipeline (§4) — before the narration call, no LLM cost.
- The refusal path: engine decides *that* the player is refused, template supplies
  `refusal_hint`, model writes the sentence. Never `refusal_text`.
- §2.2's authored act `requires`, running on the same evaluator.

**Gate.**
- Refusal path test: a gated action produces a refusal, no state change, and no observation
  pass.
- **Latching test.** A flag predicate must still evaluate true after `RECENT_TURN_LIMIT`
  turns have passed — which means it reads `flags.active ∪ flags.archive`. The naive
  implementation reads `active`, and with `act_check_frequency` defaulting to 12 against a
  10-turn window it would be false at nearly every check. This test exists to catch exactly
  that.
- No reachable deadlock: an act whose `requires` references a dropped or unknown referent
  must degrade, never block forever.

**Risk.** The predicate evaluator is the piece most likely to grow into a general expression
language. Keep it to the referent classes §2.2's table marks usable. Generated acts carry no
`requires` — that is a design decision (§2.2), not an unfinished edge.

**Detector measured** — `docs/analysis_and_plans/ENGINE_V2/GATE_DETECTION_MEASUREMENT.md`,
`scripts/gate_detection_eval.py`. Zero false refusals across 119 real player actions, ~90%
recall on genuine attempts, right gate every time it fires. Two authoring findings came out of
it and neither can be enforced in code: **a `refusal_hint` written as a finished sentence is
returned verbatim ~55% of the time** (fragments drop that to zero), and a gate is recognised
most reliably when its `target` reads the way the fiction names the place. The rest of the phase
writeup is still outstanding.

---

## Phase 7 — Observation sharding *(conditional)*

**Goal.** Keep the observation prompt bounded once several engines contribute to it.

**Do not start this phase without phase 4's measurement.** Premature sharding buys latency
risk and a `STATUS_LABELS` complication for nothing. If field count after phase 4 is inside
§5.4's budget, skip the phase and say so.

**Work.** Groups via `observation_group`, run **concurrently** — the concurrency is what
preserves `CLAUDE.md`'s "primary-plus-fallback fits inside gunicorn's `--timeout`", since
sequential shards would multiply the budget that parallel shards leave alone. Every shard is Tier C (§5.5) —
sharding splits a call, it does not change what the call is for.

Phase 0 measured `example` at 11 observation fields and `new_babel` at 15, against a §5.4
budget of ten, so a *fully ported* flagship story is expected to land just inside one shard.
Read that as the null hypothesis this phase has to disprove, not as a reason to start.

**Gate.**
- `test_status_labels.py`'s bidirectional mirror passes **without being weakened**. The
  parent fan-out keeps the `state_update` beacon and its `DEFAULT_STEP_ESTIMATE_SECONDS`
  entry; shard labels get `STATUS_LABELS` entries and no estimate entry, the same shape the
  existing `steering_seed_generation` and `relationship_promotion` labels already have.
- Wall-clock p50 against phase 0's snapshot: sharding must not make a turn slower.

**Risk.** `_status_ctx` is `threading.local` and its comment states that `_timed()` never
crosses threads. Sharding crosses threads. The beacon write already self-skips on its
`if ctx is not None` guard, so the behaviour is correct by construction — but the comment
becomes false and must be rewritten, not left to mislead the next reader.

---

## Phase 8 — `check` *(on demand)*

**Goal.** Attempt resolution with declared odds.

**Do not build this speculatively.** It is the only engine with no v2 ancestor constraining
its design (§7.7), which makes it the easiest to over-build into a general RPG ruleset
nobody asked to author. Build it when a story wants it, shaped by that story.

---

## Cross-cutting: story content

Template rewrites interleave with the phases rather than following them — each port
(phases 2, 4, 5, 6) needs its stories updated in the same commit, or the suite goes red.

Three stories, three different handling requirements:

| Story | Where it lives | Note |
|---|---|---|
| `example` | This repo | The reference port. Do it first, every time. |
| `new_babel` | `stories/private/new_babel/` | **Push the submodule before bumping its pointer.** A pointer bump landing ahead of the push breaks every clone. |
| `the_missing_core` | `stories/private/the_missing_core/` | Same submodule, same rule. No longer a standalone local repo. |

Both non-`example` stories are repo-owner items, not agent items.

**Resolved 2026-09-13** — the submodule was pushed (`005e5d4..2770d67`), the gitlink
bumped, and both private templates converted to the v3 shapes. Kept below because the
failure mode it describes is the one to avoid repeating, and because the recovery order it
states is still the rule.

**Was pending.** The submodule was restructured to one folder per story and
mounted at `stories/private/` (it was `stories/new_babel/`, a submodule of the same repo
whose content sat at its root). That restructure is committed **in the submodule and not
pushed**, so this branch deliberately carries the gitlink at the old commit with an
unstaged pointer bump — the same intentional state `docs/analysis_and_plans/PACING_LOOP/PHASE_6_HANDOFF.md` §1 describes, for
the same reason. Until the owner runs `git -C stories/private push` and bumps the pointer,
a fresh clone that initialises the submodule gets the *old* layout, where no
`stories/private/<slug>/template.json` exists and the private catalog is simply empty.
Nothing crashes; the private stories just aren't there.

**Where that restructure physically is, established 2026-09-13.** It is committed in the
submodule working copy on the **homelab** (the Docker/cloudflared host — `docker-compose.yml`
bind-mounts `.:/app`, so the checkout is persistent), edited there through Claude Code on
that device. It is not on the author's Windows workstation and it is not on GitHub: a
search of that workstation for `*missing_core*` across both drives, every branch and
dangling object in both repos, the submodule object store, local session transcripts and
recycle bins turned up nothing, and `github.com/joemoney/palimpsest-stories` still has
`README.md` + `template.json` at its root on every branch.

Two consequences worth acting on:

- **Never run `git clean -xdf` in the homelab checkout.** `stories/the_missing_core/` is
  gitignored, and that is the one ordinary command that would destroy the vestigial `.git`
  holding that story's baseline commit. The current version of the story lives at
  `stories/private/the_missing_core/template.json` inside the unpushed submodule commits;
  the baseline is recoverable with
  `git -C stories/the_missing_core show <commit>:template.json`.
- **Phase 4 landed without either private template**, because the machine it was written on
  could not reach them. Both are still v2-shaped and will load *inert* rather than raising:
  `_declared()` only binds a `mechanics` entry that is a dict carrying `"engine"`, so a bare
  `revelations`/`failure_conditions` list is silently skipped and an undeclared
  `relationships` block tracks nothing. Converting them is the remaining phase 4 content
  work:

| Block | v2 shape | v3 shape |
|---|---|---|
| `revelations` | bare list | `{"engine": "triggered_reveal", "entries": [...]}` |
| `failure_conditions` | bare list | `{"engine": "triggered_ending", "conditions": [...]}` |
| `relationships` | `{axis, limit}` | add `"engine": "scored_axis"` and a **required** `registers` price list; optional `scale`, `tiers`, `cap_per_window` |
| `inventory` | (none) | `{"engine": "tagged_items"}` if the story uses items; optional `tags`, `capacity` |
| `subplots` | (none) | `{"engine": "weighted_threads"}` wherever `plot.subplots` is authored |

`mechanics.validate()` warns at load for the two most damaging omissions — seeded stats or
`starting_inventory` with no engine declared, and authored `plot.subplots` with no
`weighted_threads`. Take those warnings seriously: an undeclared subplot engine leaves the
threads visible in every prompt and never progressing, which looks like a working story
that simply never resolves anything.

**Recovery order is fixed** (the same rule as above, restated because it is easy to invert
under pressure): confirm the story survives, **push the submodule**, pull `engine-v2`, and
only then bump the gitlink. Phase 4's five commits touch neither `.gitmodules` nor the
gitlink, so a fast-forward pull works even with the pointer bump sitting unstaged.

---

## Rollback

§8.3 accepts that there is no migration: v3 templates and v2 saves are incompatible, and
`load_state` says so rather than attempting a partial upgrade. The consequence is that a
save created after phase 2 cannot go back.

**Keep a playable v2 branch until phase 5 passes.** That is the analogue of schema v2's
"keep a v1 branch playable until phase 6 passes," and the reason is the same: the point at
which the new architecture has hosted both a ported mechanic and the two control-group
mechanics is the first point at which it has actually been shown to work.

---

## Phase 0 measurements

Measured 2026-09-13 on `engine-v2`, via `python3 scripts/measure_baseline.py --markdown`.
Re-run it — do not hand-edit these numbers — and diff with `--json`.

`the_missing_core` is deliberately not tabled here: it lives in the private
`stories/private/` submodule and does not belong to this repo. The script scans both story
roots, so anyone with the submodule checked out gets its row locally.

| Metric | `example` | `new_babel` | `courtroom` | `regency` | `survival` |
|---|---|---|---|---|---|
| Observation fields (turn 0) | 11 | 14 | 8 | 8 | 9 |
| Observation fields (post-creation) | — | 15 | — | — | — |
| Observation prompt (chars) | 7,053 | 10,626 | 3,494 | 4,263 | 3,356 |
| System prompt (chars) | 5,434 | 12,514 | 3,055 | 3,396 | 3,700 |
| System prompt (~tokens, chars/4) | 1,358 | 3,128 | 763 | 849 | 925 |

Token figures are `chars / 4`. No tokenizer is installed and the offline suite has no pip
dependencies; the divisor is fixed so successive runs stay comparable to each other, and
the number that matters for §5.1 — the field count — is exact.

| `_timed` label | p50 | samples |
|---|---|---|
| `act_advancement_check` | 12.29s | 50 |
| `narration` | 14.39s | 50 |
| `options_generation` | 2.64s | 6 |
| `state_update` | 1.73s | 50 |
| `subplot_generation` | 20.47s | 11 |
| `summary_rollover` | 21.42s | 39 |

### What the numbers say

**§5.4's six-fields-per-shard budget was contradicted by the data. Resolved — the budget
is now `core + 7` = ten.** Today's real stories run 11 and 15 fields. Walking the port plan
through §7's catalogue — `items_gained`/`items_lost` merging into one inventory field,
`leverage_gained`/`leverage_spent` into one, `beat_type`/`intensity` into one,
`memory_fragments_revealed`/`revelations_eligible` cadence-gated to usually none — a fully
ported `new_babel` still lands around 9 or 10. Three of those belong to no engine at all:
`flags_set`, `scene_update` and `new_characters` are core, and porting nothing removes
them, which is why the budget is expressed as core plus engines rather than a flat total.
Six would have made sharding mandatory from phase 1 rather than the conditional phase 7
this plan assumes, for no measured benefit. **Phase 1 is unblocked and phase 7 stays
conditional.**

**The observation prompt is not the small one.** For `example` it is 7,053 chars against a
5,434-char narration prompt — 30% larger than the prompt everyone thinks of as the big one.
§5.1's "delete before you move" is aimed at the right target.

**`state_update`'s seed estimate is wrong by more than 10×.** Measured p50 is 1.73s against
`DEFAULT_STEP_ESTIMATE_SECONDS["state_update"] = 23`. It self-corrects in production —
`p50_duration` takes over from the seed after one real call — so this is not a bug, and the
gap is explained by the tier: `state_update` runs on Tier C, the fast model. Worth knowing
before phase 7 reads wall-clock numbers, since the step sharding would parallelise is
currently the cheapest one in the turn.

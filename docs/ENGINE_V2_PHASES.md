# Engine v2 / schema v3 — phased implementation plan

**Written for whoever picks this up, including a fresh agent.** `docs/ENGINE_V2_SPEC.md`
says what to build and why. This file says in what order, what each phase has to prove
before the next one starts, and what will quietly go wrong if the order is ignored.

Primary sources, in priority order when they conflict:

1. `CLAUDE.md` — engine invariants. Read first; not optional context.
2. `docs/ENGINE_V2_SPEC.md` — the design. Section references below are to it.
3. `docs/SCHEMA_V2_SPEC.md` — P-1…P-7, all still binding.
4. This file.

**Status: phases 0–3 complete. Phase 4 is in progress — step 1 of 5 landed.**

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

**Goal.** The observation pass stops being a state diff.

**Work.** In this order, each landing green before the next:
1. ~~`relationship` / `scored_axis` (§7.2) — and the scale stops being hardcoded `±100`.~~
   **Done** — `backend/mechanics/social.py`; see *Step 1* below.
2. `inventory` / `tagged_items` (§7.3) — items become records; `items_lost`'s exact-string
   match goes away.
3. `revelation` / `triggered_reveal` (§7.5).
4. `failure` / `triggered_ending` (§7.6) — effect unchanged: set `endgame.requested`, build
   `final_arc` from `ending_prompt`, route into the existing endgame machinery.
5. Subplot progress (§2.1) — the model classifies how materially a beat advanced a thread,
   the engine prices it. `check_and_advance_act` is **not** touched; only
   `check_subplot_status`'s arithmetic moves.

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

---

## Phase 5 — Relocate `pacing_loop` and `progression`

**Goal.** Move the two mechanics that are already the right shape, and change nothing about
them.

**Work.** Both behind the registry, behaviour identical. `pacing_loop` already has the model
classifying while the engine owns counters, resets, effective thresholds and arming
(§1.3); `progression` already has the model emitting labels and kinds while the engine owns
numbering, spend-matching and eviction.

**Gate.** `test_pacing_loop.py` passes **unmodified**. If hosting them requires distorting
them, the registry is wrong — same logic as phase 2, later and cheaper to act on.

**Risk.** Low, and that is the point: these are the control group.

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

**Pending as of 2026-09-13.** The submodule was restructured to one folder per story and
mounted at `stories/private/` (it was `stories/new_babel/`, a submodule of the same repo
whose content sat at its root). That restructure is committed **in the submodule and not
pushed**, so this branch deliberately carries the gitlink at the old commit with an
unstaged pointer bump — the same intentional state `PHASE_6_HANDOFF.md` §1 describes, for
the same reason. Until the owner runs `git -C stories/private push` and bumps the pointer,
a fresh clone that initialises the submodule gets the *old* layout, where no
`stories/private/<slug>/template.json` exists and the private catalog is simply empty.
Nothing crashes; the private stories just aren't there.

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

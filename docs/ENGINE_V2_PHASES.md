# Engine v2 / schema v3 — phased implementation plan

**Written for whoever picks this up, including a fresh agent.** `docs/ENGINE_V2_SPEC.md`
says what to build and why. This file says in what order, what each phase has to prove
before the next one starts, and what will quietly go wrong if the order is ignored.

Primary sources, in priority order when they conflict:

1. `CLAUDE.md` — engine invariants. Read first; not optional context.
2. `docs/ENGINE_V2_SPEC.md` — the design. Section references below are to it.
3. `docs/SCHEMA_V2_SPEC.md` — P-1…P-7, all still binding.
4. This file.

**Status: phase 0 complete** (see *Phase 0 measurements* below). Phase 1 is the next action.

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

**Gate.** Full suite green, **and** assembled prompts byte-identical for all three stories
and all three fixtures. Phase 1 is defined by changing nothing observable; a prompt diff
here means something leaked.

**Risk.** Low, and the one real one is scope creep — the temptation to port `stats` "while
you're in there." Don't. Phase 1's value is that it isolates registry bugs from porting bugs.

---

## Phase 2 — Port `resource`, and cut over to schema v3

**Goal.** Prove the registry can host a real mechanic. **This is the stop-gate.**

**Work.**
- `resource` / `bounded_counter` (§7.1): axes, `floor`/`ceiling`, event-priced deltas,
  `per_turn` drift, `visible`, and `readout` — which becomes this engine's `render()` and
  needs no other home.
- `apply_stat_readouts` moves behind `render()`. Its guarantee does not change: the model
  emits a token, the engine substitutes, and it still rewrites a figure line the model wrote
  by hand anyway (P-7 is not softened by relocation).
- Bump `CURRENT_SCHEMA_VERSION` to 3. `load_state` refuses a v2 save plainly (§8.3), no
  partial upgrade.
- Rewrite `stories/example`'s `mechanics.stats` into `mechanics.resource`.

**Gate.**
- `test_stat_bounds.py`, `test_stat_readout.py`, `test_stat_visibility.py` pass **without
  being modified**. If a test has to change to accommodate the port, the port changed
  behaviour, and phase 2's whole point is that it must not.
- Prompt diff for `example` is limited to intended changes and each one is named.
- Observation field count for `example` is **lower** than phase 0's, not higher —
  `stat_changes` leaves and nothing replaces it.

**Risk.** This is the phase that can end the project, on purpose. `resource` is the
best-covered and least-surprising mechanic in the system, with the `render()` slot already
built and a documented real-world failure behind it. If the registry cannot host it
cleanly, the registry is wrong — stop and fix the design rather than porting a second engine
onto a bad seam.

---

## Phase 3 — Conformance fixtures

**Goal.** Prove the registry is generic **before** anything else is ported to it.

**Work.**
- Rewrite `regency.json`, `courtroom.json`, `survival.json` to select three *disjoint*
  engine sets.
- Update `test_genre_conformance.py`'s marker table and per-fixture absent-lists.

**Gate.** Everything that made the v2 fixtures load-bearing survives verbatim:
- Assertions run **in both directions** — an absent engine leaks no marker into either
  prompt, *and* an authored engine actually reaches them. One-directional absence testing
  passes happily for an engine that was never wired up at all.
- The absent-engine lists stay **written out in the test**, not derived from the fixture
  files, so deleting an engine from a fixture fails loudly instead of silently shrinking
  coverage.
- New: an unregistered engine name fails at load; `prompt_sections` output is inside each
  engine's declared budget (§5.4).

**Risk.** Ordering. Doing this after phase 4 means porting four engines against a genericity
claim nothing has tested. The fixtures are cheap here and expensive later.

---

## Phase 4 — Port the remaining state-carrying engines

**Goal.** The observation pass stops being a state diff.

**Work.** In this order, each landing green before the next:
1. `relationship` / `scored_axis` (§7.2) — and the scale stops being hardcoded `±100`.
2. `inventory` / `tagged_items` (§7.3) — items become records; `items_lost`'s exact-string
   match goes away.
3. `revelation` / `triggered_reveal` (§7.5).
4. `failure` / `triggered_ending` (§7.6) — effect unchanged: set `endgame.requested`, build
   `final_arc` from `ending_prompt`, route into the existing endgame machinery.
5. Subplot progress (§2.1) — the model classifies how materially a beat advanced a thread,
   the engine prices it. `check_and_advance_act` is **not** touched; only
   `check_subplot_status`'s arithmetic moves.

**Gate.** Per-engine tests plus absent-engine tests for each. Field count measured against
phase 0 after each port, and any increase carries a stated reason in the commit message.
Re-measure prompt size here — it is the input to phase 7's go/no-go.

**Also settle §12.5 here.** Once the observation pass is pure classification, run it on
Tier C and on Tier AB over the same held-out turns and compare misclassification rate
against the latency and cost delta. §5.5 has the argument in both directions and says
plainly that it cannot be settled on paper; this is the phase where the ported pass first
exists to measure. Tier C stands until the numbers say otherwise.

**Risk.** The highest-volume phase and the one where P-2 regressions hide. An engine that is
absent must leak nothing; the phase 3 fixtures are what catch it, which is why they come
first.

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

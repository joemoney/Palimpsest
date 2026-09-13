# Engine v2 / schema v3 — phased implementation plan

**Written for whoever picks this up, including a fresh agent.** `docs/ENGINE_V2_SPEC.md`
says what to build and why. This file says in what order, what each phase has to prove
before the next one starts, and what will quietly go wrong if the order is ignored.

Primary sources, in priority order when they conflict:

1. `CLAUDE.md` — engine invariants. Read first; not optional context.
2. `docs/ENGINE_V2_SPEC.md` — the design. Section references below are to it.
3. `docs/SCHEMA_V2_SPEC.md` — P-1…P-7, all still binding.
4. This file.

**Status: nothing started.** Phase 0 is the next action.

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

**Work.**
- Count observation fields emitted by `update_progress_from_turn` for each of the three
  stories and the three fixtures. This is the number §5.1 promises to shrink.
- Record assembled `build_system_prompt` size per story, in characters and tokens.
- Snapshot `data/perf_stats.json` p50s per `_timed` label, so phase 7 can tell whether
  sharding actually bought anything.
- Write the numbers into this file as a table. They are the phase 0 deliverable.

**Gate.** The numbers exist and are committed.

**Risk.** None. This is the cheapest phase and the only one that makes the others
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
sequential shards would multiply the budget that parallel shards leave alone.

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
| `new_babel` | Private submodule (`palimpsest-stories`) | **Push the submodule before bumping its pointer.** A pointer bump landing ahead of the push breaks every clone. |
| `the_missing_core` | Gitignored, deliberately | Stays out of this repo's history. Needs its own private repo before any push that would carry it. |

Both non-`example` stories are repo-owner items, not agent items.

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

*(To be filled in by phase 0. Table intentionally empty until then — an estimate written
here would be indistinguishable from a measurement three months from now.)*

| Metric | `example` | `new_babel` | `regency` | `courtroom` | `survival` |
|---|---|---|---|---|---|
| Observation fields | | | | | |
| System prompt (chars) | | | | | |
| System prompt (tokens) | | | | | |

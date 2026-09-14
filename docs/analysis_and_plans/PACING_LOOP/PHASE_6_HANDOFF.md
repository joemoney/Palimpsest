# Phase 6 handoff — pacing loop implementation

**Written for a fresh agent picking this up.** Phase 0 is closed; steps 1 and 2 of the
implementation order are done. This document is the context you cannot get from the code:
what was decided, what is deliberately *not* what the spec says, and what will silently
break if you follow the spec literally.

Primary sources, in priority order when they conflict:

1. `CLAUDE.md` — engine invariants. Read it first; it is not optional context.
2. `docs/Narrative_Pacing_Loop_Spec_v4.md` — the feature spec. **Partly superseded, see §2.**
3. `docs/PHASE_0_GATE_REPORT.md` §7–§10 — what was measured and what it means.
4. This file.

---

## 1. Where things stand

| Step (spec §16) | Status |
|---|---|
| §0.1 / §0.2 / §0.3 validation gates | **Closed.** 0.3 **failed**; see §2 below |
| 1. Author New Babel's module | **Done** — private submodule commit `5f3a6cb`, *pointer not bumped* |
| 2. Author `example`'s module | **Done** — `32f5afd` on `phase-6.1-pacing-modules` |
| 3. Extend state-update schema (`beat_type`, `intensity`, leverage) | **Done** — see `backend/story_engine.py`'s `update_progress_from_turn` and `test/test_pacing_loop.py` |
| 4. Counter + ledger update logic | **Done**, including leverage spend/eviction — see below |
| 5. Eligibility, deferral ceiling, `SECTIONS` directive builder | **Done** — `_section_pacing_directive` in `backend/story_engine.py`, tests in `test/test_pacing_loop.py` |
| 6. Playtest thresholds | not started — **see §6, this is the real acceptance test** |

### The two gaps steps 4-5 left open are now closed

Both were closed before step 6's playtest, on the reasoning that each changes what the
directive actually says and so would invalidate a playtest run without them.

- **Leverage spending and eviction** (spec §7). `update_progress_from_turn` now carries a
  `leverage_spent` field alongside `leverage_gained`, matched **by exact label string**
  against an entry that is still unspent - mirroring `items_lost` against `CURRENT
  INVENTORY`, which is why the `CURRENT <LABEL>` prompt line now says it is the thing to
  copy a label from. A spent entry is **marked, never removed** (§7's retention decision):
  it stays for callbacks and for the record, but drops straight out of `{unspent_leverage}`,
  so the release directive can no longer point the narrator at something the story already
  cashed in. `LEVERAGE_LIMIT` (40) plus `_evict_spent_leverage` bound the ledger, evicting
  **spent entries oldest-first and never an unspent one** - a ledger of only unspent entries
  is allowed to overflow rather than lose a live asset.
- **`pacing.reveal_queue` is now populated** (spec §12). Its hard prerequisite, CR-03, has
  landed (`_section_revelations` puts revealed content into the narration prompt), so a
  placed reveal now reaches a pipe that is actually connected. `update_progress_from_turn`
  gained a `revelations_eligible` field - gated on the story having *both* a `pacing_loop`
  and unrevealed fragments - that separates "trigger satisfied **and** written onto the
  page this turn" (`memory_fragments_revealed`, unchanged) from "satisfied but **not** yet
  written" (queued). The prompt says explicitly that an id goes in one or the other and
  never both; without that sentence the model reads the two fields as synonyms.

**One deliberate deviation from spec §12, worth knowing before you read the code.** §12 says
the directive "consumes one entry per firing, FIFO". It doesn't - consumption is on
*confirmed* reveal instead: an entry leaves the queue only once `memory_fragments_revealed`
reports the fragment actually landed. A firing is an instruction to the narrator, not a
guarantee; popping unconditionally would silently drop a reveal any time the model ignored
the bullet, and nothing would ever re-queue it, since its trigger already fired once in a
scene now well behind us. The worst case under the implemented behaviour is the next firing
citing the same reveal again, which is self-correcting rather than lossy.

**New Babel's directive was edited** as part of this. Its reveal bullet previously read "If
the reveal queue is non-empty, surface exactly ONE reveal" - an instruction about a queue the
narrator could not see, since nothing interpolated `{queued_reveal}`. It now interpolates the
queued reveal's **content** (never its id or authored trigger) and instructs the model to
write it only when that content isn't `"none queued"`. This is directive text, not a beat
definition, so it does not invalidate `PHASE_0_GATE_REPORT.md` §9.13.1 or require re-running
the gate scripts. `reduced_directive` was deliberately left alone - a constrained
mid-action breath is not the scene to land a revelation in.

**Closed:** `example`'s directive carried the same "if the reveal queue is non-empty" bullet,
but `example` authors no `mechanics.revelations` at all, so its queue could never be
non-empty and the bullet was unsatisfiable text in the prompt - harmless, but noise, and
some risk of a narrator reading it as licence to invent a reveal. Removed from `directive`
in `stories/example/template.json` (the bullet was never in `reduced_directive`). Content-
only change, no engine code touched.

Tests for all of the above are in `test/test_pacing_loop.py` (suite: 35 files, all passing).

Branch: `phase-6.1-pacing-modules`, off `master` (`508e96e`). Working tree carries one
intentional modification: ` M stories/new_babel`, the un-bumped submodule pointer.

### Two open items needing the repo owner, not you

- **The submodule must be pushed before its pointer is bumped.** `stories/new_babel` is a
  private repo (`palimpsest-stories`). Its pacing module is committed locally only. A pointer
  bump landing on a branch before that push breaks every clone. Don't bump it yourself.
- **`data/vocab_example_2beat_v3_1.json` is now a duplicate.** The template
  (`stories/example/template.json` → `mechanics.pacing_loop.beats`) is the source of truth
  from here. Treat the `data/` copy as a test fixture for the gate scripts only, and **read
  beats from the template** in engine code.

---

## 2. Where the spec is wrong, and why

The spec was written before the validation gates ran. Do not implement these parts literally.

| Spec says | Reality | Source |
|---|---|---|
| §5.1 / Appendix A: **four beats** per story | **Two.** Four-beat agreement measured 41.7% | report §7 |
| §6.2: `force_release` `threshold: 8` | **Never fires.** Authored at **5** in both templates | report §10, template `_threshold_note` |
| Appendix A: `force_complication` `threshold: 6`; playtest 6/8/10 | 6 fires 3× in 51 turns; 8 and 10 never. Authored **5**, playtest range **4–6** | template `_threshold_note` |
| §16 step 1: author New Babel first as the safe case | **No genre gap exists.** Both stories sit at κ ≈ 0.4 | report §10 |
| §0.2/§0.3: pass at ≥70% raw agreement | Raw agreement can't tell skill from class imbalance. **Use Cohen's κ**, and never quote a single run | report §9.2, §9.12.2 |

**The gate failed and the feature is proceeding anyway.** That is a deliberate, recorded
decision (report §9.13.1). The beat classifier disagrees with a careful human reader about a
third of the time, and ~1 in 5 of its calls are not reproducible run-to-run. The bet is that a
*counter* integrates over many turns and only the threshold crossing is observable, so
per-scene noise may not matter. **Step 6 tests that bet.** If you find yourself trying to fix
the classifier's accuracy, stop — that was tried across three vocabulary revisions and the
residual is wording-proof (report §9.13.1, turn 50).

**One number to keep in mind:** the classifier scores intensity **~1.31× higher** than the
human rater. Any threshold tuned against hand labels fires early in production.

---

## 3. Step 3 — the state-update pass (start here)

Add `beat_type` and `intensity` to `update_progress_from_turn`'s JSON schema, **conditional on
the story having the module**, exactly mirroring how `stat_changes` is handled.

- Schema construction: `backend/story_engine.py:855` — the `if stats:` block appending to
  `schema_fields` is the pattern to copy. A story without `mechanics.pacing_loop` must emit
  no beat fields at all, and everything must still work (the `example` template had no module
  until yesterday; every save older than that has no pacing counters).
- Diff application: `backend/story_engine.py:1061` — the `stat_changes` loop is the
  neighbourhood where counter updates belong. **State mutations live in `story_engine.py`,
  never `state_store.py`,** which is pure storage (`CLAUDE.md`).
- Interpolate the beat names and definitions **from the template**, not from a constant. Two
  stories already use different beat names (`disquiet`/`comfort` vs `threat`/`respite`) — a
  hardcoded name is an instant bug.

### Traps specific to this step

- **No new LLM call.** `beat_type` and `intensity` ride along in the existing
  `update_progress_from_turn` request (spec §6.1: "one extra field, no extra request"). If you
  add a call to the turn path anyway, you must also add it to `STATUS_LABELS`
  (`story_engine.py:401`) **and** `DEFAULT_STEP_ESTIMATE_SECONDS` (`:433`) — `test/
  test_status_labels.py` asserts the mirror both ways and will fail you.
- **Existing saves have no `pacing.counters`.** Live saves carry `turn_count`,
  `turns_since_nudge`, `turns_since_act_check`, `subplots_completed_this_act`,
  `last_direction` and nothing else. Initialise lazily with `.get(...)` defaults; do **not**
  write a migration. The project has exactly one migration ever (`_migrate_relationships`)
  and `CLAUDE.md` is explicit that there is no general schema-version mechanism.
- **`pacing` is top-level state**, not under `plot` (spec §8). It already is — don't move it.
- Spec §8 puts the ledger at `protagonist.leverage`. Live saves' `protagonist` currently has
  `name, traits, inventory, stats, creation_choices, flags`. Same lazy-init rule.

---

## 4. Steps 4–5 — counters and the directive

- **Counter arithmetic** (spec §9 step 4): beat's `feeds` counter `+= intensity`; every
  counter in `resets` → 0, and clear that rule's `armed` entry and deferral count. Then per
  rule, if `counters[watch] >= effective_threshold` (§13 resolution order: exact act number →
  `"finale"` if the act has `is_finale` → base `threshold`; `null` disables), add to `armed`.
- **`armed` is a dict keyed by rule id**, not a boolean. Presence means armed.
- **v1 implements exactly one rule per story.** Both templates have one. If a template
  declares two, log a warning and use the first (spec §6.2). Don't build arbitration.
- **The directive is a `SECTIONS` builder** (`story_engine.py:1920`) — add one entry, placed
  with the volatile sections near the pacing nudge, **never in the cacheable prefix**. It is a
  single-turn addition, dropped afterward.
- Directive text is **authored in the template**, not in the engine. Interpolation vocabulary
  is fixed: `{counter_value}`, `{deferrals}`, `{unspent_leverage}`, `{queued_reveal}`
  (spec §11).
- `suppress_when` predicates: `example` uses `["just_fired"]`; New Babel adds
  `"threat_present"`, which reads `scene.threat_present`. Predicates are opt-in per rule —
  a missing predicate must be a no-op, not a crash.

---

## 5. Testing conventions (non-negotiable)

`test/` is offline-first: `test/_llm_stubs.py` stubs `dotenv`, `google.generativeai`,
`filelock` and `werkzeug.security` so the suite runs with no pip installs and no network.
Run everything with `python3 test/run_all.py` — **34 files, all must pass.**

- New engine functions that call the LLM must be monkeypatchable around `call_llm` /
  `call_llm_json`. Follow the existing pattern.
- Add a `test/test_pacing_loop.py`. Cover at minimum: a story **without** the module is
  unaffected (no schema fields, no counters, no directive); counters accumulate intensity and
  reset on the opposite beat; `threshold_by_act` resolution including `"finale": null`;
  deferral increments and the reduced directive at the ceiling; and an **old save with no
  `pacing.counters` key** taking a turn without raising.
- `test/test_app_routes.py` needs `wait_for_idle` before asserting on save state — turns run
  on a background thread and the kickoff POST returns 202 immediately.

---

## 6. Step 6 — the acceptance test, and the point of the whole exercise

**The remaining question is not measurable by agreement statistics**, and three rounds of
labelling established that the hard way. It is: *does a corrected lull read as a lull?*

Spec §14 has the acceptance samples. The repo owner's framing, which supersedes any metric
here: **play it, and see whether the reader feels the lull.**

Suggested instrumentation, not yet built and worth proposing before building: log every
directive firing (rule id, counter value, turn, deferrals), then have the reader rate the
scene it produced. `backend/label_sheet.py` plus the inline row under the play page's choices
already implement exactly this shape for beat labelling — point the same mechanic at a
different question rather than writing a parallel one. Note that feature is gated on
`LABEL_SHEETS_USER` and is off by default.

The failure mode to watch for is **not** "the directive fired at the wrong time" — it is the
directive firing at a sensible time and producing a scene that doesn't actually feel like
relief.

---

## 7. What to escalate rather than decide

This project's history is a sequence of plausible-looking measurements that were wrong in ways
only visible on a second look. Two headline κ figures were quoted as results when they were
the best of several draws; a "passing" gate turned out to be a lucky draw; three vocabularies
each plugged one definitional hole and left the next. Escalate to the repo owner (or a
stronger model) rather than deciding alone:

- Any change to beat **definitions** in a template. They are load-bearing measurement
  artifacts; edits invalidate report §9.13.1 and require re-running `scripts/gate_02.py` and
  `scripts/classifier_retest.py`.
- Any conclusion of the form "this now passes." Check the run count first (§9.12.2: never a
  single run; report mean and range), and check the marginals for majority-class riding.
- Threshold changes. They are simulated, not guessed — `_threshold_note` in each template
  records the method, and any replacement should be derived the same way.
- Anything that would change a verdict already recorded in `PHASE_0_GATE_REPORT.md`. Corrections
  there are appended and marked superseded, never rewritten — that is a deliberate convention.

Routine implementation work — schema plumbing, counter arithmetic, section builders, tests,
fixing what the suite reports — does not need escalation. Do that directly.

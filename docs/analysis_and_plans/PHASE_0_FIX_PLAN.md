# Phase 0 fix plan — prerequisites for Phase 6

**Source:** `docs/PHASE_0_GATE_REPORT.md`
**Purpose:** clear the two blocking defects, one spec inconsistency, and one prompt-tuning
change that gate 0.2 and Phase 6.2/6.3 depend on. Nothing here implements the pacing loop.

Tasks 1–3 are defects with deterministic fixes and offline tests. Task 4 is tuning that
only a playthrough can validate — it is marked as such and must not be judged by the test
run.

## Rules for whoever executes this

1. Do the tasks **in order**. Each is independent; do not batch them into one commit.
2. After each task run `python3 test/run_all.py`. All 31 files must pass. `python` is not
   on PATH on this host — use `python3`.
3. Do **not** touch anything in the out-of-scope list at the bottom. If a task seems to
   require it, stop and report instead.
4. Do not reformat, re-order, or "tidy" surrounding code. Diffs should be minimal.
5. Per `CLAUDE.md`: default to no comments. The two comments specified below are
   prescribed because they record non-obvious *why* — write them as given, add no others.
6. Line numbers are from commit `396af0f` and are anchors, not addresses. Match on the
   quoted text, not the number.

---

## Task 1 — Instruct the state-update pass to evaluate revelation triggers

**File:** `backend/story_engine.py` (`update_progress_from_turn`)
**Why:** 0 of 2 revelations fired across 24 turns despite the narration plainly
satisfying `frag_0001`. Act 1 cannot complete without one. The triggers are interpolated
into the prompt with no instruction to act on them, and the schema line is bare — the only
field of its kind in that prompt that states no evaluation. Gate report §4.

### 1a — Enrich the schema line

Find, in the `schema_fields` list (~line 792):

```python
        '  "memory_fragments_revealed": ["<fragment_id>", "..."]',
```

Replace with:

```python
        '  "memory_fragments_revealed": ["<the exact id of every UNREVEALED MEMORY FRAGMENT '
        'TRIGGER below that the narration satisfies this turn, or [] if none>"]',
```

This mirrors the wording already used by `failure_triggered`, which carries the same
authored-id-plus-trigger shape.

### 1b — Add the evaluation instruction

Find the conditional-instruction block that builds `exact_name_instruction` and
`generic_label_instruction` (~lines 842-862). **After** that block and **before** the
`failure_line` assignment (~line 864), add:

```python
    # A trigger is authored as a description of an event ("the first time the protagonist
    # attempts a non-trivial computational proof"), but narration never echoes that wording -
    # it renders the event. Without this, the model treats the trigger list as context rather
    # than as something to evaluate, and fires nothing: 0 of 2 across a 24-turn playthrough
    # whose turns 18 and 23 both plainly satisfied one (docs/PHASE_0_GATE_REPORT.md §4).
    fragment_instruction = ""
    if unrevealed_fragments:
        fragment_instruction = (
            "For memory_fragments_revealed, check the NARRATION against each UNREVEALED MEMORY "
            "FRAGMENT TRIGGER and list the id of every one the narration satisfies this turn. "
            "Judge a trigger by what actually happens in the scene, not by whether the narration "
            "reuses the trigger's wording - a trigger describing an act is satisfied by the "
            "protagonist performing that act however it is written. Return [] if none apply; "
            "never force a match.\n"
        )
```

### 1c — Interpolate it

Find, in the prompt f-string (~lines 884-887):

```
protagonist's location and situation are unchanged from CURRENT SCENE above.
{exact_name_instruction}Only add an entry to new_characters when a character is given an actual proper name for the
```

Replace the second line's opening so it reads:

```
protagonist's location and situation are unchanged from CURRENT SCENE above.
{fragment_instruction}{exact_name_instruction}Only add an entry to new_characters when a character is given an actual proper name for the
```

Nothing else in the f-string changes.

### 1d — Test

Add to `test/test_revealed_memories.py`, following the existing `spy`-on-prompt pattern
already in that file (~line 66):

- With unrevealed fragments present, the state-update prompt contains the substring
  `"never force a match"`, and the `memory_fragments_revealed` schema line contains
  `"that the narration satisfies this turn"`.
- With **all** fragments already revealed (`revelations_revealed` covering every id),
  the prompt contains neither substring — `fragment_instruction` stays empty.
- A story with no `mechanics.revelations` at all does not raise and emits no instruction.

Print an `OK:` line per assertion, matching the file's existing style.

**Do not** change tier, model, or `call_llm_json` call site. Moving this call to Tier B is
a measurement outcome for gate 0.2, not a fix — see out-of-scope.

---

## Task 2 — Enforce `SUMMARY_MAX_WORDS`

**File:** `backend/story_engine.py`
**Why:** `compressed_summary` was 2,912 words against a 2,000 cap after only 24 turns —
46% over. The cap exists solely as prompt text and is never verified, so
`CLAUDE.md`'s claim that the field is "capped at `SUMMARY_MAX_WORDS`" is currently false.
It is re-fed into its own next rollover, so overshoot compounds. Gate report §1.

### 2a — Add the helper

Add at module scope, immediately **above** `def update_state_after_turn` (the function
containing the rollover block, ~line 1980):

```python
def _enforce_word_cap(text: str, max_words: int) -> str:
    """SUMMARY_MAX_WORDS is an instruction the model overshoots - 2,912 words against a
    2,000 cap after 24 turns (docs/PHASE_0_GATE_REPORT.md §1). Truncating here is what makes
    the documented bound real rather than aspirational. The trim back to a sentence boundary
    matters because this text is fed verbatim into every later prompt and into the next
    rollover's CURRENT SUMMARY, where a mid-clause cut would compound."""
    words = text.split()
    if len(words) <= max_words:
        return text
    truncated = " ".join(words[:max_words])
    cut = max(truncated.rfind(". "), truncated.rfind("! "), truncated.rfind("? "))
    return truncated[:cut + 1] if cut > 0 else truncated
```

### 2b — Apply it

Find, at the end of the rollover block:

```python
        history["compressed_summary"] = updated_summary.strip()
```

Replace with:

```python
        history["compressed_summary"] = _enforce_word_cap(
            updated_summary.strip(), SUMMARY_MAX_WORDS
        )
```

### 2c — Test

Add to `test/test_context_bounding.py` (which already owns the bounding invariants):

- `_enforce_word_cap` returns input unchanged when under the cap.
- An over-cap input comes back at or under `max_words`.
- An over-cap input ending mid-sentence is trimmed back to end on `.`, `!` or `?`.
- A single over-cap run with no sentence punctuation at all still returns at or under the
  cap (exercises the `cut > 0` fallback).
- End-to-end: stub the rollover `call_llm` to return an over-cap summary, force a
  `recent_turns` overflow, and assert the saved `compressed_summary` is within the cap.

**Do not** add a retry, a second LLM call, or a warning log. Truncation only.

---

## Task 3 — Fix the `stasis` reset asymmetry in the pacing spec

**File:** `docs/Narrative_Pacing_Loop_Spec_v4.md` (§5.1 only)
**Why:** New Babel's `crisis` and `escalation` declare no `resets`, so `stasis` only ever
grows — it would cross any threshold once, fire, and sit permanently suppressed by
`just_fired`. `example`'s Appendix A vocabulary resets `stasis` on both its tension-feeding
beats. §5.2 calls the two "structurally parallel"; they are not. Gate report §3.2.

This is a **documentation-only** change. It corrects the spec before Phase 6.1 authors
templates from it. Write no Python and touch no `template.json`.

In §5.1's `beats` block, add a `resets` key to both tension-feeding beats so they match
`example`'s structure:

```json
  "crisis": {
    "definition": "...unchanged...",
    "feeds": "tension",
    "resets": ["stasis"]
  },
  "escalation": {
    "definition": "...unchanged...",
    "feeds": "tension",
    "resets": ["stasis"]
  },
```

Leave every `definition` string byte-for-byte unchanged — they go verbatim into the
classifier prompt and gate 0.2 will measure against them.

Then add, immediately below that JSON block:

> Both correction directions are symmetric by construction: each pair of beats resets the
> counter the other pair feeds. A vocabulary where one counter is never reset is monotonic
> and will fire its rule exactly once before `just_fired` suppresses it permanently.

**Do not** change §6.2's rule config, thresholds, or `suppress_when`. Whether New Babel's
single v1 rule should watch `stasis` instead of `tension` is an open question deferred to
the 50-turn playthrough (gate report §6) — it is a swap, not an addition, because §6.2
constrains v1 to exactly one rule per story.

---

## Task 4 — Require option sets to diverge in kind

**File:** `backend/story_engine.py` (`_options_block_instruction`)
**Status:** **prompt tuning, not a deterministic fix.** Tasks 1–3 are defects with
verifiable fixes. This one can only be validated by playing. Land it, but do not treat a
green test run as evidence it worked.

**Why:** during the eight-turn release run (gate report §2, turns 10–17) the option sets
themselves were non-divergent, so the player had no exit from the lull to choose:

- Turn 17 — "Step inside and announce yourself" / "Stay in the doorway and ask to hear
  their first question" / "Step inside but scan the room". Options 1 and 3 are the same
  act with different posture; option 2 stalls for exposition.
- Turn 15 — "Ask why the Wolves don't run skiffs" / "demand to know where she's leading
  you" / "keep moving and memorize the route". All three investigate rather than commit.
- Turn 12 — all three conversational; no physical or spatial option offered at all.

**The critical context: this is already prohibited and the prohibition is being ignored.**
The existing instruction says *"never an option that just asks for more detail,
investigates further before committing to anything, or otherwise stalls for more exposition"*
— live since `58bcf95`, active for all 24 turns. Do **not** respond by adding more
prohibition text. The change below converts a negative constraint into a positive
structural requirement, which is the actual hypothesis being tested.

### 4a — Rewrite the instruction

Find, in `_options_block_instruction` (~lines 1784-1793):

```python
        "under 15 words, distinct, and plausible. Each option must be a meaningfully different "
        "course of action with real consequences for the story - never an option that just "
        "asks for more detail, investigates further before committing to anything, or "
        "otherwise stalls for more exposition instead of moving the scene forward. No extra "
        "commentary after the list."
```

Replace with:

```python
        "under 15 words, distinct, and plausible.\n"
        f"The {option_count} options must diverge in kind, not in degree. Give each one a "
        "different mode: speaking or pressing someone; acting physically on the world; going "
        "somewhere or leaving; committing to a risk. Two options that differ only in tone, "
        "posture, or wording while leading to the same next scene count as one option, not "
        "two - replace one of them. At least one option must change the protagonist's "
        "physical situation rather than continue the current exchange. Asking a question is "
        "legitimate when the answer would genuinely change what the protagonist does next, "
        "but never more than one such option, and never all of them. No extra commentary "
        "after the list."
```

The format specification in the preceding lines is load-bearing for
`parse_narration_and_options` — leave it exactly as it is. Only the text quoted above
changes.

### 4b — Test

Add to `test/test_narration_module.py` (which already owns narration-prompt assertions):

- The narration prompt contains `"must diverge in kind, not in degree"`.
- `generate_missing_options`' follow-up prompt contains the same substring — the two call
  sites share `_options_block_instruction` and must not drift, which is the documented
  reason that helper exists.

That is the full extent of what is testable offline. It proves the prompt says the right
thing, not that the model complies.

### 4c — Manual validation

This is the real test, and it happens during the 50-turn playthrough. Afterwards, export
and check the option sets in any conversation-heavy stretch:

```bash
python3 backend/export_story.py --user <user> --story new_babel --include-actions --output /tmp/after.txt
grep -A4 "^OPTIONS:" /tmp/after.txt
```

Compare against the preserved 24-turn "before" export. Look for: no option set where all
options are conversational, and no set where two options lead to the same next scene.

If the sets are still homogeneous, **do not iterate on this prompt a third time.** Two
static formulations will have failed, which is itself the finding: report it, and let the
Phase 6 directive — injected fresh, for a single turn, at high salience — own the problem
instead of a permanent footer rule.

### 4d — Sequencing note

Land this **before** the 50-turn playthrough. It costs clean comparability against the
24-turn baseline, which is accepted deliberately: the 24-turn export is preserved as the
"before" artifact, and the 50-turn run is more valuable measuring the engine as it would
actually ship. It also feeds the deferred decision in gate report §6 — if divergence alone
reduces the stasis clustering, New Babel may not need its rule direction swapped at all.

---

## Verification

```bash
cd /home/joe/git/cyoa-app
python3 test/run_all.py          # all 31 files must pass
git diff --stat
```

Expected footprint: `backend/story_engine.py`, `test/test_revealed_memories.py`,
`test/test_context_bounding.py`, `test/test_narration_module.py`,
`docs/Narrative_Pacing_Loop_Spec_v4.md`. Nothing else.

Commit as four commits, one per task. Do not push.

### Manual check after Task 1

Tasks 1 and 2 cannot be fully validated offline — the stubs prove the prompt says the
right thing, not that the model then acts on it. During the 50-turn playthrough, confirm
`plot.revelations_revealed` becomes non-empty:

```bash
python3 -c "import json; d=json.load(open('data/saves/<user>/new_babel.json')); print(d['plot']['revelations_revealed'])"
```

Still empty after a turn that clearly satisfies a trigger means the prompt fix was
insufficient and the call needs Tier B — that is gate 0.2's decision to make, with
measurements, not a follow-up patch to improvise here.

---

## Out of scope — do not do these

- **Do not** move `update_progress_from_turn` to Tier B, or add a second LLM call for
  revelations. It runs every turn; the cost is real. Gate 0.2 decides this with data.
- **Do not** implement any part of Phase 6: no `beat_type`, no `intensity`, no
  `leverage_gained`, no `counters`, no `rules`, no directive injection.
- **Do not** add `mechanics.pacing_loop` or `mechanics.progression` to any template. That
  is Phase 6.1 and it is gated on 0.2 passing.
- **Do not** change New Babel's rule direction (`force_release` → `force_complication`).
  Deferred until after Task 4 has been measured over the 50-turn playthrough — divergent
  options may break up the clustering on their own, in which case no swap is needed. Gate
  report §6.
- **Do not** update `CLAUDE.md`. It is stale with respect to schema v2, `SECTIONS`, and
  mechanics modules, and that is deliberately scheduled as Phase 7.4.
- **Do not** investigate `subplot_002` sitting at 0 progress, the act-check cadence, or
  `entity_contact_count: 0`. The first is expected to resolve once acts can advance; the
  other two are working as designed (gate report §1, §7).
- **Do not** delete or modify `new_babel.json.bak-turn18-salvage`.
- **Do not** author `regency.json` or `the_attention_economy.txt`. Neither is needed: the
  first is an export of `new_babel` (regenerate with `backend/export_story.py`), and gate
  0.3's cross-genre case is already `stories/example` with its Appendix A vocabulary. Gate
  report §0.

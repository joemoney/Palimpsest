# Authoring tropes in a template — feasibility and tone impact

**Status: parked 2026-09-17, not started.** Raised while reworking `the_missing_core` for
QUORUM/quests. Revisit before adding any new authored guidance block to a story template.

## The question

If a story authors a list of tropes it wants to lean into, how does that interact with the
tone the template already sets?

## What was measured

On a **fresh** `the_missing_core` save (turn 0, no history):

| section | chars | share of prompt |
|---|---|---|
| `world.rules` | 8,220 | 35.7% |
| `narration.style` | 3,465 | 15.1% |
| everything else | 11,324 | 49.2% |
| **total** | **23,009** | |

So authored guidance is **~51% of the prompt** at the moment tone is being established.
By turn ~100 that inverts: `world.rules` falls to ~10%, `style` to ~4%, and `recent`
(the story's own accumulated prose) rises to ~49%.

Also measured: **90% of `world.rules` entries and 92% of `narration.style` entries contain
an explicit prohibition** (`never`, `not`, `forbidden`, `do not`, `cannot`).

## Three findings

**1. Trope influence would be front-loaded and then decay.** Strong for the first ~10 turns,
progressively weaker after. The decay mechanism is the same one behind every drift bug found
on 2026-09-15..17: accumulated narration is a *stronger* tone signal than any authored rule,
because it is worked examples rather than abstract instruction. Nine `[ SYSTEM ]` blocks in
the wrong register beat one correct rule sitting 54k chars upstream. A trope list is subject
to exactly that.

**2. This template's tone is built out of refusals.** Several rules exist specifically to
resist genre convention:

- `world.rules[10]` — "The operator is a salvager, not a soldier. They lose fights they
  should lose."
- `world.rules[14]` — "THE OLD SCALE IS BACKGROUND, NEVER THE SUBJECT… never let the prose
  inflate toward space opera."
- `world.rules[16]` — "PHYSICAL COMEDY IS CONFINED TO THE WORK", forbidden in four places.
- Plus "Do not explain the Consonance" and "nobody ever will in full".

A flat trope list is positive genre-convention pull aimed at rules whose entire job is
resisting it. The model resolves that conflict per-scene and unpredictably, so the likely
failure is intermittent tonal wobble — one scene reaching for the trope and inflating, the
next declining it — rather than a clean shift.

**3. A separate `tropes` block would repeat a known mistake.** Tone already lives in three
places: `meta.tone`, `narration.style`, `world.rules`. The readout guidance was previously
spread across six entries in two sections and lost every time; consolidating it was the fix
(see `the_missing_core` `world.rules` merge, 2026-09-16). A fourth tone location is the same
dilution under a new name.

## Recommendation if revisited

- Author tropes as **paired** entries — the convention *and* how this story bends it.
  `"The mentor figure: present, but it is the ship, it is not on your side, and it will not
  explain itself"` carries tone. A bare `"mentor figure"` invites the generic version.
- Put them **inside `narration.style`**, not a new block.
- If a separate block is wanted anyway, the engineering is trivial (one section function, a
  `prompt_sections` entry) but it needs a conformance marker and a fixture that omits it, or
  nothing guards P-2 for it — see `CLAUDE.md` § Testing.
- Watch prompt size: `the_missing_core` already carries the largest authored surface in the
  repo (21k static chars vs `new_babel`'s 12.8k).

## Related

The same measurement explains why the story read "too serious" in playthrough 02: every one
of its five comedy entries was phrased as a restriction on comedy rather than a permission
for it. See the tone rebalance made on 2026-09-17 in the same template.

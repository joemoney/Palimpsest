# Reviewing the steering engine

The steering engine (`docs/analysis_and_plans/AUTHORING_TOOL/AUTHORING_TOOL_PHASES.md`, S5) rests on
assumptions no test can settle: tests check that a prompt contains the right text, not that the model
does anything useful with it. So the engine writes a **trace** as it plays, and a report turns it into
numbers. This note says what to look at and what each number can and cannot tell you.

## Getting the data

Play normally. Every turn appends to `data/traces/<user>/<story>.jsonl` (one JSON event per line; the
schema and its bounds are in `backend/engine_trace.py`). `PALIMPSEST_TRACE=0` turns it off. Then:

```bash
python3 scripts/steering_report.py                              # every trace
python3 scripts/steering_report.py --story the_missing_core --run <run id>
python3 scripts/steering_report.py --json > run_a.json          # to diff two builds
```

- A **run** is one playthrough (a save takes a `run_id` on its first turn). One file can hold several.
- `run_start` records the **build** (`steering-1`, ...) and which features were on, so a run made before
  a later piece landed can be compared with one made after.
- A regenerated turn is kept once (its re-roll). `--keep-regen` keeps both.
- The trace holds ids, counts, sizes and outcomes, not player text or narration. To judge whether an event
  was *right*, read the save's transcript at the same `turn`
  (`history.full_transcript` followed by `recent_turns` in `data/saves/<user>/<story>.json`).

## Assumptions, and the numbers that test them

| # | Assumption | Report section | Look for | It cannot tell you |
|---|---|---|---|---|
| 1 | The state-update pass sets a declared flag when its event happens, and not otherwise | `declared flags` | `set` vs `never set`; `lag` (turns from first asked to set); `dropped_false`; `share of the state-update prompt` | Whether a set was *correct*. Read the transcript at `set_turn`. A flag set at its first-asked turn, or ten turns before its event, is the failure to look for. |
| 2 | Listing unset flags costs a bounded amount of prompt | `declared flags` | `asked-text chars` (median, max) and its share of the state-update prompt, falling as flags are set | |
| 3 | Offering a waypoint's `plant` (to the act generator, or in a nudge) makes it happen sooner | `steering association` | `offered` vs `not offered`: share planted within one act-check interval, the four `offered × live carrier` cells, and the `via act` / `via nudge` / `via none` rows | **Causation.** A waypoint is offered *because* it is unplanted, and one with a running carrier plants sooner anyway. Read the gap with its sample size; a gap that holds inside the `live_carrier=False` row is the interesting one. |
| 4 | Offers rotate, so every steered destination gets set up | `acts` → `fairness` | `offers_min`/`offers_max` close together; `distinct_used` near the number of unplanted waypoints | |
| 5 | The act director produces acts when asked | `acts` | `ready_rate`, `turns between acts`, `due` (cadence vs completed) | |
| 6 | The plant text is small | `acts` | `plant text chars` (median, max) | |
| 7 | The funnel's scores rise as the story approaches an ending | `funnel` | each destination's `first`/`last`/`max` score; how often the steered set changed | Whether the scores mean anything. Compare `end_turn` and `cause` with the budget. |
| 8 | The story ends by commitment, not by the forced backstop | `funnel` | `cause`; `end_turn` against `commit_by`; `judge said not-now`, `committed by the null limit` | |
| 9 | A destination is pruned only for a real reason | `funnel` → `pruned` | each prune's turn and `why` (`viable_while` vs `carriers_failed`) | |
| 10 | Every steered waypoint has a carrier that is running when it matters | `carriers` | `steered-waypoint checks with no running carrier` of the total | |
| 11 | Early activation starts a thread that then delivers its waypoint | `carriers` | each started thread: `planted_turn` and `turns_to_plant`; `scans with a candidate` (how often it would fire at all) | Whether the thread would have started anyway. Compare `activated by` (early vs condition). |
| 12 | Generation was mostly inventing threads the story did not need | `threads` | `generated` per 100 turns: the baseline for restricting generation to texture | |
| 13 | A nudge and a pacing directive rarely collide | `nudges and directives` | `turns with nudge and directive`; the nudge's `parts` | This is the baseline for the precedence rule between a drive nudge and a directive. |
| 15 | Raising a carrier's priority puts the right thread in front of the narrator | `nudge steering` | `boost changed the lead thread` (how often the boost, not the thread's own priority, decided who led); `carrier plant on a thread line` | Whether the narrator then did anything with the plant: read the transcript at the nudge's turn. |
| 16 | Hints rotate, and never crowd a nudge | `nudge steering` | `hints … by destination` roughly even; at most one per nudge (structural) | Whether a hint gives an ending away. It is the author's fragment, shown from the Open phase for every steered destination; read a few in context. |
| 17 | The drive nudge appears only from `narrow_until` and points at the right destination | `nudge steering` | `drive nudges` and their `leaders`; leader flips between checks (`funnel` scores) | |
| 18 | A drive nudge rarely has to give way to a pacing rule | `nudge steering` | `yielded to a pacing rule` against `drive nudges`, and the rule named | A high yield rate means the two mechanisms fight for the same turns; the nudge cadence (`nudge_frequency`) and the rule's threshold are the levers. |
| 19 | Steering is a small part of the nudge | `nudge steering` | `characters added` (median, max, share of the nudge) | |
| 14 | Steering adds little wall-clock time | `cost` | `seconds per turn`, `act_advancement_check` p50/p90, prompt characters over time | |

## Reading a report

- **Small samples are marked.** A rate over fewer than 8 items prints `(small n)`. One playthrough of
  `the_missing_core` has 12 waypoints and a few dozen act checks, so most steering numbers will be small
  for a single run: pool several, or read the rows instead of the rate.
- **The rows are the evidence.** `waypoints` lists each waypoint's plant turn, route, first offer and carrier
  states at the moment; `carriers` lists each early start and what became of its waypoint. Read those beside
  the transcript before trusting a summary.
- **Comparing builds:** run the report with `--json` on each and diff the sections you care about. Only
  compare runs of the same `story_version` and, ideally, the same `budget`.

## What is not in the trace

- Whether narration *actually staged* a planted event. The trace records that the waypoint was planted
  (its `done_when` held, or the model reported its `detect`), not that the scene was good.
- Model output beyond the ids and outcomes the engine acted on.
- Anything from before the trace existed. Old saves have no `run_id` until their next turn.

## Known limits of the instrument

- A failed write (full disk, unwritable directory) turns tracing off for the rest of that process and
  prints one line to stderr. The turn is never affected. Check for `[TRACE] disabled` in the logs before
  trusting a short file.
- The steering association is computed at funnel checks (every `check_every` turns), so a waypoint
  planted and offered between two checks is seen only at the next one.

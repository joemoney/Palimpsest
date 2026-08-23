# Millbrook — *The Last Ferry to Millbrook*

See the top-level `README.md` for how to run the engine itself. This file is
about this specific story.

## Why this story exists

This is the one story bundled directly in the public repo, committed
alongside the engine's code. It exists so the repo is runnable and demoable
on its own, with zero setup beyond cloning it — everything else lives in
private companion repos (see "Public vs. private stories" in the top-level
README). Treat it as both a working demo and a template to copy when
authoring a new `stories/<slug>/template.json`.

## Synopsis

The last ferry of the season drops you in Millbrook just as the fog rolls
in — and doesn't leave. The town is friendly, the inn is warm, and everyone
insists nothing is wrong. But the ferry isn't due back for a week, and
you're starting to notice that no one in Millbrook quite agrees on what
happened yesterday.

This is also stored as `meta.synopsis` in `template.json`, so the web
interface's story picker can display it directly — keep the two in sync if
you edit either.

## Setting Reference

A small, cozy small-town mystery with a persistent, unresolved uncanniness
underneath — closer to a quiet ghost story than horror. Deliberately low on
lore and worldbuilding compared to a full story like New Babel, so it's easy
to read the whole schema in one sitting:

- **Setting**: Millbrook, a fog-bound coastal town reachable only by a
  weekly ferry. Four locations (`world.locations`): the ferry dock, the
  Harborlight Inn, the town square, and the off-limits lighthouse.
- **World rules** (`world.rules`) establish the town's central conceit —
  residents' accounts of "yesterday" genuinely contradict each other, the
  fog is a real constant rather than a device to be explained away, and
  nothing supernatural is ever confirmed outright. Strangeness is meant to
  accumulate through small contradictions, not exposition.
- **No reincarnation/memory-fragment hook** — `player.origin.memory_fragments`
  is present (the engine's state-update pass reads that path unconditionally
  regardless of story) but left empty, since this story doesn't use it. Any
  new story template needs the field to exist even if unused.
- **`plot.main_thread`** centers on working out why the town can't agree on
  what happened the day before, and what the lighthouse has to do with it —
  replace with whatever direction fits a new story built from this template.

## Starting Setting

The protagonist opens the story stepping off the ferry that just dropped
them at Millbrook's dock, with a letter in their pocket implying someone
here was expecting them (a hook `plot.main_thread` can pick up later, or
ignore — it's never explained in the opening itself). No prior-life hook,
no fragmented memory — a much simpler starting point than New Babel's,
representative of the *minimum* a story needs rather than the maximum.

- **The opening is fixed, hand-authored, and always the same** —
  `plot.opening_scene` (`narration_before_name` / `narration_after_name`) in
  `template.json`, played by `story_engine.run_opening_scene()`. As with
  every story, the name-entry prompt is woven into the scene itself (the
  innkeeper asking "what should I put you down as, in the guest book?"),
  not a meta setup screen.
- `narration_after_name` ends with the `OPTIONS:` block format the engine
  expects from every turn going forward (`<action label> || <first-person
  prose>`, one per line) — worth copying exactly when authoring a new
  story's opening scene, since that's the one narration the engine doesn't
  generate itself and so won't get the format right automatically.

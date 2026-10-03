# New Babel — *The Attention Economy*

This directory holds one story's content for the Palimpsest engine. This repo
holds every private story, one folder each, and is mounted as a single git
submodule at `stories/private/` in the (public) engine repo — so this story
lives at `stories/private/new_babel/` there, and isn't runnable on its own.
Kept in a separate private repo specifically so this story's content stays
private while the engine itself is open source; see
"Public vs. private stories" in the engine repo's top-level `README.md` for
how the split works and how to run it. This file is about this specific
story: its pitch, setting, and design rationale.

## Synopsis

You wake up in a Cordon Dynamics intake ward with no memory of dying — only a
name you have to choose for yourself, and a city, New Babel, that has no idea
you exist yet. Someone spent a fortune recomputing you back into existence, and
nobody, including you, knows why. In a metropolis where hacking and forbidden
magic are the same crime, you'll have to survive corporate bureaucracy,
black-market algorithmic occultists, and your own fragmented past to find out
who you were — and what the thing that rebuilt you actually wants.

This section is generated from `meta.synopsis` in `template.json` by the
authoring tool's save flow - edit the template, not this file, to change it.

## Setting Reference

**Pivoted from the original Rothfuss/Sympathy reference to a Lovecraftian/
cyberpunk fusion**, using Charles Stross's Laundry Files magic system as the
mechanical basis:

- **Reference: Charles Stross's "applied computation" magic system** (The
  Laundry Files) — magic is executing specific mathematical proofs against
  reality's structure; casting draws on real computational resources, and
  sufficiently complex computations risk attracting the attention of hostile
  extradimensional entities. This mechanic maps almost directly onto
  cyberpunk hacking/netrunning tropes with minimal rewriting needed — see
  `template.json` → `world.rules`.
- This fusion has real genre precedent (e.g. the game *Transient* and the
  novel *No Dogs in Philly*, both explicitly blending Lovecraftian horror
  with cyberpunk dystopia), so it's a well-trodden combination, not an
  untested mashup.
- Fusion hooks worth building out: cybernetic neural implants as literal
  computational conduits (more augmentation = more risk of "attention");
  black-market "occult exploits" traded like zero-days; a megacorp
  containment division playing the role Stross's bureaucratic agency plays
  in the original — equal parts terrifying and darkly funny.
- **City: a parallel-universe analog of Cyberpunk 2077's Night City** —
  original name (**New Babel**), original megacorps/gangs/districts, same
  genre DNA (corporate-arcology skyline, a submerged/abandoned lower quarter
  as combat zone, street-level factions carving up what the corps don't
  want). See `template.json` → `world.locations` / `world.factions`: Cordon
  Dynamics (the containment megacorp), Praetor Security Solutions (its
  corporate rival), the Null Choir (underground algorithmic occultists), the
  Chrome Wolves (Drowned Quarter gang).
- Only the *mechanics/structure* are being reused from either reference, not
  text, names, or specific plot content — this story's locations and
  factions are original and should keep being extended that way as the
  story needs more of them.

## Starting Setting: Reincarnation Hook (Isekai-style)

The protagonist opens the story freshly **reincarnated into this world** —
framed diegetically as being computed back into existence in the substrate
after dying elsewhere, which reuses the same applied-computation rules
already governing magic rather than introducing a separate mechanic.

- **Memory model: partial/fragmented, surfaces gradually** (not full
  recall, not full amnesia). Past-life memory returns only as discrete
  fragments triggered by specific in-story events, tracked in
  `player.origin.memory_fragments` (each with a `trigger`, `content`, and
  `revealed` flag). Two rules in `world.rules` enforce this: memory never
  returns fully or on command, and retained instinct shows up as unexplained
  aptitude for computation rather than as usable facts.
- **"Cheat" aptitude, not "cheat" knowledge** — the protagonist is unusually
  good at applied computation because of who they were before, but doesn't
  consciously know why until a relevant fragment surfaces. This keeps the
  overpowered-protagonist isekai trope from short-circuiting the mystery.
- **Opening scene default**: a megacorp Containment Division intake ward
  processing the protagonist as a newly-computed arrival — a dark-comedy
  inversion of the usual "friendly goddess explains the rules" isekai
  opening, in keeping with the Stross-style bureaucratic-horror tone.
- **The opening is fixed, hand-authored, and always the same** —
  `plot.opening_scene` (`narration_before_name` / `narration_after_name`) in
  `template.json`, played by `story_engine.run_opening_scene()`. Every
  playthrough starts from this identical beat; everything after it is
  free-branching LLM narration. It's a no-op once `opening_scene.played` is
  `true`, so resuming a save doesn't replay the intro.
- **Protagonist name is captured diegetically, in-scene** — the intake
  system's dialogue itself asks "what should we call you?", and the real
  name-entry prompt sits right at that line, so naming yourself reads as
  answering an in-fiction question rather than a meta setup screen. The name
  is substituted into `narration_after_name` and the whole opening is logged
  into `history_log.recent_turns` so the first LLM-generated turn has
  continuity. Blank input falls back to "Subject Zero".
- `plot.main_thread` defaults to pointing at the mystery of the fragmented
  past life and why the protagonist was reconstructed here — replace once
  the user has a more specific story goal in mind.

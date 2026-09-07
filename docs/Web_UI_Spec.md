# Web UI Specification — CYOA Story Interface

## Core Interaction Model
The UI behaves as a single, continuously-appending page. The UI never
triggers full page reloads. Functionally, it is a chat-style transcript
that alternates between AI narration and player choices. The feed renders
this transcript from top to bottom, in a scrolling view.

## Flow
1. **Narration renders** — the current scene's text appears in the feed.
   Then 2-4 choice options appear (buttons or clickable divs), according
   to `meta.scene_length_target` in `world_state.json`.
2. **The player selects a choice:**
   - The UI removes the choice options from the DOM, or hides them.
   - A new div renders. This div shows the player's selected choice. The
     UI styles this div distinctly from narration (for example,
     right-aligned, with a different background), similar to how a chat
     bubble marks the user's turn.
   - The next scene's narration starts to render immediately below that
     choice div. The feed continues to grow downward.
3. **Repeat.** The feed grows as narration-choice pairs stack vertically.

The page never refreshes. All updates are DOM-level appends, not full
navigations.

## Scrollback ("Save-Load") Behavior
- On load, or when the player resumes a saved session, only the last 2-3
  scenes render in the DOM.
- When the player scrolls toward the top of the feed, the UI loads the
  next-older batch of scenes. The UI prepends this batch above the
  currently-visible content. This pattern is "reverse infinite scroll."
  Chat apps such as Discord and Slack use the same pattern, so a player
  can scroll up through message history.
- **Implementation note:** When the UI prepends content above the user's
  current scroll position, the developer must manually adjust `scrollTop`
  after the prepend. Otherwise, the browser does not preserve the user's
  visual position, and the feed appears to jump. This is the most fragile
  part of the pattern. Developers must test this part early rather than
  late in the project.

## Suggested Frontend Fit
This is fundamentally an append-only feed with reverse-infinite-scroll
pagination, not complex client-side state. It maps cleanly onto any of
the three frontend options already discussed: Svelte, SolidJS, or HTMX.
**HTMX is a particularly strong fit here.** "Append new content below"
and "prepend older content above on scroll" are both native HTMX swap
patterns (`hx-swap="beforeend"` or `hx-swap="afterbegin"`), and neither
needs much custom JS. HTMX also pairs directly with the Python backend,
which already handles all the state and LLM work in `story_engine.py`.

## Turn Latency (as implemented)
One turn is not one request-response round trip. A turn can take anywhere
from a few seconds to well over a minute. In sequence, a turn can run
narration, a follow-up state-update call, and, conditionally, a repair
call for a missing OPTIONS block, subplot generation, the judgment to
advance the act, and a summary rollover.

A real incident showed that holding a single HTTP response open for that
whole span is not safe through every network path a deployment might sit
behind. An intermediate proxy or tunnel can cancel a connection well
before the backend actually finishes and saves.

Because of this risk, step 2 ("player selects a choice") above is, as
implemented, a three-part handoff rather than one call. The submission
starts the turn and returns almost immediately. The client then polls a
status endpoint for progress. Once that status reports done, a second
fetch retrieves the actual new scene and choices, and the UI appends them
to the feed.

This handoff is invisible to the "Core Interaction Model" above; the feed
still only ever grows by appending narration-choice pairs. Any
reimplementation on a different stack must budget for this as a polling
loop, not a single request-response per turn. See `CLAUDE.md`'s "Web UI"
section, "Asynchronous turn-taking," for the exact mechanics as built.

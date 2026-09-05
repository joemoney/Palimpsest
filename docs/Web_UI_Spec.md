# Web UI Specification — CYOA Story Interface

## Core Interaction Model
The UI behaves as a single, continuously-appending page — no full reloads.
Functionally, it's a chat-style transcript alternating between AI narration
and player choices, rendered top-to-bottom in a scrolling feed.

## Flow
1. **Narration renders** — the current scene's text appears in the feed,
   followed by 2-4 choice options (buttons or clickable divs), per
   `meta.scene_length_target` in `world_state.json`.
2. **Player selects a choice:**
   - The choice options are removed from the DOM (or hidden).
   - A new div renders showing the player's selected choice — styled
     distinctly from narration (e.g. right-aligned, different background),
     similar to how a chat bubble marks the user's turn.
   - The next scene's narration begins rendering immediately below that
     choice div, continuing the feed downward.
3. Repeat — the feed grows as narration/choice pairs stack vertically.

No page refresh occurs at any point — all updates are DOM-level appends,
not full navigations.

## Scrollback ("Save-Load") Behavior
- On load, or when resuming a saved session, only the **last 2-3 scenes**
  render in the DOM.
- Scrolling toward the top of the feed triggers loading of the next-older
  batch of scenes, prepended above the currently-visible content — this is
  "reverse infinite scroll," the same pattern chat apps like Discord/Slack
  use for scrolling up through message history.
- **Implementation note:** prepending content above the user's current
  scroll position requires manually adjusting `scrollTop` after the
  prepend, or the browser won't preserve the user's visual position (the
  feed will appear to jump). This is the most fragile part of the pattern —
  worth testing early rather than leaving for later.

## Suggested Frontend Fit
This is fundamentally an append-only feed with reverse-infinite-scroll
pagination — not complex client-side state. It maps cleanly onto any of
the three frontend options already discussed (Svelte, SolidJS, HTMX), but
**HTMX is a particularly strong fit here specifically**: "append new
content below" and "prepend older content above on scroll" are both native
HTMX swap patterns (`hx-swap="beforeend"` / `"afterbegin"`) without much
custom JS — and it pairs directly with the Python backend already doing
all the state/LLM work in `story_engine.py`.

## Turn Latency (as implemented)
One turn is not one request-response round trip. A turn can take anywhere
from a few seconds to well over a minute (narration, plus a follow-up
state-update call, plus - conditionally - a repair call for a missing
OPTIONS block, subplot generation, act-advancement judgment, and a summary
rollover, run in sequence), and a real incident showed that holding a single HTTP response open for that whole
span isn't safe through every network path a deployment might sit behind -
an intermediate proxy/tunnel can cancel a connection well before the
backend actually finishes and saves. So step 2 ("player selects a choice")
above is, as implemented, a three-part handoff rather than one call: the
submission starts the turn and returns almost immediately, the client polls
a status endpoint for progress, and once that reports done, a second fetch
retrieves the actual new scene and choices to append to the feed. This is
invisible to the "Core Interaction Model" above - the feed still only ever
grows by appending narration/choice pairs - but any reimplementation on a
different stack should budget for this as a polling loop, not a single
request/response per turn. See `CLAUDE.md`'s "Web UI" → "Asynchronous
turn-taking" for the exact mechanics as built.
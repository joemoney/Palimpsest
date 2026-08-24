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
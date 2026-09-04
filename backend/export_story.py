#!/usr/bin/env python3
"""CLI wrapper around story_engine.export_narrative - exports a save's narrative as plain
readable text: just the story the LLM generated, not the player's typed actions (unless
--include-actions is passed) or any of the surrounding plot/character/pacing state that
`data/saves/<user>/<story>.json` otherwise carries. Same export the web UI's "Export Story"
link (app.py's /play/<story_slug>/export route) uses.
"""
import sys

import state_store
import story_engine


def main():
    user_id, story_slug, argv = state_store.parse_user_story_args(sys.argv[1:])
    include_actions = "--include-actions" in argv
    argv = [a for a in argv if a != "--include-actions"]

    output_path = None
    if "--output" in argv:
        i = argv.index("--output")
        if i + 1 >= len(argv):
            print("Usage: python export_story.py [--user ID] [--story SLUG] [--output PATH] [--include-actions]")
            return
        output_path = argv[i + 1]

    state = state_store.load_state(user_id, story_slug)
    text = story_engine.export_narrative(state, include_actions=include_actions)

    if output_path:
        with open(output_path, "w") as f:
            f.write(text)
        print(f"Wrote {output_path}")
    else:
        print(text)


if __name__ == "__main__":
    main()

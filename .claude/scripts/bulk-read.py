#!/usr/bin/env python3
"""Delegate reading. The files go to the cheap worker, only bullets return.

    .claude/scripts/bulk-read.py --question "where is retry configured?" a.java b.java

Nothing is stored. The worker is a one-shot completion with no session and
no tools; the file contents never enter the calling context.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "portal"))
import runner  # noqa: E402

# A rough ceiling so one call cannot blow the 30s cap.
MAX_CHARS = int(os.environ.get("PORTAL_MAX_CHARS", 400_000))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--question", "-q", required=True,
                    help="what you need to know, in one sentence")
    ap.add_argument("--mode", default="bulk-reader")
    ap.add_argument("files", nargs="+")
    args = ap.parse_args()

    blocks, total = [], 0
    for path in args.files:
        if not os.path.isfile(path):
            sys.exit(f"portal: no such file: {path}")
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except OSError as e:
            sys.exit(f"portal: cannot read {path}: {e}")
        total += len(text)
        if total > MAX_CHARS:
            sys.exit(
                f"portal: {total} characters exceeds the per-call budget of "
                f"{MAX_CHARS}. A delegation is capped at 30 seconds, so split "
                "this into two or more bulk-read calls.")
        blocks.append(
            f'<file path="{path}" lines="{text.count(chr(10)) + 1}">\n{text}\n</file>')

    prompt = ("<files>\n" + "\n".join(blocks) + "\n</files>\n\n"
              f"Question: {args.question}")

    mode = runner.load_mode(args.mode)
    print(runner.run(mode, prompt))


if __name__ == "__main__":
    main()

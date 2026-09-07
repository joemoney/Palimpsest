#!/usr/bin/env python3
"""Delegate generation. The worker writes the file; the caller never reads it.

    .claude/scripts/code-write.py \
        --spec "a paginated list endpoint for orders" \
        --reference src/api/UserController.java \
        --out src/api/OrderController.java

The reference is required, not optional. Without one the worker invents a
house style and the expensive model has to rewrite the result, which costs
more than it saved.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "portal"))
import runner  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--spec", "-s", required=True,
                    help="what the file should do")
    ap.add_argument("--reference", "-r", required=True,
                    help="an existing file whose style the output must match")
    ap.add_argument("--out", "-o", required=True, help="path to write")
    ap.add_argument("--mode", default="code-writer")
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing --out")
    args = ap.parse_args()

    if not os.path.isfile(args.reference):
        sys.exit(f"portal: reference file not found: {args.reference}. "
                 "A reference is required.")
    if os.path.exists(args.out) and not args.force:
        sys.exit(f"portal: {args.out} exists. Pass --force to overwrite.")

    reference = open(args.reference, encoding="utf-8", errors="replace").read()
    prompt = (
        f'<reference path="{args.reference}">\n{reference}\n</reference>\n\n'
        f"Write the complete contents of {args.out}.\n\n"
        f"Spec: {args.spec}")

    mode = runner.load_mode(args.mode)
    code = runner.strip_fences(runner.run(mode, prompt))
    if not code:
        sys.exit("portal: worker returned nothing. Narrow the spec and retry.")

    parent = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(parent, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(code if code.endswith("\n") else code + "\n")

    # Only this line reaches the calling context. Not the code.
    print(f"portal: wrote {args.out} ({code.count(chr(10)) + 1} lines), style "
          f"matched to {args.reference}. The content was not read back. "
          f"Verify by running the tests, or Read a specific range if a "
          f"targeted check is needed.")


if __name__ == "__main__":
    main()

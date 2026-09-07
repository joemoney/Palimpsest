#!/usr/bin/env python3
"""PreToolUse hook on Read. Fires on every Read call.

Blocks an untargeted read of a file over the line threshold and tells the
model exactly what to do instead. A Read carrying offset or limit is a
targeted read and always passes: that is the escape hatch that keeps
editing possible.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from portal_common import (  # noqa: E402
    allow, count_lines, deny, is_exempt, is_worker, read_event, script_path,
    threshold,
)

MESSAGE = """portal: {path} is {lines} lines, over the {limit}-line threshold, so a full read is blocked.

Pick one:

1. Delegate the reading to the cheap worker:
     {script} --question "<what you need to know>" {path}
   It returns bullets. The file itself never enters this context, so
   follow-up turns do not pay for it again. Pass several files at once.

2. Read the exact region you need, any size:
     Read({path}, offset=<start>, limit=<count>)
   Targeted reads are deliberately allowed. Use this before an Edit, since
   worker bullets carry no reliable line numbers.

Do not delegate debugging, architectural judgement, or safety-critical
review. Those are worth the frontier rate."""


def main():
    event = read_event()
    if is_worker() or event.get("tool_name") != "Read":
        allow()

    tool_input = event.get("tool_input") or {}
    path = tool_input.get("file_path")
    if not path:
        allow()

    # A targeted read is the sanctioned way to get exact lines.
    if tool_input.get("offset") or tool_input.get("limit"):
        allow()

    if is_exempt(path):
        allow()

    lines = count_lines(path)
    if lines is None or lines <= threshold():
        allow()

    deny(MESSAGE.format(path=path, lines=lines, limit=threshold(),
                        script=script_path("bulk-read.py")))


if __name__ == "__main__":
    main()

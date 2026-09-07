#!/usr/bin/env python3
"""PreToolUse hook on Bash. Closes the shell route around the Read hook.

cat, head, tail, less and more can pull a whole file into the context
without ever touching the Read tool. This checks each pipeline stage and
blocks the ones that would do that, while leaving genuinely bounded reads
(`head -n 40`) and filtered pipelines (`cat x | grep y`) alone.
"""
import os
import re
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from portal_common import (  # noqa: E402
    allow, count_lines, deny, is_exempt, is_worker, read_event, script_path,
    threshold,
)

READERS = {"cat", "head", "tail", "less", "more", "bat", "view"}
PAGERS = {"less", "more"}
# If output flows into one of these, the context sees the filtered result.
FILTERS = {"grep", "rg", "egrep", "fgrep", "awk", "sed", "wc", "head", "tail",
           "jq", "yq", "sort", "uniq", "cut", "diff", "tr", "python3", "python"}

MESSAGE = """portal: `{prog}` on {path} would put about {lines} lines into the context, over the {limit}-line threshold.

Pick one:

1. Delegate it:
     {script} --question "<what you need to know>" {path}

2. Bound the read yourself, either
     Read({path}, offset=<start>, limit=<count>)
   or a shell read with an explicit small count, e.g. `head -n 100 {path}`,
   or pipe into a filter such as grep, rg or awk so only matches come back."""

PAGER_MESSAGE = """portal: `{prog}` is a pager and dumps the whole file into the context. Use
`{script} --question "..." {path}` to delegate, or a
targeted Read with offset and limit."""


def split_commands(command: str):
    """Split on ; && || and newlines, keeping pipelines intact."""
    return re.split(r"(?:;|&&|\|\||\n)", command)


def stage_words(stage: str):
    try:
        return shlex.split(stage)
    except ValueError:
        return stage.split()


def explicit_limit(prog, args):
    """How many lines head/tail will emit, or None if unbounded.

    Bare `head file` / `tail file` default to 10, which is bounded and fine.
    `-n +N` counts from a line rather than limiting, so it is unbounded.
    """
    if prog not in ("head", "tail"):
        return None
    for i, a in enumerate(args):
        value = None
        if a == "-n" and i + 1 < len(args):
            value = args[i + 1]
        else:
            m = re.fullmatch(r"-n?(\+?\d+)", a)
            if m:
                value = m.group(1)
        if value is None:
            continue
        if value.startswith("+"):
            return None  # counts from a line, not a cap
        try:
            return int(value)
        except ValueError:
            return None
    return 10  # POSIX default


def main():
    event = read_event()
    if is_worker() or event.get("tool_name") != "Bash":
        allow()

    command = (event.get("tool_input") or {}).get("command", "")
    if not command:
        allow()

    limit = threshold()

    for pipeline in split_commands(command):
        stages = [s for s in pipeline.split("|") if s.strip()]
        for idx, stage in enumerate(stages):
            words = stage_words(stage)
            if not words:
                continue
            prog = os.path.basename(words[0])
            if prog not in READERS:
                continue

            downstream = [os.path.basename(stage_words(s)[0])
                          for s in stages[idx + 1:] if stage_words(s)]
            if any(d in FILTERS for d in downstream):
                continue  # output is filtered before it reaches the context

            args = words[1:]
            n = explicit_limit(prog, args)
            if n is not None and n <= limit:
                continue  # bounded to a safe number of lines

            for a in args:
                if a.startswith("-") or not os.path.isfile(a):
                    continue
                if is_exempt(a):
                    continue
                lines = count_lines(a)
                if lines is None:
                    continue
                if prog in PAGERS:
                    deny(PAGER_MESSAGE.format(prog=prog, path=a,
                                              script=script_path("bulk-read.py")))
                emitted = lines if n is None else min(n, lines)
                if emitted > limit:
                    deny(MESSAGE.format(
                        prog=prog, path=a, lines=emitted, limit=limit,
                        script=script_path("bulk-read.py")))

    allow()


if __name__ == "__main__":
    main()

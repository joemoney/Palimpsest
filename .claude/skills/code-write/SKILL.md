---
name: code-write
description: Generate a new code file from a spec plus an existing reference file, written straight to disk by a cheap worker. Use for boilerplate that follows an established pattern - a new endpoint like the existing endpoints, a new test like the existing tests, a new DTO, adapter or migration. Do not use for architectural decisions, safety-critical code, or edits to an existing file.
allowed-tools: Bash
---

# code-write

The worker writes the file. You never read what it produced, which is the
whole point: the generated code never occupies context.

```bash
.claude/scripts/code-write.py \
  --spec "<what the file should do>" \
  --reference <an existing file in the same style> \
  --out <path to write>
```

## The reference is required

Pick a file that already does the closest thing, in the same language and
the same layer. The worker matches its imports, naming, error handling and
test conventions. Without a reference the worker invents a house style and
you end up rewriting it, which costs more than it saved.

## After it runs

You get back one line: the path and the line count. Verify by running the
tests or the type checker. If you need to check a specific part, do a
targeted `Read` with `offset` and `limit` — do not read the whole file back
in, or you have paid the frontier rate for it after all.

## When not to use it

- **Editing an existing file.** Use `Edit` after a targeted `Read`.
- **Anything that carries judgement** — architecture, concurrency,
  auth, anything safety-critical. Those are excluded from routing on
  purpose, not because the worker failed a test.
- **Anything small.** Under a few dozen lines, write it yourself. The
  round trip is 10 to 30 seconds and a single call is capped at 30, so
  large generations have to be split anyway.

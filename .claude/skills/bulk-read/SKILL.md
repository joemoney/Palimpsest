---
name: bulk-read
description: Read one or more large files without pulling them into context. Use when you need to know something that lives in files over ~350 lines - locating a symbol, tracing a call path, summarising a config, answering "where is X handled" across a set of files. Also use after a portal hook blocks a Read. Do not use when you are about to edit the file, when you are debugging, or when the file is small.
allowed-tools: Bash
---

# bulk-read

Sends whole files to a cheap worker with your question attached. Bullets
come back. The files never enter this context, so follow-up turns are free.

```bash
.claude/scripts/bulk-read.py --question "<one sentence>" path/to/file [more files...]
```

## Writing the question

The worker answers exactly what it is asked and stops. One specific
question beats a vague one.

- Good: `where is the retry policy configured and what are its bounds?`
- Good: `list every public method and the exception each one throws`
- Bad: `summarise this file` — you get a shape, not a fact.

Batch related files into one call rather than one call per file. Each
delegation is a network round trip of 10 to 30 seconds, so two calls cost
twice the latency for the same tokens.

## When not to use it

- **You are about to edit.** The bullets carry no reliable line numbers.
  Use a targeted `Read` with `offset` and `limit` instead; the hook always
  lets those through.
- **You are debugging.** The worker finds surface patterns and stops. Work
  that needs judgement is worth the frontier rate.
- **The file is small.** Below the threshold the 10-30s round trip costs
  more than the tokens it saves.

#!/usr/bin/env python3
"""Server-only performance dashboard: parses `docker logs palimpsest-web` for the
per-call [TIMING] lines (backend/story_engine.py's _timed() wrapper - narration,
state_update, subplot_generation, act_advancement_check, end_story_final_arc,
summary_rollover) and the gunicorn access log's /api/turn and /api/regenerate lines,
and reports latency stats for each.

Each [TIMING] line carries the model name that call actually hit (e.g. "narration
model=deepseek/deepseek-v4-pro-20260813: 12.34s"), so stats are grouped by (label,
model) pair - TIER_AB_MODEL/TIER_C_MODEL are meant to be freely swapped via
.env for testing (see CLAUDE.md's Gemini fail-safe bullet), and a single retained log
can span several different models across restarts. Grouping by model keeps those
runs from being silently averaged together into a meaningless blend.

Deliberately NOT a Flask route - this reads `docker logs` directly and needs shell/
docker access to the host, so it's only reachable by whoever can already open a
terminal on the server. Never wire this into app.py; a web route on the same process
that's tunneled to the internet would expose it to every player. See CLAUDE.md's
"Web UI" section for the play app's actual routes, none of which this touches.

By default (no --persist/--report-history), this only covers whatever `docker logs`
currently retains for the container - a container recreation (not just `docker
compose restart`) clears that history, so a one-shot run reports recent performance,
not a durable long-term record. Use --persist to change that (see below). Uses only
the standard library, so it runs on the host's system Python with no project
dependencies installed.

Run this on the HOST (wherever the `docker` CLI can see palimpsest-web from outside),
NOT inside the container via `docker exec` - a container doesn't have its own Docker
client, so `docker logs` isn't available from in there. If you're already in a shell
inside the container, `exit` first.

Usage (from the host, in the repo root or anywhere - paths below aren't relative to cwd):
  python3 scripts/perf_dashboard.py                 # one-shot report, full retained log
  python3 scripts/perf_dashboard.py --since 2h       # only the last 2 hours (docker logs --since syntax)
  python3 scripts/perf_dashboard.py --watch 30       # re-run and reprint every 30s

Durable history (survives container recreation / log rotation):
  python3 scripts/perf_dashboard.py --persist        # append new log lines to
                                                      # data/perf_history.jsonl, then exit
  python3 scripts/perf_dashboard.py --report-history # report from data/perf_history.jsonl,
                                                      # split into pre/post ARCHITECTURE_CUTOVER

--persist is meant to be run periodically (e.g. a cron job) so nothing is lost to log
rotation between runs; it tracks the timestamp of the last line it saw in
data/perf_history_state.json and only appends genuinely new lines each time.
"""
import argparse
import datetime
import json
import os
import re
import statistics
import subprocess
import sys

TIMING_RE = re.compile(r"\[TIMING\] (\S+) model=(\S+): ([\d.]+)s")
# Pre-model-tagging log lines (older retained logs, or a not-yet-restarted container)
# lack "model=" - still parsed, just grouped under this placeholder rather than dropped.
TIMING_RE_LEGACY = re.compile(r"\[TIMING\] (\S+): ([\d.]+)s")
UNKNOWN_MODEL = "(unknown - pre model-tagging log line)"
ACCESS_RE = re.compile(r'"(GET|POST) (\S+) HTTP/\S+"\s+(\d+)\s+([\d.]+)s')
TURN_PATHS = ("/api/turn", "/api/regenerate")
# `docker logs --timestamps` prefixes each line with an RFC3339 timestamp + a space.
TIMESTAMP_RE = re.compile(r"^(\S+)Z?\s(.*)$")

# The date the "new architecture" (schema v2 migration, mechanics module pattern,
# SECTIONS prompt refactor - commit 396af0f) shipped. Historical perf data is split
# at this boundary on request, since latency/behavior before and after isn't
# comparable - don't average across it without saying so.
ARCHITECTURE_CUTOVER = datetime.date(2026, 9, 4)

# data/ is gitignored (see CLAUDE.md's "Multi-User, Multi-Story Architecture" -
# runtime-only state lives here), which is exactly right for this too: it's a local
# durable cache built from docker logs, not source content.
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
HISTORY_PATH = os.path.join(DATA_DIR, "perf_history.jsonl")
HISTORY_STATE_PATH = os.path.join(DATA_DIR, "perf_history_state.json")


def fetch_logs(container: str, since: str | None, timestamps: bool = False) -> str:
    cmd = ["docker", "logs"]
    if timestamps:
        cmd.append("--timestamps")
    cmd.append(container)
    if since:
        cmd += ["--since", since]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        print(
            "error: `docker` command not found. This script needs to run on the HOST,\n"
            "not inside the palimpsest-web container - a container doesn't have its own\n"
            "Docker client. If you're in a shell from `docker exec -it palimpsest-web bash`,\n"
            "run `exit` first, then run this script from the host instead.",
            file=sys.stderr,
        )
        sys.exit(1)
    if result.returncode != 0:
        print(f"error running `docker logs {container}`: {result.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    # gunicorn writes access logs to stdout and error/timing prints to stdout too
    # (PYTHONUNBUFFERED=1 in the Dockerfile) - `docker logs` merges both streams anyway.
    return result.stdout + result.stderr


def _stats(values: list) -> dict:
    values = sorted(values)
    n = len(values)
    return {
        "count": n,
        "min": values[0],
        "max": values[-1],
        "mean": statistics.mean(values),
        "p50": statistics.median(values),
        "p90": values[min(n - 1, int(n * 0.9))],
    }


def _parse_line(line: str):
    """Return a parsed record dict for one (non-timestamped) log line, or None."""
    m = TIMING_RE.search(line)
    if m:
        label, model, seconds = m.group(1), m.group(2), float(m.group(3))
        return {"kind": "timing", "label": label, "model": model, "seconds": seconds}
    m = TIMING_RE_LEGACY.search(line)
    if m:
        label, seconds = m.group(1), float(m.group(2))
        return {"kind": "timing", "label": label, "model": UNKNOWN_MODEL, "seconds": seconds}
    m = ACCESS_RE.search(line)
    if m and any(p in m.group(2) for p in TURN_PATHS):
        status, seconds = m.group(3), float(m.group(4))
        return {"kind": "turn", "status": status, "seconds": seconds}
    return None


def parse(log_text: str):
    """One-shot (non-timestamped) parse, used by the live --watch/one-off report path."""
    timings = {}  # {(label, model): [seconds, ...]}
    turn_durations = []
    status_counts = {}
    for line in log_text.splitlines():
        rec = _parse_line(line)
        if rec is None:
            continue
        if rec["kind"] == "timing":
            timings.setdefault((rec["label"], rec["model"]), []).append(rec["seconds"])
        else:
            turn_durations.append(rec["seconds"])
            status_counts[rec["status"]] = status_counts.get(rec["status"], 0) + 1
    return timings, turn_durations, status_counts


def parse_timestamped(log_text: str):
    """Parse `docker logs --timestamps` output into a flat list of records, each
    tagged with the ISO timestamp docker attached to that line - used for the
    durable history path, since bucketing by architecture era requires knowing
    when each line was actually logged."""
    records = []
    for line in log_text.splitlines():
        m = TIMESTAMP_RE.match(line)
        if not m:
            continue
        ts, rest = m.group(1), m.group(2)
        rec = _parse_line(rest)
        if rec is not None:
            rec["ts"] = ts
            records.append(rec)
    return records


def _load_history_state() -> dict:
    try:
        with open(HISTORY_STATE_PATH, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_history_state(state: dict):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp_path = HISTORY_STATE_PATH + f".tmp{os.getpid()}"
    with open(tmp_path, "w") as f:
        json.dump(state, f)
    os.replace(tmp_path, HISTORY_STATE_PATH)


def persist(container: str):
    """Append any log lines newer than the last persisted timestamp to
    data/perf_history.jsonl. Safe to run repeatedly (e.g. from cron) - each run
    only fetches --since the last seen timestamp, and de-dupes against it exactly
    so a line isn't double-counted if `docker logs --since` returns it again."""
    state = _load_history_state()
    last_ts = state.get("last_ts")
    log_text = fetch_logs(container, since=last_ts, timestamps=True)
    records = parse_timestamped(log_text)
    if last_ts:
        records = [r for r in records if r["ts"] > last_ts]
    if not records:
        print("No new timed calls or turn requests since last --persist run.")
        return
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(HISTORY_PATH, "a") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")
    newest_ts = max(r["ts"] for r in records)
    _save_history_state({"last_ts": newest_ts})
    print(f"Appended {len(records)} new record(s) to {HISTORY_PATH} (up to {newest_ts}).")


def _era(ts: str) -> str:
    # ts looks like "2026-09-04T12:34:56.789012345Z" - date is always the first 10 chars.
    date = datetime.date.fromisoformat(ts[:10])
    return "pre" if date < ARCHITECTURE_CUTOVER else "post"


def load_history_by_era():
    """Read data/perf_history.jsonl and split its records into two eras, straddling
    ARCHITECTURE_CUTOVER. Returns {"pre": (timings, turn_durations, status_counts),
    "post": (...)} in the same shape `parse()` returns, so `render()` can be reused
    unchanged for both."""
    buckets = {
        "pre": ({}, [], {}),
        "post": ({}, [], {}),
    }
    try:
        with open(HISTORY_PATH, "r") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return buckets
    for line in lines:
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        timings, turn_durations, status_counts = buckets[_era(rec["ts"])]
        if rec["kind"] == "timing":
            timings.setdefault((rec["label"], rec["model"]), []).append(rec["seconds"])
        else:
            turn_durations.append(rec["seconds"])
            status_counts[rec["status"]] = status_counts.get(rec["status"], 0) + 1
    return buckets


def _print_table(rows, headers):
    widths = [
        max(len(str(r[i])) for r in ([headers] + rows)) for i in range(len(headers))
    ]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print(fmt.format(*["-" * w for w in widths]))
    for r in rows:
        print(fmt.format(*r))


def render(timings, turn_durations, status_counts, title="Palimpsest performance dashboard"):
    print("=" * 72)
    print(f"{title}  ({sum(len(v) for v in timings.values())} timed calls, "
          f"{len(turn_durations)} turn/regenerate requests)")
    print("=" * 72)

    if timings:
        print("\nPer-call LLM latency (seconds), by label + model:\n")
        rows = []
        for key in sorted(timings, key=lambda k: -sum(timings[k])):
            label, model = key
            s = _stats(timings[key])
            rows.append([
                label, model, s["count"], f"{s['min']:.2f}", f"{s['mean']:.2f}",
                f"{s['p50']:.2f}", f"{s['p90']:.2f}", f"{s['max']:.2f}",
            ])
        _print_table(rows, ["label", "model", "count", "min", "mean", "p50", "p90", "max"])
    else:
        print("\nNo [TIMING] lines found.")

    if turn_durations:
        s = _stats(turn_durations)
        print("\nEnd-to-end /api/turn + /api/regenerate request duration (seconds):\n")
        _print_table(
            [[s["count"], f"{s['min']:.2f}", f"{s['mean']:.2f}", f"{s['p50']:.2f}",
              f"{s['p90']:.2f}", f"{s['max']:.2f}"]],
            ["count", "min", "mean", "p50", "p90", "max"],
        )
        print(f"\nStatus codes: {dict(sorted(status_counts.items()))}")
    else:
        print("\nNo /api/turn or /api/regenerate access log lines found.")
    print()


def render_history():
    buckets = load_history_by_era()
    print(f"Durable history from {HISTORY_PATH}, split at ARCHITECTURE_CUTOVER = "
          f"{ARCHITECTURE_CUTOVER.isoformat()} (schema v2 / mechanics module migration):\n")
    timings, turn_durations, status_counts = buckets["pre"]
    render(timings, turn_durations, status_counts,
           title=f"PRE-{ARCHITECTURE_CUTOVER.isoformat()} (old architecture)")
    timings, turn_durations, status_counts = buckets["post"]
    render(timings, turn_durations, status_counts,
           title=f"POST-{ARCHITECTURE_CUTOVER.isoformat()} (new architecture)")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--container", default="palimpsest-web", help="container name (default: palimpsest-web)")
    parser.add_argument("--since", default=None, help="only logs since this time, e.g. '2h', '30m' (docker logs --since syntax)")
    parser.add_argument("--watch", type=int, default=None, metavar="SECONDS", help="re-run and reprint every SECONDS")
    parser.add_argument("--persist", action="store_true",
                         help="append new log lines to data/perf_history.jsonl and exit (no report printed)")
    parser.add_argument("--report-history", action="store_true",
                         help="report from data/perf_history.jsonl, split into pre/post ARCHITECTURE_CUTOVER, instead of live docker logs")
    args = parser.parse_args()

    if args.persist:
        persist(args.container)
        return

    if args.report_history:
        render_history()
        return

    if args.watch:
        import time
        try:
            while True:
                print("\033c", end="")  # clear terminal between refreshes
                timings, turn_durations, status_counts = parse(fetch_logs(args.container, args.since))
                render(timings, turn_durations, status_counts)
                print(f"(refreshing every {args.watch}s - Ctrl+C to stop)")
                time.sleep(args.watch)
        except KeyboardInterrupt:
            pass
    else:
        timings, turn_durations, status_counts = parse(fetch_logs(args.container, args.since))
        render(timings, turn_durations, status_counts)


if __name__ == "__main__":
    main()

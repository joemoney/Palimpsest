#!/usr/bin/env python3
"""Server-only performance dashboard: parses `docker logs palimpsest-web` for the
per-call [TIMING] lines (backend/story_engine.py's _timed() wrapper - narration,
state_update, subplot_generation, act_advancement_check, end_story_final_arc,
summary_rollover) and the gunicorn access log's /api/turn and /api/regenerate lines,
and reports latency stats for each.

Deliberately NOT a Flask route - this reads `docker logs` directly and needs shell/
docker access to the host, so it's only reachable by whoever can already open a
terminal on the server. Never wire this into app.py; a web route on the same process
that's tunneled to the internet would expose it to every player. See CLAUDE.md's
"Web UI" section for the play app's actual routes, none of which this touches.

Only covers whatever `docker logs` currently retains for the container - a container
recreation (not just `docker compose restart`) clears that history, so this reports
recent performance, not a durable long-term record. Uses only the standard library,
so it runs on the host's system Python with no project dependencies installed.

Usage:
  python3 scripts/perf_dashboard.py                 # one-shot report, full retained log
  python3 scripts/perf_dashboard.py --since 2h       # only the last 2 hours (docker logs --since syntax)
  python3 scripts/perf_dashboard.py --watch 30       # re-run and reprint every 30s
"""
import argparse
import re
import statistics
import subprocess
import sys

TIMING_RE = re.compile(r"\[TIMING\] (\S+): ([\d.]+)s")
ACCESS_RE = re.compile(r'"(GET|POST) (\S+) HTTP/\S+"\s+(\d+)\s+([\d.]+)s')
TURN_PATHS = ("/api/turn", "/api/regenerate")


def fetch_logs(container: str, since: str | None) -> str:
    cmd = ["docker", "logs", container]
    if since:
        cmd += ["--since", since]
    result = subprocess.run(cmd, capture_output=True, text=True)
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


def parse(log_text: str):
    timings = {}
    turn_durations = []
    status_counts = {}
    for line in log_text.splitlines():
        m = TIMING_RE.search(line)
        if m:
            label, seconds = m.group(1), float(m.group(2))
            timings.setdefault(label, []).append(seconds)
            continue
        m = ACCESS_RE.search(line)
        if m and any(p in m.group(2) for p in TURN_PATHS):
            status, seconds = m.group(3), float(m.group(4))
            turn_durations.append(seconds)
            status_counts[status] = status_counts.get(status, 0) + 1
    return timings, turn_durations, status_counts


def _print_table(rows, headers):
    widths = [
        max(len(str(r[i])) for r in ([headers] + rows)) for i in range(len(headers))
    ]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print(fmt.format(*["-" * w for w in widths]))
    for r in rows:
        print(fmt.format(*r))


def render(log_text: str):
    timings, turn_durations, status_counts = parse(log_text)

    print("=" * 72)
    print(f"Palimpsest performance dashboard  ({sum(len(v) for v in timings.values())} timed calls, "
          f"{len(turn_durations)} turn/regenerate requests)")
    print("=" * 72)

    if timings:
        print("\nPer-call LLM latency (seconds), by label:\n")
        rows = []
        for label in sorted(timings, key=lambda l: -sum(timings[l])):
            s = _stats(timings[label])
            rows.append([
                label, s["count"], f"{s['min']:.2f}", f"{s['mean']:.2f}",
                f"{s['p50']:.2f}", f"{s['p90']:.2f}", f"{s['max']:.2f}",
            ])
        _print_table(rows, ["label", "count", "min", "mean", "p50", "p90", "max"])
    else:
        print("\nNo [TIMING] lines found in the retained logs.")

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


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--container", default="palimpsest-web", help="container name (default: palimpsest-web)")
    parser.add_argument("--since", default=None, help="only logs since this time, e.g. '2h', '30m' (docker logs --since syntax)")
    parser.add_argument("--watch", type=int, default=None, metavar="SECONDS", help="re-run and reprint every SECONDS")
    args = parser.parse_args()

    if args.watch:
        import time
        try:
            while True:
                print("\033c", end="")  # clear terminal between refreshes
                render(fetch_logs(args.container, args.since))
                print(f"(refreshing every {args.watch}s - Ctrl+C to stop)")
                time.sleep(args.watch)
        except KeyboardInterrupt:
            pass
    else:
        render(fetch_logs(args.container, args.since))


if __name__ == "__main__":
    main()

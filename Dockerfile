FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# UID/GID 1000 matches the typical first non-root host user, so files this
# container creates under bind-mounted data/ (saves, accounts.db, lock files)
# come out owned by that host user instead of root.
RUN groupadd --gid 1000 appuser \
    && useradd --uid 1000 --gid appuser --create-home --shell /bin/bash appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# --pythonpath (not --chdir) so app.py/state_store.py/etc. under backend/ import cleanly
# while cwd stays /app (the repo root) - state_store.py's STORIES_DIR/DATA_DIR are plain
# relative paths ("stories"/"data") that need cwd to stay at the repo root to resolve.
# --timeout is deliberately longer than story_engine.py's own worst-case call chain: a
# failed primary call (OPENROUTER_TOTAL_TIMEOUT=100s or GOOGLE_TOTAL_TIMEOUT=60s, whichever
# tier's provider) followed by the Gemini fail-safe retry (another GOOGLE_TOTAL_TIMEOUT=60s)
# tops out at 160s. If gunicorn's own timeout were equal to or shorter than that combined
# figure, a slow-but-real double-timeout could hit gunicorn's harder SIGABRT before
# story_engine's own clean timeout handling ever gets a chance to run, killing the worker
# mid-request with no response sent to the client and the turn's state never saved.
# --access-logfile - (stdout, captured by `docker logs`) plus %(L)s (request duration in
# seconds) in the format string - there was previously no access log at all, only error-level
# output, so a hung/slow request left no trace unless it happened to crash outright.
CMD ["gunicorn", "--pythonpath", "backend", "--bind", "0.0.0.0:8000", "--workers", "3", \
     "--timeout", "220", "--access-logfile", "-", \
     "--access-logformat", "%(t)s %(h)s \"%(r)s\" %(s)s %(L)ss", "app:app"]

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
CMD ["gunicorn", "--pythonpath", "backend", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "120", "app:app"]

#!/usr/bin/env bash
# Restarts the `web` gunicorn service (palimpsest-web). Needed after any app.py/
# templates/state_store.py change: gunicorn loads the app once at boot and the
# bind-mounted source (docker-compose.yml's `.:/app`) doesn't trigger a reload on
# its own. story-engine isn't touched - it reloads its own module fresh on every
# CLI invocation, so it never needs restarting for a code change.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

docker compose restart web
docker compose logs web --tail 20

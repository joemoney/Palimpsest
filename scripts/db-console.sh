#!/usr/bin/env bash
# Opens an interactive sqlite3 console against data/accounts.db (the accounts
# table - see CLAUDE.md's "Multi-User, Multi-Story Architecture"). Story saves
# under data/saves/ are plain JSON files, not part of this database.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

sqlite3 -header -column data/accounts.db

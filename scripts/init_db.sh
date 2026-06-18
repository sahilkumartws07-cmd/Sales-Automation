#!/usr/bin/env bash
set -euo pipefail

psql -v ON_ERROR_STOP=1 -f "$(dirname "$0")/init_db.sql"


#!/bin/bash
# Ahavah pytest runner.
# Run via: ./tests/run.sh                       (all tests)
#         ./tests/run.sh tests/test_smoke.py   (one file)
#         ./tests/run.sh -k smoke              (filter by name)
#
# Installs test-only deps inside the api container, then runs pytest.
# Idempotent — re-runs reuse already-installed deps.

set -e

if [ -z "$INSIDE_CONTAINER" ]; then
  # Outside the container: re-exec inside the running api container.
  exec docker compose exec -T -e INSIDE_CONTAINER=1 api bash /app/tests/run.sh "$@"
fi

cd /app

if ! python -c 'import pytest' 2>/dev/null; then
  echo "Installing test deps..."
  pip install --no-cache-dir -r tests/requirements-test.txt
fi

PYTHONPATH=/app exec python -m pytest "$@"

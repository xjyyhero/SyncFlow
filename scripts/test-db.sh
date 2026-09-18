#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
mkdir -p output/db-tests
rm -f output/db-tests/test-results.json
# Separate project, temporary MySQL storage, no development credentials or volumes.
trap 'docker compose -f compose.test.yaml down' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
docker compose -f compose.test.yaml up --build --attach db-tests --abort-on-container-exit --exit-code-from db-tests

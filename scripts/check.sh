#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
backend/.venv/bin/ruff check --config backend/pyproject.toml backend/app backend/tests scripts/smoke.py scripts/review-db.py scripts/export-openapi.py scripts/acceptance-e2e.py scripts/package-release.py
backend/.venv/bin/ruff format --check --config backend/pyproject.toml backend/app backend/tests scripts/smoke.py scripts/review-db.py scripts/export-openapi.py scripts/acceptance-e2e.py scripts/package-release.py
PYTHONPATH=backend backend/.venv/bin/python -m unittest discover -s backend/tests -p test_csv_source.py
npm --prefix frontend run format:check
node --test frontend/src/api.test.ts
npm --prefix frontend run build
docker compose config --quiet
docker compose -f compose.test.yaml config --quiet
backend/.venv/bin/python scripts/export-openapi.py --check

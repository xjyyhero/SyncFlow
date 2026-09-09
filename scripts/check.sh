#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
backend/.venv/bin/ruff check --config backend/pyproject.toml backend/app scripts/smoke.py
backend/.venv/bin/ruff format --check --config backend/pyproject.toml backend/app scripts/smoke.py
npm --prefix frontend run format:check
npm --prefix frontend run build
docker compose config --quiet

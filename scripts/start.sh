#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
if [ ! -f .env ]; then
  (umask 077; cp .env.example .env)
fi
docker compose up --build --wait --wait-timeout 180
printf '\nWeb: http://localhost:5173\nAPI docs: http://localhost:8000/docs\n'

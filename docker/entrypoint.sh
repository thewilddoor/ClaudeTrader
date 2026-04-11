#!/bin/bash
# Run bootstrap on first deploy (idempotent: skips if agent already exists),
# then hand off to the scheduler.
set -e

mkdir -p /app/state /app/logs/sessions /app/logs/errors

echo "Running bootstrap..."
python -m scheduler.bootstrap

echo "Starting scheduler..."
exec python -m scheduler.main

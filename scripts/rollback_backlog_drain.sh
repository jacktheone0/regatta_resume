#!/usr/bin/env bash
# Roll `main` back to the state before the backlog-drain changes
# (branch rollback/pre-backlog-drain).
#
# Run this from a checkout of the CURRENT main -- the database downgrade
# needs migration 006 present in the code. Nothing is pushed: the final
# `git push --force-with-lease origin main` is printed for you to run.
set -euo pipefail

: "${DATABASE_URL:?Set DATABASE_URL to the Neon connection string first}"

cd "$(git rev-parse --show-toplevel)"

echo "1/3 Downgrading the database to revision 005"
echo "    (drops clubspot_regatta_checks and the run-counter columns on scraper_logs;"
echo "     regattas, sailors and results are untouched)..."
FLASK_APP=app.py flask db downgrade 005_add_scraper_log_entries

echo "2/3 Resetting local main to rollback/pre-backlog-drain..."
git fetch origin main rollback/pre-backlog-drain
git checkout main
git reset --hard origin/rollback/pre-backlog-drain

echo "3/3 Done locally. To publish the rollback (this rewrites main):"
echo "    git push --force-with-lease origin main"
echo "Render then redeploys the old code, and the workflow on main is back on the weekly schedule."

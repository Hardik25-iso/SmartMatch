#!/bin/sh
set -e

echo "==> Waiting for MySQL at ${DB_HOST}..."
until python -c "import os, pymysql; pymysql.connect(host=os.environ['DB_HOST'], user=os.environ['DB_USER'], password=os.environ['DB_PASSWORD'])" 2>/dev/null; do
  echo "    ...database not ready yet, retrying in 3s"
  sleep 3
done
echo "==> MySQL is up."

# Ensure tables exist WITHOUT destroying data. Do NOT run `flask create-db`
# (it calls db.drop_all()) or `flask seed-db` here — that would wipe all
# accounts, pledges and NGO records on every container restart.
# create_all() is idempotent: it only creates tables that are missing.
echo "==> Ensuring database tables exist..."
python -c "from app import app, db; app.app_context().push(); db.create_all()"

# Flag listings nobody has confirmed in 30+ days. Safe to repeat: it only updates
# the stale flags and never touches last_updated or any other data.
echo "==> SmartMatch: flagging stale listings..."
flask smartmatch-scan || echo "    (scan skipped; tables may be empty on a first run)"

# First-time setup only, run manually once:
#   docker compose exec web flask seed-db          # demo accounts + 10 Pune NGOs (WIPES data)
#   docker compose exec web flask smartmatch-seed  # synthetic donors, NGOs and history
#   docker compose exec web flask smartmatch-train # train the ranking model
# Then daily, from host cron:
#   docker compose exec -T web flask smartmatch-scan --alert

echo "==> Starting gunicorn on :8000"
exec gunicorn --bind 0.0.0.0:8000 --workers 3 --timeout 120 app:app

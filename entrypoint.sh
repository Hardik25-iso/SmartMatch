#!/bin/sh
set -e

echo "==> Waiting for MySQL at ${DB_HOST}..."
until python -c "import os, pymysql; pymysql.connect(host=os.environ['DB_HOST'], user=os.environ['DB_USER'], password=os.environ['DB_PASSWORD'])" 2>/dev/null; do
  echo "    ...database not ready yet, retrying in 3s"
  sleep 3
done
echo "==> MySQL is up."

# Create the schema and load deterministic demo data.
# NOTE: this rebuilds the demo dataset on every start, so the demo always has
# the same known accounts (donor@example.com / password: 11) and seeded orphanages.
echo "==> Creating tables..."
flask create-db
echo "==> Seeding demo data..."
flask seed-db

echo "==> Starting gunicorn on :8000"
exec gunicorn --bind 0.0.0.0:8000 --workers 3 --timeout 120 app:app

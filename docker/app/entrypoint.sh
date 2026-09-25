#!/bin/bash
# Migrate, then run gunicorn (Django) and nginx side by side. If either one
# exits, the container exits too, so Docker's restart policy can bring it back.
set -euo pipefail
cd /app

# Named/bind-mounted volumes can arrive owned by root.
chown -R www-data:www-data /app/media /app/log

# The database container may still be starting - retry for about a minute.
for attempt in $(seq 1 30); do
  if python manage.py migrate --noinput; then
    break
  fi
  if [ "$attempt" -eq 30 ]; then
    echo "entrypoint: migrate still failing after 30 attempts - giving up" >&2
    exit 1
  fi
  echo "entrypoint: migrate failed (database not ready?) - retrying in 2s ($attempt/30)" >&2
  sleep 2
done

gunicorn meadery.wsgi:application \
  --bind 127.0.0.1:8000 \
  --workers "${GUNICORN_WORKERS:-3}" \
  --user www-data --group www-data \
  --access-logfile - --error-logfile - &
gunicorn_pid=$!

nginx -g 'daemon off;' &
nginx_pid=$!

# `docker stop` sends SIGTERM to this script (PID 1): pass it on.
trap 'kill -TERM "$gunicorn_pid" "$nginx_pid" 2>/dev/null' TERM INT

wait -n "$gunicorn_pid" "$nginx_pid"
status=$?
echo "entrypoint: gunicorn or nginx exited ($status) - stopping the container" >&2
kill -TERM "$gunicorn_pid" "$nginx_pid" 2>/dev/null || true
exit "$status"

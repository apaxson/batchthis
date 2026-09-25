# syntax=docker/dockerfile:1
#
# Batch This - Django (gunicorn) behind nginx, on Ubuntu LTS.
#
# Build (from the repo root - the build context is the whole project):
#   docker build -f docker/app/Dockerfile -t batchthis-app .
#
# Run (with the database from docker/postgres on the same network):
#   docker run -d --name batchthis-app --network batchthis -p 8080:80 \
#     -e POSTGRES_HOST=batchthis-db -e POSTGRES_PASSWORD=... \
#     -e DJANGO_SECRET_KEY=... -e DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1 \
#     -v batchthis-media:/app/media batchthis-app
#
# Environment (all read by meadery/settings.py):
#   DJANGO_SECRET_KEY            required in production
#   DJANGO_ALLOWED_HOSTS         comma-separated host names the site is reached by
#   DJANGO_CSRF_TRUSTED_ORIGINS  e.g. https://cellar.example.com, when behind HTTPS/another proxy
#   DJANGO_DEBUG                 false by default in this image
#   POSTGRES_HOST/PORT/DB/USER/PASSWORD   PostgreSQL; without POSTGRES_HOST it falls back to SQLite
#   GUNICORN_WORKERS             default 3

ARG UBUNTU_VERSION=26.04
FROM ubuntu:${UBUNTU_VERSION}

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    DJANGO_SETTINGS_MODULE=meadery.settings \
    DJANGO_DEBUG=false \
    DJANGO_STATIC_ROOT=/app/staticfiles

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv nginx ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && rm -f /etc/nginx/sites-enabled/default

WORKDIR /app

# Dependencies first, so code changes don't re-install them.
COPY requirements.txt .
RUN python3 -m venv "$VIRTUAL_ENV" \
 && pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

COPY . .
COPY docker/app/nginx.conf /etc/nginx/conf.d/batchthis.conf
COPY docker/app/entrypoint.sh /usr/local/bin/entrypoint.sh

# The logging config writes ./log/batchthis/debug.log, so the folder must exist
# before settings load. collectstatic needs no database; the key is a throwaway.
RUN chmod +x /usr/local/bin/entrypoint.sh \
 && mkdir -p log/batchthis media staticfiles \
 && DJANGO_SECRET_KEY=collectstatic-only python manage.py collectstatic --noinput \
 && chown -R www-data:www-data log media

EXPOSE 80
VOLUME ["/app/media"]
# Healthy = gunicorn is listening and nginx serves a static file. (A Django page would
# answer 400 unless 127.0.0.1 is in DJANGO_ALLOWED_HOSTS.)
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s \
  CMD python3 -c "import socket, urllib.request; socket.create_connection(('127.0.0.1', 8000), 4).close(); urllib.request.urlopen('http://127.0.0.1/static/batchthis/css/cellar-ledger.css', timeout=4)" || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

# syntax=docker/dockerfile:1
#
# Batch This - PostgreSQL database.
#
# Build:
#   docker build -t batchthis-db docker/postgres
#
# Run (the app container reaches it by its name, batchthis-db, on this network):
#   docker network create batchthis
#   docker run -d --name batchthis-db --network batchthis \
#     -e POSTGRES_PASSWORD=... \
#     -v batchthis-pgdata:/var/lib/postgresql batchthis-db
#
# Data lives in the volume. PostgreSQL 18 keeps it under /var/lib/postgresql/18/docker,
# so mount the volume at /var/lib/postgresql (not .../data as with older versions).
# Add -p 5432:5432 only if you want to reach it from the host (e.g. psql, dumpdata/loaddata).

FROM postgres:18

# Match the app's defaults in meadery/settings.py (POSTGRES_DB / POSTGRES_USER).
# POSTGRES_PASSWORD deliberately has no default: pass it at `docker run`.
ENV POSTGRES_DB=batchthis \
    POSTGRES_USER=batchthis \
    TZ=UTC

EXPOSE 5432

HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=5 \
  CMD pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" || exit 1

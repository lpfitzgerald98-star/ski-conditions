#!/bin/sh
set -e

# Seed the persistent volume on first boot only.
#
# The volume starts empty. The image carries a snapshot of ski.db (1.2M raw
# observations, ~90MB) purely so a fresh deploy has history to grade against
# instead of re-fetching six upstream APIs for 79 mountains.
#
# The `-f` guard is the whole point: on every subsequent deploy the volume already
# holds the live DB -- with fresher observations and a warm score cache -- and must
# NOT be clobbered by the build-time snapshot.
DB="${SKI_DB_PATH:-/data/ski.db}"
DB_DIR="$(dirname "$DB")"
mkdir -p "$DB_DIR"

if [ ! -f "$DB" ]; then
    if [ -f /app/seed/ski.db ]; then
        echo "seeding $DB from image snapshot"
        cp /app/seed/ski.db "$DB"
    else
        echo "no seed DB in image; starting with an empty database"
    fi
else
    echo "existing database at $DB, leaving it alone"
fi

# WAL puts -wal and -shm sidecars next to the DB. They must live on the SAME
# filesystem as the DB itself, which they do -- both under the volume mount.
exec "$@"

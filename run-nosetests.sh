#!/bin/sh
#set -x
set -e

export FLASK_APP="ingest"
export SECRET_KEY="$(dd if=/dev/urandom bs=12 count=1 status=none | xxd -p -c 12)"

if [ -f "${POSTGIS_PASS_FILE}" ]; then
    export POSTGIS_PASS="$(cat ${POSTGIS_PASS_FILE})"
fi

if [ -f "${DB_PASS_FILE}" ]; then
    export DB_PASS="$(cat ${POSTGIS_PASS_FILE})"
fi

if [ -f "${GEOSERVER_PASS_FILE}" ]; then
    export GEOSERVER_PASS="$(cat ${GEOSERVER_PASS_FILE})"
fi
export DATABASE_URI="${DB_ENGINE}://${DB_USER}:${DB_PASS}@${DB_HOST}:${DB_PORT}/${DB_NAME}"

# Initialize database

flask init-db

# Run

exec nosetests $@

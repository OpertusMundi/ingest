#!/bin/sh
set -u -e -o pipefail

[[ "${DEBUG:-f}" != "f" || "${XTRACE:-f}" != "f" ]] && set -x

# Check environment

python_version="$(python3 -c 'import platform; print(platform.python_version())' | cut -d '.' -f 1,2)"
if [ "${python_version}" != "${PYTHON_VERSION}" ]; then
    echo "PYTHON_VERSION (${PYTHON_VERSION}) different with version reported from python3 executable (${python_version})" 1>&2 && exit 1
fi

if [ ! -f "${SECRET_KEY_FILE}" ]; then
    echo "SECRET_KEY_FILE does not exist!" 1>&2 && exit 1
fi

if [ -z "${POSTGIS_USER}" ]; then
    echo "POSTGIS_USER is not set!" 1>&2 && exit 1
fi

if [ ! -f "${POSTGIS_PASS_FILE}" ]; then
    echo "POSTGIS_PASS_FILE does not exist!" 1>&2 && exit 1
fi

if [ -z "${GEOSERVER_USER}" ]; then
    echo "GEOSERVER_USER is not set!" 1>&2 && exit 1
fi

if [ ! -f "${GEOSERVER_PASS_FILE}" ]; then
    echo "GEOSERVER_PASS_FILE does not exist!" 1>&2 && exit 1
fi

if [ ! -f "${LOGGING_FILE_CONFIG}" ]; then
    echo "LOGGING_FILE_CONFIG (configuration for Python logging) does not exist!" 1>&2
    exit 1
fi

if [ ! -f "${DB_PASS_FILE}" ]; then
    echo "DB_PASS_FILE does not exist!" 1>&2 && exit 1
fi

logging_file_config=${LOGGING_FILE_CONFIG}
if [ -n "${LOGGING_ROOT_LEVEL}" ]; then
    logging_file_config="logging-$(echo ${HOSTNAME}| md5sum| head -c10).conf"
    sed -e "/^\[logger_root\]/,/^\[.*/ { s/^level=.*/level=${LOGGING_ROOT_LEVEL}/ }" ${LOGGING_FILE_CONFIG} \
        > ${logging_file_config}
fi

export FLASK_APP="ingest"
export SECRET_KEY="$(cat ${SECRET_KEY_FILE} | tr -d '\n')"
export POSTGIS_PASS="$(cat ${POSTGIS_PASS_FILE} | tr -d '\n')"
export GEOSERVER_PASS="$(cat ${GEOSERVER_PASS_FILE} | tr -d '\n')"
DB_PASS="$(cat ${DB_PASS_FILE} | tr -d '\n')"
export DATABASE_URI="${DB_ENGINE}://${DB_USER}:${DB_PASS}@${DB_HOST}:${DB_PORT}/${DB_NAME}"

# Initialize database

flask init-db

# Configure and start WSGI server

if [ "${FLASK_ENV}" == "development" ]; then
    # Run a development server
    exec /usr/local/bin/wsgi.py
fi

num_workers="${NUM_WORKERS:-4}"
server_port="5000"

gunicorn_ssl_options=
if [ -n "${TLS_CERTIFICATE}" ] && [ -n "${TLS_KEY}" ]; then
    gunicorn_ssl_options="--keyfile ${TLS_KEY} --certfile ${TLS_CERTIFICATE}"
    server_port="5443"
fi

exec gunicorn --log-config ${logging_file_config} --access-logfile - \
  --workers ${num_workers} \
  --bind "0.0.0.0:${server_port}" ${gunicorn_ssl_options} \
  ingest.app:app

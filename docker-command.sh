#!/bin/bash 

source /venv/bin/activate

set -u -e -o pipefail

[[ "${XTRACE:-false}" != "false" ]] && set -x

# Check environment

if [ ! -f "${SECRET_KEY_FILE}" ]; then
    echo "SECRET_KEY_FILE does not exist!" 1>&2 && exit 1
fi
export SECRET_KEY="$(cat ${SECRET_KEY_FILE} | tr -d '\n')"

if [ ! -f "${LOGGING_FILE_CONFIG}" ]; then
    echo "LOGGING_FILE_CONFIG (configuration for Python logging) does not exist!" 1>&2
    exit 1
fi

logging_file_config=${LOGGING_FILE_CONFIG}
if [ -n "${LOGGING_ROOT_LEVEL}" ]; then
    logging_file_config="logging-$(echo ${HOSTNAME}| md5sum| head -c10).conf"
    sed -e "/^\[logger_root\]/,/^\[.*/ { s/^level=.*/level=${LOGGING_ROOT_LEVEL}/ }" ${LOGGING_FILE_CONFIG} \
        > ${logging_file_config}
fi

# Initialize database

if [[ "${INITIALIZE_DATABASE:-false}" != "false" ]]; then 
    flask init-db
fi

# Configure and start WSGI server

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

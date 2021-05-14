# vim: set syntax=dockerfile:

# see https://github.com/OpertusMundi/docker-library/blob/gdal-python-builder/gdal-python-builder/Dockerfile
FROM opertusmundi/gdal-python-builder:1-3.1 as build-stage-1

FROM osgeo/gdal:alpine-normal-3.1.0
ARG VERSION

LABEL language="python"
LABEL framework="flask"
LABEL usage="ingest microservice for rasters and vectors"

RUN apk update && \
  apk add --no-cache sqlite openssl postgresql-dev curl py3-yaml py3-numpy py3-psycopg2 py3-sqlalchemy

ENV VERSION="${VERSION}"
ENV PYTHON_VERSION="3.8"
ENV PYTHONPATH="/usr/local/lib/python${PYTHON_VERSION}/site-packages"

RUN addgroup flask && adduser -h /var/local/ingest -D -G flask flask

COPY --from=build-stage-1 /usr/local/ /usr/local/

RUN mkdir /usr/local/ingest/

WORKDIR /usr/local/ingest

COPY setup.py requirements.txt requirements-production.txt /usr/local/ingest/
RUN pip3 install --upgrade pip \
  && pip3 install --no-cache-dir --prefix=/usr/local -r requirements.txt -r requirements-production.txt

COPY ingest /usr/local/ingest/ingest
RUN cd /usr/local/ingest && python setup.py install --prefix=/usr/local && python setup.py clean -a

COPY wsgi.py docker-command.sh /usr/local/bin/
RUN chmod a+x /usr/local/bin/wsgi.py /usr/local/bin/docker-command.sh

WORKDIR /var/local/ingest

RUN mkdir ./logs && chown flask:flask ./logs
COPY --chown=flask logging.conf .

ENV FLASK_APP="ingest" \
    FLASK_ENV="production" \
    FLASK_DEBUG="false" \
    LOGGING_FILE_CONFIG="logging.conf" \
    LOGGING_ROOT_LEVEL="" \
    INSTANCE_PATH="/var/local/ingest/data" \
    DATA_DIR="/var/local/ingest/data" \
    TEMP_DIR="" \
    INPUT_DIR="/var/local/ingest/input" \
    SECRET_KEY_FILE="/var/local/ingest/secret_key" \
    TLS_CERTIFICATE="" \
    TLS_KEY=""

USER flask
CMD ["/usr/local/bin/docker-command.sh"]

EXPOSE 5000
EXPOSE 5443

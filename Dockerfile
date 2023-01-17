# vim: set syntax=dockerfile:

FROM opertusmundi/ingest-base:0.3 AS build-stage-1
# or ... (see base.dockerfile)
#FROM continuumio/miniconda3:4.10.3 AS build-stage-1
#COPY conda-env.yml /environment.yml
#RUN conda env create -n env1
#RUN conda install -q -y -c conda-forge conda-pack 

WORKDIR /usr/local/ingest
COPY setup.py requirements.txt requirements-production.txt ./
RUN conda install -c conda-forge -q -y -n env1 --file requirements-production.txt 
COPY ingest ./ingest
RUN conda run -n env1 pip3 install --root-user-action=ignore .

RUN conda-pack -n env1 -o /tmp/env1.tar && \
  mkdir /venv && tar xf /tmp/env1.tar -C /venv && \
  rm /tmp/env1.tar

RUN /venv/bin/conda-unpack


FROM debian:11.2-slim 

COPY --from=build-stage-1 /venv /venv

RUN groupadd flask && useradd -g flask -m -d /var/local/ingest flask

COPY docker-command.sh /
RUN chmod a+x /docker-command.sh

WORKDIR /var/local/ingest

RUN mkdir ./logs && chown flask:flask ./logs
COPY --chown=flask logging.conf .

ENV FLASK_APP="ingest" \
    FLASK_ENV="production" \
    FLASK_DEBUG="false" \
    NUM_WORKERS="4" \
    LOGGING_FILE_CONFIG="logging.conf" \
    LOGGING_ROOT_LEVEL="" \
    INSTANCE_PATH="/var/local/ingest/data" \
    DATA_DIR="/var/local/ingest/data" \
    TEMP_DIR="" \
    INPUT_DIR="/var/local/ingest/input" \
    SECRET_KEY_FILE="/secrets/secret_key" \
    SQLALCHEMY_POOL_SIZE="4" \
    INITIALIZE_DATABASE="true" \
    DATABASE_PASS_FILE="secrets/database-password" \
    DATABASE_URL="postgresql://postgres:5432/opertusmundi-ingest" \
    DATABASE_USER="opertusmundi" \
    GEODATA_SHARDS="" \
    GEOSERVER_DATASTORE="{database}.{schema}" \
    GEOSERVER_DEFAULT_WORKSPACE="work_1" \
    GEOSERVER_USER="admin" \
    GEOSERVER_PASS_FILE="secrets/geoserver/admin-password" \
    GEOSERVER_URL="http://geoserver:8080/geoserver" \
    GEOSERVER_PORT_MAP="" \
    POSTGIS_DEFAULT_SCHEMA="public" \
    POSTGIS_URL="postgresql://geoserver-postgis:5432/geodata" \
    POSTGIS_PORT_MAP="" \
    POSTGIS_USER="geodata" \
    POSTGIS_PASS_FILE="secrets/postgis/geodata-password" \
    TLS_CERTIFICATE="" \
    TLS_KEY="" 

USER flask
CMD ["/docker-command.sh"]

EXPOSE 5000
EXPOSE 5443


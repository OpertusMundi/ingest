# Ingest micro-service

[![Build Status](https://ci.dev-1.opertusmundi.eu:9443/api/badges/OpertusMundi/ingest/status.svg?ref=refs/heads/master)](https://ci.dev-1.opertusmundi.eu:9443/OpertusMundi/ingest)

## Description

The purpose of this package is to deploy a micro-service which ingests a KML or ShapeFile into a PostGIS capable PostgreSQL database and publish an associated layer to GeoServer.
The service is built on *flask* and *sqlite*, GeoPandas and GeoAlchemy are used for the database ingestion and pyCurl to communicate with GeoServer REST API.

## Installation

The package requires at least Python 3.7. First, install *sqlite* and *pyculr*, e.g. for Debian:
```
apt-get install sqlite3 python3-pycurl
```
To install with **pip**:
```
pip install git+https://github.com/OpertusMundi/ingest.git
```
Initialize sqlite database by running:
```
flask init-db
```

The following environment variables should be set:
- `FLASK_ENV`: `development` or `production`.
- `FLASK_APP`: `ingest` (if running as a container, this will be always set).
- `DATABASE`: (optional) The path to the SQLite database holding state of this microservice (default is `.data/ingest.sqlite`). 
- `POSTGIS_HOST`: The PostGIS host to use for ingesting spatial datasets.
- `POSTGIS_PORT`: The PostGIS host.
- `POSTGIS_DB_NAME`: The database to use on the PostGIS server.
- `POSTGIS_DB_SCHEMA`: The table schema to use on the PostGIS database.
- `POSTGIS_USER`: The username for the PostGIS user.
- `POSTGIS_PASS`: The password for the PostGIS user.
- `GEOSERVER_URL`: GeoServer base URL.
- `GEOSERVER_WORKSPACE` (optional): GeoServer workspace to use. It will be created if not exists. If not set, the default workspace will be used instead.
- `GEOSERVER_STORE`: GeoServer PostGIS data store name. It will be created if not exists.
- `GEOSERVER_USER`: GeoServer user name.
- `GEOSERVER_PASS`: The password for the GeoServer user.
- `TEMP_DIR` (optional): The location for storing temporary files. If not set, the system temporary path location will be used.
- `CORS`: List or string of allowed origins
- `LOGGING_CONFIG_FILE`: The logging configuration file.
- `LOGGING_ROOT_LEVEL` (optional): The level of detail for the root logger; one of `DEBUG`, `INFO`, `WARNING`.
- `INPUT_DIR`: The input directory; all input paths will be resolved under this directory. 

A development server could be started with:
```
flask run
```

## Usage

You can browse the full [OpenAPI documentation](https://opertusmundi.github.io/ingest/).

The main endpoints `/ingest` and `/publish` are accessible via POST requests. Each such request is associated with a request ticket, and optionally by a idempotency-key set by the request (the value of the request header `X-Idempotence-Key`).

For the case of ingestion, the response can be `prompt` or `deferred`, set by the corresponding value `response` in the request body. In case of `prompt` response the service should promptly initiate the ingestion process and wait to finish in order to return the response, whereas in the `deferred` case a response is sent immediately without waiting for the process to finish. In any case, one could request `/status/{ticket}` in order to get the status of the process corresponding to a specific ticket or `/result/{ticket}` to retrieve the table information that the vector file was ingested into.

Furthermore, the associated ticket of an idempotene-key could be retrieved with the request `/ticket_by_key/{key}`.

Once deployed, the OpenAPI JSON is served by the index of the service.


## Build and run as a container

Copy `.env.example` to `.env` and configure if needed (e.g `FLASK_ENV` variable).

Copy `compose.yml.example` to `compose.yml` (or `docker-compose.yml`) and adjust to your needs (e.g. specify volume source locations etc.). You will at least need to configure the network (inside `compose.yml`) to attach to.

For example, you can create a private network named `opertusmundi_network`:

    docker network create --attachable opertusmundi_network

Build:

    docker-compose -f compose.yml build

Prepare the following files/directories:

   * `./data/ingest.sqlite`:  the SQLite database (an empty database, if running for first time)
   * `./secrets/secret_key`: file needed (by Flask) for signing/encrypting session data
   * `./secrets/postgis/ingest-password`: file containing the password for the PostGIS database user
   * `./secrets/geoserver/ingest-password`: file containing the password for the Geoserver user
   * `./logs`: a directory to keep logs under
   * `./temp`: a directory to be used as temporary storage
   * `./input`: a directory to be used as the root of input files

Start application:

    docker-compose -f compose.yml up


## Run tests

Copy `compose-testing.yml.example` to `compose-testing.yml` and adjust to your needs. This is a just a docker-compose recipe for setting up the testing container.

Build testing container:

    docker-compose -f compose-testing.yml build

Run nosetests (in an ephemeral container):

    docker-compose -f compose-testing.yml run --rm --user "$(id -u):$(id -g)" nosetests -v


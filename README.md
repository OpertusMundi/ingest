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
- `POSTGIS_HOST`: PostgreSQL Host Server.
- `POSTGIS_PORT`: PostgreSQL Port.
- `POSTGIS_DB_NAME`: PostgreSQL database to use.
- `POSTGIS_DB_SCHEMA`: PostgreSQL active schema.
- `POSTGIS_USER`: PostgreSQL user name.
- `POSTGIS_PASS`: The password for the PostgreSQL user.
- `GEOSERVER_URL`: GeoServer base URL.
- `GEOSERVER_WORKSPACE` (optional): GeoServer workspace to use. It will be created if not exists. If not set, the default workspace will be used instead.
- `GEOSERVER_STORE`: GeoServer PostGIS data store name. It will be created if not exists.
- `GEOSERVER_USER`: GeoServer user name.
- `GEOSERVER_PASS`: The password for the GeoServer user.
- `TEMPDIR` (optional): The location for storing temporary files. If not set, the system temporary path location will be used.
- `CORS`: List or string of allowed origins
- `LOGGING_CONFIG_FILE`: The logging configuration file.
- `LOGGING_ROOT_LEVEL` (optional): The level of detail for the root logger; one of `DEBUG`, `INFO`, `WARNING`.

A development server could be started with:
```
flask run
```

## Usage

The main endpoint `/ingest` is accessible via a POST request and expects the following parameters:
- `resource` (required): A string representing the spatial file resolvable path **or** a stream containing the spatial file.
- `response`: `prompt` (default) or `deferred`.

In case of `prompt` response the service should promptly initiate the transformation process and wait to finish in order to return the response, whereas in the `deferred` case a response is sent immediately without waiting for the process to finish. In latter case, one could request `/status/\<ticket\>` in order to get the status of the process corresponding to a specific ticket or `/endpoints/\<ticket\>` to retrieve the GeoServer services endpoints associated with the specific ticket.

Once deployed, info about the endpoints and their possible HTTP parameters could be obtained by requesting the index of the service, i.e. for development environment http://localhost:5000.


## Build and run as a container

Copy `.env.example` to `.env` and configure if needed (e.g `FLASK_ENV` variable).

Copy `compose.yml.example` to `compose.yml` (or `docker-compose.yml`) and adjust to your needs (e.g. specify volume source locations etc.).

Build:

    docker-compose -f compose.yml build

Prepare the following files/directories:

   * `./data/ingest.sqlite`:  the SQLite database (an empty database, if running for first time)
   * `./secrets/secret_key`: file needed (by Flask) for signing/encrypting session data
   * `./secrets/postgis/password`: file containing the password for the PostGIS database user
   * `./secrets/geoserver/password`: file containing the password for the Geoserver user
   * `./logs`: a directory to keep logs under
   * `./temp`: a directory to be used as temporary storage

Start application:
    
    docker-compose -f compose.yml up


## Run tests

Copy `compose-testing.yml.example` to `compose-testing.yml` and adjust to your needs. This is a just a docker-compose recipe for setting up the testing container.

Run nosetests (in an ephemeral container):

    docker-compose -f compose-testing.yml run --rm --user "$(id -u):$(id -g)" nosetests -v


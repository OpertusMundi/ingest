# Ingest micro-service

[![Build Status](https://ci.dev-1.opertusmundi.eu:9443/api/badges/OpertusMundi/ingest/status.svg?ref=refs/heads/master)](https://ci.dev-1.opertusmundi.eu:9443/OpertusMundi/ingest)

## Description

The purpose of this package is to deploy a micro-service which ingests a KML or ShapeFile into a PostGIS capable PostgreSQL database and publish an associated layer to GeoServer.
The service is built on *flask* and *SQLAlchemy*, GeoPandas and GeoAlchemy are used for the database ingestion and pyCurl to communicate with GeoServer REST API.

## Installation

The package requires at least Python 3.8. First, install and *pycurl*, e.g. for Debian:
```
apt-get install python3-pycurl
```

To install:
```
pip install -r requirements.txt
python setup.py install
```

Initialize database:
```
flask init-db
```

The service understands the following environment variables:
- `FLASK_ENV`: `development` or `production`.
- `FLASK_APP`: `ingest` (if running as a container, this will be always set).
- `SECRET_KEY`: A random string used to encrypt cookies
- `LOGGING_CONFIG_FILE`: The logging configuration file.
- `LOGGING_ROOT_LEVEL` (optional): The level of detail for the root logger; one of `DEBUG`, `INFO` (default), `WARNING`.
- `CORS`: List or string of allowed origins (`*` by default)
- `INPUT_DIR`: The input directory; all input paths will be resolved under this directory. 
- `TEMP_DIR` (optional): The location for temporary files. If not set, the system temporary path location will be used.
- `INSTANCE_PATH`: The location where a Flask instance keeps runtime data
- `SQLALCHEMY_POOL_SIZE`: The size of the connection pool for the database (`4`, by default)
- `DATABASE_URL`: An SQLAlchemy-friendly connection URL for the database, e.g. `postgresql://postgres-1:5432/opertusmundi-ingest`
- `DATABASE_USER`: The username for the database
- `DATABASE_PASS_FILE`: The file containing the password for the database
- `GEODATA_SHARDS`: (optional) A comma-separated list of shard identifiers, e.g. `s1,s2`. If sharding is not used, this variable should be empty
- `POSTGIS_DEFAULT_SCHEMA`: The default database schema for a PostGis store backend (`public`, if not given)
- `POSTGIS_USER`: The username for a PostGis store backend (common for all shards, if sharding is used)
- `POSTGIS_PASS_FILE`: The file containing the password for a PostGis store backend (common for all shards, if sharding is used) 
- `POSTGIS_URL`: An SQLAlchemy-friendly connection URL for the PostGis store backend, e.g. `postgresql://geoserver-postgis-0:5432/geodata`. If sharding is used, this URL is a template that may use the following variables:
    * `shard`: the shard identifier
    * `port`: A port for the service on the selected shard (see also `POSTGIS_PORT_MAP`)
 
  An example: `postgresql://geoserver-{shard}-postgis-0:{port}/geodata`
- `POSTGIS_PORT_MAP`: (optional for sharding) A comma-separated list of shard-to-port mappings of the form `shard:port`. An example: `s1:31523,s2:32500`

- `GEOSERVER_DATASTORE`: The Geoserver datastore. It can be a template string that may use the following variables:
     * `database`: The database name as specified in PostGis connection URL
     * `schema`: The database schema (same as workspace)
  The default is: `{database}.{schema}`
- `GEOSERVER_DEFAULT_WORKSPACE`: A default workspace for Geoserver
- `GEOSERVER_URL`: The Geoserver base URL e.g. `http://geoserver-1:8080/geoserver`. If sharding is used, this URL is a template that may use the following variables:
     * `shard`: the shard identifier
     * `port`: the service on the selected shard (see also `GEOSERVER_PORT_MAP`)
     
  An example: `http://geoserver-{shard}-0:{port}/geoserver`
- `GEOSERVER_PORT_MAP`:  (optional for sharding) A comma-separated list of shard-to-port mappings of the form `shard:port`. An example: `s1:31319,s2:31329`
- `GEOSERVER_USER`: The username for Geoserver's REST API (common for all shards, if sharding is used)
- `GEOSERVER_PASS_FILE`: The file containing the password for Geoserver's REST API (common for all shards, if sharding is used)


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

Copy `.env.example` to `.env` and configure.

Copy `compose.yml.example` to `compose.yml` and adjust to your needs (e.g. specify volume name for input directory etc.).

For example, you can create a private network named `opertusmundi_network`:

    docker network create --attachable opertusmundi_network

Build:

    docker-compose -f compose.yml build

Prepare the following files/directories:

   * `./secrets/secret_key`: file needed (by Flask) for signing/encrypting session data
   * `./secrets/postgis/geodata-password`: file containing the password for the PostGIS database user
   * `./secrets/geoserver/admin-password`: file containing the password for the Geoserver admin user
   * `./secrets/database-password`: file containing the password for the main database user
   * `./logs`: a directory to keep logs under
   * `./temp`: a directory to be used as temporary storage

Start application:

    docker-compose -f compose.yml up


## Run tests *FIXME*

Copy `compose-testing.yml.example` to `compose-testing.yml` and adjust to your needs. This is a just a docker-compose recipe for setting up the testing container.

Build testing container:

    docker-compose -f compose-testing.yml build

Run nosetests (in an ephemeral container):

    docker-compose -f compose-testing.yml run --rm --user "$(id -u):$(id -g)" nosetests -v


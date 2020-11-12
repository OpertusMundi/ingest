# Ingest micro-service
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
- **FLASK_ENV**: *development* or *production*.
- **FLASK_APP**: *ingest*.
- **DB_HOST**: PostgreSQL Host Server.
- **DB_PORT**: PostgreSQL Port.
- **DB_NAME**: PostgreSQL database to use.
- **DB_SCHEMA**: PostgreSQL active schema.
- **DB_USER**: PostgreSQL user name.
- **DB_PASS**: PostgreSQL user password.
- **GS_URL**: GeoServer base URL.
- (optional) **GS_WORKSPACE**: GeoServer workspace to use. It will be created if not exists. If not set, the default workspace will be used instead.
- **GS_STORE**: GeoServer PostGIS data store name. It will be created if not exists.
- **GS_USER**: GeoServer user name.
- **GS_PASS** GeoServer user password.
- (optional) **TEMPDIR**: The location for storing temporary files. If not set, the system temporary path location will be used.
- (optional) **CORS**: List or string of allowed origins. Default: \*.

A development server could be started with:
```
flask run
```

## Usage
The main endpoint */ingest* is accessible via a **POST** request and expects the following parameters:
- **resource** (required): A string representing the spatial file resolvable path **or** a stream containing the spatial file.
- **response**: *Prompt* (default) or *deferred*.

In case of **prompt** response the service should promptly initiate the transformation process and wait to finish in order to return the response, whereas in the **deferred** case a response is sent immediately without waiting for the process to finish. In latter case, one could request */status/\<ticket\>* in order to get the status of the process corresponding to a specific ticket or */endpoints/\<ticket\>* to retrieve the GeoServer services endpoints associated with the specific ticket.

Once deployed, info about the endpoints and their possible HTTP parameters could be obtained by requesting the index of the service, i.e. for development environment http://localhost:5000.
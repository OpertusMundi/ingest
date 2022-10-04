from flask import Flask
from flask import request, current_app, make_response, g
from werkzeug.utils import secure_filename
from werkzeug.exceptions import HTTPException, InternalServerError
from flask_cors import CORS
from os import path, getenv, environ, makedirs, unlink
import sys
from shutil import rmtree
import tempfile
from uuid import uuid4
from hashlib import md5
from datetime import datetime, timezone
from flask_executor import Executor
from apispec import APISpec
from apispec_webframeworks.flask import FlaskPlugin
import zipfile
import tarfile
import json
import distutils.util
import sqlalchemy

from .database import db
from .database.model import Queue
from .database.actions import db_queue, db_update_queue_status
from .postgres import Postgres
from .geoserver import Geoserver
from .logging import mainLogger, accountingLogger, exception_as_rfc5424_structured_data
from .forms import IngestForm, PublishForm

#
# Helpers
#

def _makeDir(path):
    """Creates recursively the path, ignoring warnings for existing directories."""
    try:
        makedirs(path)
    except OSError:
        pass

def _getTempDir():
    """Return the temporary directory"""
    return getenv('TEMP_DIR') or tempfile.gettempdir()

def databaseUrlFromEnv():
    database_url = sqlalchemy.engine.url.make_url(environ['DATABASE_URL'])
    
    username = environ['DATABASE_USER']
    password = None
    if 'DATABASE_PASS' in environ: 
        password = environ['DATABASE_PASS']
    elif 'DATABASE_PASS_FILE' in environ:
        with open(environ['DATABASE_PASS_FILE'], "r") as f: password = f.read().strip();
    else:
        raise RuntimeError("missing password for database connection (DATABASE_PASS or DATABASE_PASS_FILE)")
    
    return database_url.set(username=username, password=password);

def _getWorkingPath(ticket):
    """Returns the working directory for each request."""
    return path.join(_getTempDir(), __name__, ticket)

def _checkDirectoryWritable(d):
    fd, fname = tempfile.mkstemp(None, None, d)
    unlink(fname);

def _checkConnectToPostgis():
    global postgis
    global geodata_shards
    if geodata_shards:
        for s in geodata_shards:
            url = postgis.check(s);
            mainLogger.debug('_checkConnectToPostgis(): Connected to shard [%s]: %r', s, url)
    else:
        url = postgis.check()
        mainLogger.debug('_checkConnectToPostgis(): Connected to %r', url)

def _checkConnectToGeoserver():
    global geoserver
    global geodata_shards
    if geodata_shards:
        for s in geodata_shards:
            url = geoserver.check(s)
            mainLogger.debug('_checkConnectToGeoserver(): Connected to shard [%s]: %s', s, url)
    else:
        url = geoserver.check()
        mainLogger.debug('_checkConnectToGeoserver(): Connected to %s', url)

def _checkConnectToDB():
    engine = sqlalchemy.create_engine(database_url)
    with engine.connect() as conn:
        conn.execute('SELECT 1')
    mainLogger.debug("_checkConnectToDB(): Connected to %r", database_url)

def _executorCallback(future):
    """The callback function called when a job has been completed."""
    ticket, result, success, error_msg, rows = future.result()
    record = db_update_queue_status(ticket, completed=True, success=success, result=result, error_msg=error_msg, rows=rows)
    accountingLogger(ticket=ticket, success=success, execution_start=record.initiated, execution_time=record.execution_time, comment=error_msg, rows=rows)


#
# Initialize spec for OpenAPI documentation
#
spec = APISpec(
    title="Ingest/Publish API",
    version=getenv('VERSION'),
    info=dict(
        description="A microservice to ingest geospatial (KML/Shapefile/CSV) resource into a PostGIS database and then publish an associated layer to GeoServer.",
        contact={"email": "pmitropoulos@getmap.gr"}
    ),
    externalDocs={"description": "GitHub", "url": "https://github.com/OpertusMundi/ingest"},
    openapi_version="3.0.2",
    plugins=[FlaskPlugin()],
)

geodata_shards = [s1 for s1 in (s.strip() for s in environ.get("GEODATA_SHARDS", '').split(",")) if s1];

postgis = Postgres.makeFromEnv();

geoserver = Geoserver.makeFromEnv();

# Initialize app

database_url = databaseUrlFromEnv();
input_dir = environ['INPUT_DIR'];

app = Flask(__name__, instance_relative_config=True, instance_path=getenv('INSTANCE_PATH'))
app.config.from_mapping(
    SECRET_KEY=environ['SECRET_KEY'],
    SQLALCHEMY_DATABASE_URI=str(database_url),
    SQLALCHEMY_ENGINE_OPTIONS={'pool_size': int(environ.get('SQLALCHEMY_POOL_SIZE', '4')), 'pool_pre_ping': True},
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    JSON_SORT_KEYS=False,
    EXECUTOR_TYPE="thread",
    EXECUTOR_MAX_WORKERS="1"
);

# Ensure the instance folder exists and initialize application, db and executor.

_makeDir(app.instance_path)
db.init_app(app)
executor = Executor(app)

#Enable CORS
if getenv('CORS') is not None:
    if getenv('CORS')[0:1] == '[':
        origins = json.loads(getenv('CORS'))
    else:
        origins = getenv('CORS')
    cors = CORS(app, origins=origins)

# Register cli commands
with app.app_context():
    import ingest.cli

def _prepareSession():
    """Prepares session.
    Returns:
        (dict): Dictionary with session info.
    """
    idempotency_key = request.headers.get('X-Idempotency-Key')
    queue = db_queue(idempotency_key=idempotency_key, request=request.endpoint)

    session = {'ticket': queue['ticket'], 'idempotency_key': idempotency_key, 'initiated': queue['initiated']}

    return session

def enqueue(src_file, ticket, tablename, schema, shard=None, replace=None, **kwargs):
    """Enqueue a transform job (in case requested response type is 'deferred')."""
    mainLogger.info("Processing ticket %s (%s)", ticket, src_file)
    try:
        result = _ingest(src_file, ticket, tablename, schema, shard, replace=replace, **kwargs)
    except Exception as e:
        return (ticket, None, 0, str(e), None)
    rows = result.pop('length')
    return (ticket, json.dumps(result), 1, None, rows)

@app.after_request
def _afterRequest(response):
    """Log request.
    Log only POST requests. If request has been deferred, the queue job is responsible for logging.
    """
    
    if request.method != 'POST' or response.status_code in [202, 400, 500]:
        return response

    ticket = g.session['ticket']
    request_time = g.session['initiated']
    execution_time = round((datetime.now(timezone.utc).astimezone() - request_time).total_seconds(), 3)
    if response.status_code != 200:
        comment = response.get_data(as_text=True)
        result = None
        rows = None
        success = False
        accountingLogger(ticket=ticket, success=False, execution_start=request_time, execution_time=execution_time, comment=comment)
    else:
        response_json = response.json
        result = json.dumps(response_json)
        success = True
        comment = None
        rows = response_json.pop('length') if 'length' in response_json else None
        accountingLogger(ticket=ticket, success=True, execution_start=request_time, execution_time=execution_time, rows=rows)
    
    db_update_queue_status(ticket, completed=True, success=success, result=result, error_msg=comment, rows=rows)
    
    return response

@app.teardown_request
def cleanTempFiles(error=None):
    """Cleans the temp directory."""
    if hasattr(g, 'response_type') and hasattr(g, 'ticket') and g.response_type == 'prompt':
        working_path = _getWorkingPath(g.ticket)
        try:
            rmtree(working_path)
        except Exception as e:
            pass

@app.route("/")
def index():
    """The index route, gives info about the API endpoints."""
    mainLogger.info('Generating the OpenAPI document...')
    return make_response(spec.to_dict(), 200)

@app.route("/_health")
def healthCheck():
    """Perform basic health checks
    ---
    get:
      tags:
      - Health
      summary: Get health status
      description: 'Get health status'
      operationId: 'getHealth'
      responses:
        default:
          description: An object with status information
          content:
            application/json:
              schema:
                type: object
                properties:
                  status:
                    type: string
                    description: A status of 'OK' or 'FAILED'
                    enum: ["OK", "FAILED"]
                  reason:
                    type: string
                    description: the reason of failure (if failed)
                  detail:
                    type: string
                    description: more details on this failure (if failed)
              examples:
                example-1:
                  status: "OK"
    """

    mainLogger.info('Performing health checks...')
    # Check that temp directory is writable
    try:
        _checkDirectoryWritable(_getTempDir())
    except Exception as exc:
        return make_response({'status': 'FAILED', 'reason': 'temp directory not writable', 'detail': str(exc)}, 200)
    # Check that we can connect to Database
    try:
        _checkConnectToDB()
    except Exception as exc:
        return make_response({'status': 'FAILED', 'reason': 'cannot connect to Database backend', 'detail': str(exc)}, 200)
    # Check that we can connect to our PostGIS backend
    try:
        _checkConnectToPostgis()
    except Exception as exc:
        return make_response({'status': 'FAILED', 'reason': 'cannot connect to PostGIS backend', 'detail': str(exc)}, 200)
    # Check that we can connect to our Geoserver backend
    try:
        _checkConnectToGeoserver()
    except Exception as exc:
        return make_response({'status': 'FAILED', 'reason': 'cannot connect to GeoServer REST API.', 'detail': str(exc)}, 200)
    return make_response({'status': 'OK'}, 200)

@app.route("/ingest", methods=["POST"])
def ingest():
    """The ingest endpoint.
    ---
    post:
      summary: Ingest a vector file (Shapefile/KML) into PostGIS.
      tags:
        - Ingest
      parameters:
        - in: header
          name: X-Idempotency-Key
          description: Associates the request with an Idempotency Key (it has to be unique).
          schema:
            type: string
            format: uuid
          required: false
      requestBody:
        required: true
        content:
          multipart/form-data:
            schema:
              type: object
              properties:
                resource:
                  type: string
                  format: binary
                  description: The vector file.
                response:
                  type: string
                  enum: [prompt, deferred]
                  default: prompt
                  description: Determines whether the proccess should be promptly initiated (*prompt*) or queued (*deferred*). In the first case, the response waits for the result, in the second the response is immediate returning a ticket corresponding to the request.
                table:
                  type: string
                  description: The name of the (new) table into which the data will be ingested
                workspace:
                  type: string
                  description: The workspace determines the database schema
                shard:
                  type: string
                  description: The shard identifier (if any)
                replace:
                  type: boolean
                  description: If true and table already exists, data will replace existing data
                  default: false
                encoding:
                  type: string
                  description: File encoding.
                crs:
                  type: string
                  description: CRS of the dataset.
              required:
                - resource
                - table
                - workspace
          application/x-www-form-urlencoded:
            schema:
              type: object
              properties:
                resource:
                  type: string
                  description: The vector file resolvable path.
                response:
                  type: string
                  enum: [prompt, deferred]
                  default: prompt
                  description: Determines whether the proccess should be promptly initiated (*prompt*) or queued (*deferred*). In the first case, the response waits for the result, in the second the response is immediate returning a ticket corresponding to the request.
                table:
                  type: string
                  description: The name of the (new) table into which the data will be ingested
                workspace:
                  type: string
                  description: The workspace determines the database schema
                shard:
                  type: string
                  description: The shard identifier (if any)
                replace:
                  type: boolean
                  description: If true and table already exists, data will replace existing data
                  default: false
                encoding:
                  type: string
                  description: File encoding.
                crs:
                  type: string
                  description: CRS of the dataset.
              required:
                - resource
                - table
                - workspace
      responses:
        200:
          description: Ingestion completed.
          content:
            application/json:
              schema:
                type: object
                properties:
                  schema:
                    type: string
                    description: The database schema of the created table.
                    example: "work_1"
                  table:
                    type: string
                    description: The name of the created table.
                    example: "corfu_pois"
                  length:
                    type: integer
                    description: The number of features stored in the table.
                    example: 539
                  type:
                    type: string
                    description: The response type as requested.
                    example: "prompt"
        202:
          description: Accepted for processing, but ingestion has not been completed.
          content:
            application/json:
              schema:
                type: object
                properties:
                  ticket:
                    type: string
                    description: The ticket corresponding to the request.
                    example: "5d530de91ae5f265329efe38c97ac931"
                  status:
                    type: string
                    description: The *status* endpoint to poll for the status of the request.
                    example: "/status/5d530de91ae5f265329efe38c97ac931"
                  type:
                    type: string
                    description: The response type as requested.
                    example: "deferred"
          links:
            GetStatus:
              operationId: getStatus
              parameters:
                ticket: '$response.body#/ticket'
              description: The `ticket` value returned in the response can be used as the `ticket` parameter in `GET /status/{ticket}`.
        400:
          description: Encountered a validation error
          content:
            application/json:
              schema:
                type: object
                properties:
                  error:
                    type: string
                    description: A error message for the entire request
                  errors:
                    type: object
                    description: A map of validation errors keyed on a request parameter
                    additionalProperties:
                      type: array
                      items:
                        type: string
                example: { "errors": { "crs": [ "Field must be a valid CRS" ] } }
        403:
          description: Insufficient privilege for writing in the database schema.
    """

    form = IngestForm(**request.form)
    if not form.validate():
        return make_response({ 'errors': form.errors }, 400)

    # Form the source full path of the uploaded file
    if request.values.get('resource') is not None:
        src_file = path.join(environ['INPUT_DIR'], form.resource)
    else:
        try:
            resource = request.files['resource']
        except KeyError:
            return make_response({ 'errors' : { 'resource': [ 'expected a file upload field' ] } }, 400)
        if resource is None:
            mainLogger.info('Client error: %s', 'resource not uploaded')
            return make_response({ 'errors' : { 'resource': [ 'file was not uploaded' ] } }, 400)

    session = _prepareSession()
    g.session = session

    replace = distutils.util.strtobool(form.replace) if not isinstance(form.replace, bool) else form.replace
    read_options = {opt: getattr(form, opt) for opt in ['encoding', 'crs'] if getattr(form, opt) is not None}

    ticket = session['ticket']
    mainLogger.info("Starting {} request with ticket {}.".format(form.response, ticket))

    if request.values.get('resource') is None:
        # Create tmp directory and store the uploaded file.
        working_path = _getWorkingPath(ticket)
        src_path = path.join(working_path, 'src')
        _makeDir(src_path)
        src_file = path.join(src_path, secure_filename(resource.filename))
        resource.save(src_file)

    table_name = form.table
    schema = form.workspace
    shard = form.shard    

    if form.response == 'prompt':
        g.response_type = 'prompt'
        try:
            result = _ingest(src_file, ticket, table_name, schema, shard, replace=replace, **read_options)
        except Exception as e:
            return make_response({ 'error': str(e) }, 400)
        return make_response({**result, "type": form.response}, 200)
    else:
        g.response_type = 'deferred'
        future = executor.submit(enqueue, 
            src_file, ticket, table_name, schema, shard, replace=replace, **read_options)
        future.add_done_callback(_executorCallback)
        return make_response({"ticket": ticket, "status": "/status/{}".format(ticket), "type": form.response}, 202)

@app.route("/publish", methods=["POST"])
def publish():
    """The ingest endpoint.
    ---
    post:
      summary: Publishes a layer to GeoServer from PostGIS table.
      tags:
        - Publish
      parameters:
        - in: header
          name: X-Idempotency-Key
          schema:
            type: string
            format: uuid
          required: false
      requestBody:
        required: true
        content:
          application/x-www-form-urlencoded:
            schema:
              type: object
              properties:
                table:
                  type: string
                  description: The table name.
                workspace:
                  type: string
                  description: The workspace in which this layer will be created. The workspace also determines the database schema for the table
                shard:
                  type: string
                  description: The shard identifier (if any)
              required:
                - table
                - workspace
      responses:
        200:
          description: Publication completed.
          content:
            application/json:
              schema:
                type: object
                properties:
                  wms:
                    type: string
                    description: WMS endpoint
                  wfs:
                    type: string
                    description: WFS endpoint
        400:
          description: Encountered a validation error
          content:
            application/json:
              schema:
                type: object
                properties:
                  error:
                    type: string
                    description: A error message for the entire request
                  errors:
                    type: object
                    description: A map of validation errors keyed on a request parameter
                    additionalProperties:
                      type: array
                      items:
                        type: string
                example: { "errors": { "table": [ "Table name cannot be empty" ] } }
    """
    
    global postgis
    global geoserver
    
    form = PublishForm(**request.form)
    if not form.validate():
        return make_response({ 'errors': form.errors }, 400)
    
    table_name = form.table
    schema = form.workspace
    workspace = form.workspace
    shard = form.shard
    
    if not postgis.checkIfTableExists(table_name, schema, shard):
        err_message = 'The specified table [{0}] is expected to be found in schema [{1}] (on shard [{2}])'.format(
            table, schema, shard) 
        return make_response({ 'error': err_message }, 400)
    
    ows_service_endpoints = _getGeoserverServiceEndpoints(workspace, table_name)
    
    if geoserver.checkIfLayerExists(workspace, table_name, shard):
        return make_response(ows_service_endpoints, 200)

    g.session = _prepareSession()

    try:
        _publishTable(table_name, schema, workspace, shard)
    except Exception as e:
        mainLogger.error("Failed to publish table \"%s\".\"%s\" on Geoserver workspace [%s] on shard [%s]: %s", 
            schema, table_name, workspace, shard or '', str(e))
        return make_response({ 'error': str(e) }, 500)
    
    return make_response(ows_service_endpoints, 200)

@app.route("/ingest", methods=["DELETE"])
def drop():
    """Remove all ingested data relative to the given table.
    ---
    delete:
      summary: Drop PostGis table created from a previous ingest operation
      description: Drop PostGis table created from a previous ingest operation
      tags:
        - Ingest
      parameters:
        - name: table
          in: query
          description: The table from which the layer originated from (same as layer name)
          required: true
          schema:
            type: string
        - name: workspace
          in: query
          description: The workspace that the layer belongs; if not present, the default workspace will be assumed.
          required: true
          schema:
            type: string
        - name: shard
          in: query
          description: The shard identifier (if any)
          required: false
          schema:
            type: string
      responses:
        204:
          description: Table dropped (if existed).
        400:
          description: Cannot drop table because a layer depends on it
    """
    
    global geoserver
    global postgis

    table = request.values['table']
    workspace = request.values['workspace']
    schema = workspace
    shard = request.values.get('shard')
   
    if not postgis.checkIfTableExists(table, schema, shard):
        return make_response('', 204)

    if geoserver.checkIfLayerExists(workspace, table, shard):
        err_message = 'Cannot drop table {0}.{1} (on shard [{2}]) because a layer depends on that table'.format(
            schema, table, shard or '')
        return make_response({ 'error': err_message }, 400)
    
    try:
        postgis.dropTable(table, schema, shard)
    except Exception as e:
        return make_response({ 'error': str(e) }, 500)
    
    return '', 204

@app.route("/publish", methods=["DELETE"])
def unpublish():
    """Unpublish a GeoServer layer.
    ---
    delete:
      summary: Unpublish a GeoServer layer.
      description: Removes both the layer and feature type from GeoServer.
      tags:
        - Publish
      parameters:
        - name: table
          in: query
          description: The table from which the layer originated from (same as layer name)
          required: true
          schema:
            type: string
        - name: workspace
          in: query
          description: The workspace that the layer belongs; if not present, the default workspace will be assumed.
          required: true
          schema:
            type: string
        - name: shard
          in: query
          description: The shard identifier (if any)
          required: false
          schema:
            type: string
      responses:
        204:
          description: Layer unpublished (if existed).
    """
    
    table = request.values['table']
    workspace = request.values['workspace']
    schema = workspace
    shard = request.values.get('shard')
    
    try:
        _unpublishTable(table, schema, workspace, shard)
    except Exception as e:
        return make_response({ 'error': str(e) }, 500)
    return '', 204

@app.route("/status/<ticket>")
def status(ticket):
    """Get the status of a specific ticket.
    ---
    get:
      summary: Get the status of a request.
      operationId: getStatus
      description: Returns the status of a request corresponding to a specific ticket.
      tags:
        - Status
      parameters:
        - name: ticket
          in: path
          description: The ticket of the request
          required: true
          schema:
            type: string
      responses:
        200:
          description: Ticket found and status returned.
          content:
            application/json:
              schema:
                type: object
                properties:
                  completed:
                    type: boolean
                    description: Whether ingestion/publication process has been completed or not.
                  success:
                    type: boolean
                    description: Whether the process completed succesfully.
                  comment:
                    type: string
                    description: If ingestion/publication has failed, a short comment describing the reason.
                  requested:
                    type: string
                    format: datetime
                    description: The timestamp of the request.
                  executionTime:
                    type: integer
                    description: The execution time in seconds.
        404:
          description: Ticket not found
        400:
          description: The given ticket is either missing or not valid
    """
    if ticket is None:
        return make_response({ 'error': 'Ticket is missing' }, 400)
    queue = Queue().get(ticket=ticket)
    if queue is None:
        return make_response({ 'error': 'No job found for ticket [{0}]'.format(ticket) }, 404)
    info = {
        "completed": queue['completed'],
        "success": queue['success'],
        "requested": queue['initiated'].isoformat(),
        "executionTime": queue['execution_time'],
        "comment": queue['error_msg'],
    }
    return make_response(info, 200)

@app.route("/result/<ticket>")
def result(ticket):
    """Get the result associated with a specific ingest ticket.
    ---
    get:
      summary: Get the result of the ingest.
      description: Returns the table in PostGIS resulted from a ingestion request corresponding to a specific ticket.
      tags:
        - Result
      parameters:
        - name: ticket
          in: path
          description: The ticket of the request
          required: true
          schema:
            type: string
      responses:
        200:
          description: The resulted endpoints.
          content:
            application/json:
              schema:
                type: object
                properties:
                  schema:
                    type: string
                    description: The schema of the created table.
                  table:
                    type: string
                    description: The name of the created table.
                  length:
                    type: integer
                    description: The number of features stored in the table.
        404:
          description: Ticket not found or ingest has not been completed.
        400:
          description: The given ticket is either missing or not valid
    """

    if ticket is None:
        return make_response({ 'error': 'Ticket is missing' }, 400)
    queue = Queue().get(ticket=ticket)
    if queue is None:
        return make_response({ 'error': 'No job found for ticket [{0}]'.format(ticket) }, 404)

    return make_response({**json.loads(queue['result']), 'length': queue['rows']}, 200)

@app.route("/ticket_by_key/<key>")
def getTicketByKey(key):
    """Get a request ticket associated with an idempotent key.
    ---
    get:
      summary: Returns a request ticket associated with an idempotent key.
      tags:
        - Ticket
      parameters:
        - name: key
          in: path
          description: The idempotent key as sent in X-Idempotency-Key header.
          required: true
          schema:
            type: string
      responses:
        200:
          description: The associated request and ticket.
          content:
            application/json:
              schema:
                type: object
                properties:
                  ticket:
                    type: string
                    description: The associated ticket.
                  request:
                    type: string
                    enum: [ingest, publish]
                    description: The request of this ticket.
        404:
          description: Idempotent key not found.
    """
    
    queue = Queue().get(idempotency_key=key)
    if queue is None:
        return make_response({ 'error': 'No job found for key [{0}]'.format(key) }, 404)
    
    return make_response({'ticket': queue['ticket'], 'request': queue['request']}, 200)

with app.test_request_context():
    spec.path(view=ingest)
    spec.path(view=publish)
    spec.path(view=status)
    spec.path(view=result)
    spec.path(view=healthCheck)
    spec.path(view=getTicketByKey)
    spec.path(view=drop)
    spec.path(view=unpublish)


def _ingest(src_file, ticket, tablename, schema, shard=None, replace=False, **kwargs):
    """Ingest file content to PostgreSQL and publish to geoserver.

    Parameters:
        src_file (str): full path to source file.
        ticket (str): ticket of the request
        tablename (str): target table name
        schema (str): database schema.
        shard (str): shard indentifier, or None if no sharding is used
        replace (bool, optional): if True, the table will be replaced if it exists.
        **kwargs: additional arguments for GeoPandas read file.

    Returns:
        (dict) Schema, table name and length.
    """
    
    global postgis
    
    # Check if source file is compressed
    working_path = _getWorkingPath(ticket)
    src_path = path.join(working_path, 'extracted')
    if tarfile.is_tarfile(src_file):
        handle = tarfile.open(src_file)
        handle.extractall(src_path)
        src_file = src_path
        handle.close()
    elif zipfile.is_zipfile(src_file):
        with zipfile.ZipFile(src_file, 'r') as handle:
            handle.extractall(src_path)
        src_file = src_path
    
    try:
        result = postgis.ingest(src_file, tablename, schema, shard, replace=replace, **kwargs)
    except Exception as e:
        mainLogger.error("Failed to ingest %s into table \"%s\".\"%s\" on shard [%s]: %s", 
            src_file, schema, tablename, shard or '', str(e))
        raise e

    try:
        rmtree(working_path)
    except Exception as e:
        pass
    
    return dict(zip(('schema', 'table', 'length'), result))

def _getGeoserverServiceEndpoints(workspace, layer):
    """Form GeoServer WMS/WFS endpoints.

    Parameters:
        workspace (str): GeoServer workspace
        layer (str): Layer name

    Returns:
        (dict) The GeoServer layer endpoints.
    """
    return {
        "wms": '{0}/wms?service=WMS&request=GetMap&layers={0}:{1}'.format(workspace, layer),
        "wfs": '{0}/ows?service=WFS&request=GetFeature&typeName={0}:{1}'.format(workspace, layer)
    }

def _publishTable(table, schema, workspace, shard=None):
    """Publishes the contents of a PostGis table to Geoserver"""
    global geoserver
    global postgis
    
    database_url = postgis.urlFor(shard);
    datastore = geoserver.datastoreName(database_url, schema, shard)
    
    geoserver.createWorkspaceIfNotExists(workspace, shard)
    geoserver.createDatastoreIfNotExists(datastore, workspace, database_url, schema, shard)
    geoserver.publish(workspace, datastore, table, shard)

def _unpublishTable(table, schema, workspace, shard=None):
    """Unpublish layer (derived from PostGis table) from Geoserver"""
    global geoserver
    global postgis
    
    database_url = postgis.urlFor(shard);
    datastore = geoserver.datastoreName(database_url, schema, shard)
    
    geoserver.unpublish(workspace, datastore, table, shard)

#
# Exception handlers
#

# Define a catch-all exception handler that simply logs a proper error message.
# Note: If actual error handling is needed, consider defining handlers targeting
#   more specific exception types (derived from Exception).
@app.errorhandler(Exception)
def handleAnyError(ex):
    exc_message = str(ex)
    mainLogger.error("Unexpected error: %s", exc_message, extra=exception_as_rfc5424_structured_data(ex))
    # Convert and return an HTTPException (is a valid response object for Flask)
    if isinstance(ex, HTTPException):
        return ex
    else:
        return InternalServerError(exc_message)

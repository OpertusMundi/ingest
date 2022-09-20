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
from sqlalchemy import create_engine

from .database import db
from .database.model import Queue
from .database.actions import db_queue, db_update_queue_status
from .postgres import Postgres, SchemaException, InsufficientPrivilege
from .geoserver import Geoserver
from .logging import mainLogger, accountingLogger, exception_as_rfc5424_structured_data
from .forms import IngestForm, PublishForm


def _makeDir(path):
    """Creates recursively the path, ignoring warnings for existing directories."""
    try:
        makedirs(path)
    except OSError:
        pass

def _getTempDir():
    """Return the temporary directory"""
    return getenv('TEMP_DIR') or tempfile.gettempdir()

def _getWorkingPath(ticket):
    """Returns the working directory for each request."""
    return path.join(_getTempDir(), __name__, ticket)

def _checkDirectoryWritable(d):
    fd, fname = tempfile.mkstemp(None, None, d)
    unlink(fname);

def _checkConnectToPostgis():
    postgres = Postgres()
    engine_url = postgres.check()
    mainLogger.debug('_checkConnectToPostgis(): Connected to %s' % (engine_url))

def _checkConnectToGeoserver():
    gs = Geoserver()
    mainLogger.debug('_checkConnectToGeoserver(): Using REST API at %s' % (gs.rest_url))
    gs_url = gs.check()
    mainLogger.debug('_checkConnectToGeoserver(): Connected to %s' % (gs_url))

def _checkConnectToDB():
    url = environ['DATABASE_URI']
    engine = create_engine(url)
    conn = engine.connect()
    conn.execute('SELECT 1')
    mainLogger.debug("_checkConnectToDB(): Connected to %s", engine.url)

def _executorCallback(future):
    """The callback function called when a job has been completed."""
    ticket, result, success, error_msg, rows = future.result()
    record = db_update_queue_status(ticket, completed=True, success=success, result=result, error_msg=error_msg, rows=rows)
    accountingLogger(ticket=ticket, success=success, execution_start=record.initiated, execution_time=record.execution_time, comment=error_msg, rows=rows)

def _ingestIntoPostgis(src_file, ticket, tablename=None, schema=None, replace=False, **kwargs):
    """Ingest file content to PostgreSQL and publish to geoserver.

    Parameters:
        src_file (string): Full path to source file.
        ticket (string): The ticket of the request that will be also used as table and layer name.
        tablename (string): The resulted table name (default: ticket)
        schema (string, optional): Database schema.
        replace (bool, optional): If True, the table will be replace if it exists.
        **kwargs: Additional arguments for GeoPandas read file.

    Returns:
        (dict) Schema, table name and length.
    """
    # Create tablename, schema
    tablename = tablename or ticket
    schema = schema or getenv('POSTGIS_DB_SCHEMA')
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
    # Ingest
    postgres = Postgres()
    result = postgres.ingest(src_file, tablename, schema=schema, replace=replace, **kwargs)
    try:
        rmtree(working_path)
    except Exception as e:
        pass
    return dict(zip(('schema', 'table', 'length'), result))

def _geoserver_endpoints(workspace, layer):
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

def _publishTable(table, schema=None, workspace=None):
    """Publishes the contents of a PostGIS table to GeoServer.
    Parameters:
        table (string): The table name
    """
    geoserver = Geoserver()
    workspace = workspace or getenv('GEOSERVER_WORKSPACE')
    if workspace is not None:
        geoserver.createWorkspace(workspace)
    store = dict(
        name=getenv('GEOSERVER_STORE'),
        workspace=workspace,
        pg_db=getenv('POSTGIS_DB_NAME'),
        pg_user=getenv('POSTGIS_USER'),
        pg_password=getenv('POSTGIS_PASS'),
        pg_host=getenv('POSTGIS_HOST'),
        pg_port=getenv('POSTGIS_PORT'),
        pg_schema=schema or getenv('POSTGIS_DB_SCHEMA')
    )
    geoserver.createStore(**store)
    geoserver.publish(store=store['name'], table=table, workspace=workspace)

# Read (required) environment parameters
for variable in [
    'POSTGIS_HOST', 'POSTGIS_USER', 'POSTGIS_PASS', 'POSTGIS_PORT', 'POSTGIS_DB_NAME', 'POSTGIS_DB_SCHEMA',
    'GEOSERVER_URL', 'GEOSERVER_USER', 'GEOSERVER_PASS', 'GEOSERVER_STORE', 'INPUT_DIR', 'DATABASE_URI'
]:
    if getenv(variable) is None:
        mainLogger.fatal('Environment variable not set [variable="%s"]', variable)
        sys.exit(1)


# Initialize OpenAPI documentation
spec = APISpec(
    title="Ingest/Publish API",
    version=getenv('VERSION'),
    info=dict(
        description="A simple service to ingest a KML or ShapeFile into a PostGIS capable PostgreSQL database and publish an associated layer to GeoServer.",
        contact={"email": "pmitropoulos@getmap.gr"}
    ),
    externalDocs={"description": "GitHub", "url": "https://github.com/OpertusMundi/ingest"},
    openapi_version="3.0.2",
    plugins=[FlaskPlugin()],
)

# Initialize app
app = Flask(__name__, instance_relative_config=True, instance_path=getenv('INSTANCE_PATH'))
app.config.from_mapping(
    SECRET_KEY=getenv('SECRET_KEY'),
    SQLALCHEMY_DATABASE_URI=environ['DATABASE_URI'],
    SQLALCHEMY_ENGINE_OPTIONS={'pool_size': int(environ.get('SQLALCHEMY_POOL_SIZE', '4')), 'pool_pre_ping': True},
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    JSON_SORT_KEYS=False,
    EXECUTOR_TYPE="thread",
    EXECUTOR_MAX_WORKERS="1"
)

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

def _get_session():
    """Prepares session.

    Returns:
        (dict): Dictionary with session info.
    """
    idempotency_key = request.headers.get('X-Idempotency-Key')
    queue = db_queue(idempotency_key=idempotency_key, request=request.endpoint)

    session = {'ticket': queue['ticket'], 'idempotency_key': idempotency_key, 'initiated': queue['initiated']}

    return session

def enqueue(src_file, ticket, schema=None, tablename=None, replace=None, **kwargs):
    """Enqueue a transform job (in case requested response type is 'deferred')."""
    mainLogger.info("Processing ticket %s (%s)", ticket, src_file)
    try:
        result = _ingestIntoPostgis(src_file, ticket, schema=schema, tablename=tablename, replace=replace, **kwargs)
    except Exception as e:
        return (ticket, None, 0, str(e), None)
    rows = result.pop('length')
    return (ticket, json.dumps(result), 1, None, rows)

@app.after_request
def log_request(response):
    """Log request.
    Log only POST requests. If request has been deferred, the queue job is responsible for logging.
    """
    if request.method != 'POST' or response.status_code in [202, 400]:
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
    # In case method is POST and response status is not 202 or 400
    db_update_queue_status(ticket, completed=True, success=success, result=result, error_msg=comment, rows=rows)
    return response

@app.teardown_request
def clean_temp(error=None):
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
def health_check():
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
    # Check that we can connect to our PostGIS backend
    try:
        _checkConnectToPostgis()
    except Exception as exc:
        return make_response({'status': 'FAILED', 'reason': 'cannot connect to PostGIS backend', 'detail': str(exc)}, 200)
    # Check that we can connect to Database
    try:
        _checkConnectToDB()
    except Exception as exc:
        return make_response({'status': 'FAILED', 'reason': 'cannot connect to Database backend', 'detail': str(exc)}, 200)
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
                tablename:
                  type: string
                  description: The name of the table into which the data will be ingested (it should be a new table). By default, a unique random name will be given to the new table.
                schema:
                  type: string
                  description: The schema in which the table will be created (it has to exist). If not given, the default schema will be used.
                replace:
                  type: boolean
                  description: If true, the table will be replace if exists.
                  default: false
                encoding:
                  type: string
                  description: File encoding.
                crs:
                  type: string
                  description: CRS of the dataset.
              required:
                - resource
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
                tablename:
                  type: string
                  description: The name of the table into which the data will be ingested (it should be a new table). By default, a unique random name will be given to a new table.
                schema:
                  type: string
                  description: The schema in which the table will be created (schema has to exist). If not given, the default schema will be used.
                replace:
                  type: boolean
                  description: If true, the table will be replace if exists.
                  default: false
                encoding:
                  type: string
                  description: File encoding.
                crs:
                  type: string
                  description: CRS of the dataset.
              required:
                - resource
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
                    description: The schema of the created table.
                  table:
                    type: string
                    description: The name of the created table.
                  length:
                    type: integer
                    description: The number of features stored in the table.
                  type:
                    type: string
                    description: The response type as requested.
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
                  status:
                    type: string
                    description: The *status* endpoint to poll for the status of the request.
                  type:
                    type: string
                    description: The response type as requested.
          links:
            GetStatus:
              operationId: getStatus
              parameters:
                ticket: '$response.body#/ticket'
              description: The `ticket` value returned in the response can be used as the `ticket` parameter in `GET /status/{ticket}`.
        400:
          description: Form validation error or database schema does not exist.
          content:
            application/json:
              schema:
                type: object
                description: The key is the request body key.
                additionalProperties:
                  type: array
                  items:
                    type: string
                    description: Description of validation error.
                example: {crs: [Field must be a valid CRS.]}
        403:
          description: Insufficient privilege for writing in the database schema.
    """
    form = IngestForm(**request.form)
    if not form.validate():
        return make_response(form.errors, 400)

    # Form the source full path of the uploaded file
    if request.values.get('resource') is not None:
        src_file = path.join(environ['INPUT_DIR'], form.resource)
    else:
        try:
            resource = request.files['resource']
        except KeyError:
            return make_response({'resource' : ['Field is required.']}, 400)
        if resource is None:
            mainLogger.info('Client error: %s', 'resource not uploaded')
            return make_response({'resource': ["Not uploaded."]}, 400)

    session = _get_session()
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

    if form.response == 'prompt':
        g.response_type = 'prompt'
        try:
            result = _ingestIntoPostgis(src_file, ticket, tablename=form.tablename, schema=form.schema, replace=replace, **read_options)
        except Exception as e:
            if isinstance(e, SchemaException):
                return make_response({'schema': [str(e)]}, 400)
            elif isinstance(e, InsufficientPrivilege):
                return make_response(str(e), 403)
            else:
                return make_response(str(e), 500)
        return make_response({**result, "type": form.response}, 200)
    else:
        g.response_type = 'deferred'
        future = executor.submit(enqueue, src_file, ticket, tablename=form.tablename, schema=form.schema, replace=replace, **read_options)
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
                schema:
                  type: string
                  description: The database schema in which the table exists.
                workspace:
                  type: string
                  description: The GeoServer workspace in which the layer will be published (it will be created if does not exist). If not given, the default workspace will be used.
              required:
                - table
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
          description: Form validation error or table does not exist.
          content:
            application/json:
              schema:
                type: object
                description: The key is the request body key.
                additionalProperties:
                  type: array
                  items:
                    type: string
                    description: Description of validation error.
                example: {table: [Field is required.]}
    """
    form = PublishForm(**request.form)
    if not form.validate():
        return make_response(form.errors, 400)
    table = form.table
    schema = form.schema or getenv('POSTGIS_DB_SCHEMA')
    postgres = Postgres(schema=schema)
    if not postgres.checkIfTableExists(table):
        return make_response({'table': ['Field must represent an existing table in schema `%s`.' % (schema)]}, 400)
    g.session = _get_session()
    workspace = form.workspace or getenv('GEOSERVER_WORKSPACE')
    endpoints = _geoserver_endpoints(workspace, table)
    geoserver = Geoserver()
    if geoserver.checkIfLayersExists(workspace, table):
        return make_response(endpoints, 200)

    try:
        _publishTable(table, schema=schema, workspace=workspace)
    except Exception as e:
        return make_response(str(e), 500)
    return make_response(endpoints, 200)

@app.route("/ingest/<table>", methods=["DELETE"])
def drop(table):
    """Remove all ingested data relative to the given table.
    ---
    delete:
      summary: Remove all ingested data relative to the given table.
      description: Unpublishes the corresponding layer from GeoServer and drops the database table.
      tags:
        - Ingest
      parameters:
        - name: schema
          in: query
          description: The database schema; if not present the default schema will be assumed.
          required: false
          schema:
            type: string
        - name: workspace
          in: query
          description: The workspace that the layer belongs; if not present, the default workspace will be assumed.
          required: false
          schema:
            type: string
      responses:
        204:
          description: Table dropped (if existed).
    """
    store = getenv('GEOSERVER_STORE')
    schema = request.values.get('schema') or getenv('POSTGIS_DB_SCHEMA')
    workspace = request.values.get('workspace') or getenv('GEOSERVER_WORKSPACE')
    try:
        postgres = Postgres(schema=schema)
        geoserver = Geoserver()
        geoserver.unpublish(table, store, workspace=workspace)
        postgres.drop(table)
    except Exception as e:
        return make_response(str(e), 500)
    return '', 204

@app.route("/publish/<layer>", methods=["DELETE"])
def unpublish(layer):
    """Unpublish a GeoServer layer.
    ---
    delete:
      summary: Unpublish a GeoServer layer.
      description: Removes both the layer and feature type from GeoServer.
      tags:
        - Publish
      parameters:
        - name: workspace
          in: query
          description: The workspace that the layer belongs; if not present, the default workspace will be assumed.
          required: false
          schema:
            type: string
      responses:
        204:
          description: Layer unpulished (if existed).
    """
    store = getenv('GEOSERVER_STORE')
    workspace = request.values.get('workspace') or getenv('GEOSERVER_WORKSPACE')
    geoserver = Geoserver()
    try:
        geoserver.unpublish(layer, store, workspace=workspace)
    except Exception as e:
        return make_response(str(e), 500)
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
          description: Ticket not found.
    """
    if ticket is None:
        return make_response('Ticket is missing.', 400)
    queue = Queue().get(ticket=ticket)
    if queue is None:
        return make_response({"status": "Process not found."}, 404)
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
    """
    if ticket is None:
        return make_response('Resource ticket is missing.', 400)
    queue = Queue().get(ticket=ticket)
    if queue is None:
        return make_response({"status": "Process not found."}, 404)
    return make_response({**json.loads(queue['result']), 'length': queue['rows']}, 200)

@app.route("/ticket_by_key/<key>")
def get_ticket(key):
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
        return make_response({"status": "Process not found."}, 404)
    return make_response({'ticket': queue['ticket'], 'request': queue['request']}, 200)

with app.test_request_context():
    spec.path(view=ingest)
    spec.path(view=publish)
    spec.path(view=status)
    spec.path(view=result)
    spec.path(view=health_check)
    spec.path(view=get_ticket)
    spec.path(view=drop)
    spec.path(view=unpublish)


#
# Exception handlers
#

# Define a catch-all exception handler that simply logs a proper error message.
# Note: If actual error handling is needed, consider defining handlers targeting
#   more specific exception types (derived from Exception).
@app.errorhandler(Exception)
def handle_any_error(ex):
    exc_message = str(ex)
    mainLogger.error("Unexpected error: %s", exc_message, extra=exception_as_rfc5424_structured_data(ex))
    # Convert and return an HTTPException (is a valid response object for Flask)
    if isinstance(ex, HTTPException):
        return ex
    else:
        return InternalServerError(exc_message)


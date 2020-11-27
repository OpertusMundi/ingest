from flask import Flask
from flask import request, current_app, make_response
from werkzeug.utils import secure_filename
from flask_cors import CORS
from os import path, getenv, environ, makedirs, unlink
from shutil import move
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
import sqlalchemy
from . import db
from .postgres import Postgres
from .geoserver import Geoserver
from .logging import getLoggers

mainLogger, accountLogger = getLoggers()

def _makeDir(path):
    """Creates recursively the path, ignoring warnings for existing directories."""
    try:
        makedirs(path)
    except OSError:
        pass

def _getTempDir():
    """Return the temporary directory"""
    return getenv('TEMPDIR') or tempfile.gettempdir()

def _checkDirectoryWritable(d):
    fd, fname = tempfile.mkstemp(None, None, d)
    unlink(fname);

def _checkConnectToPostgis():
    url = 'postgresql://%(POSTGIS_USER)s:%(POSTGIS_PASS)s@%(POSTGIS_HOST)s:%(POSTGIS_PORT)s/%(POSTGIS_DB_NAME)s' % environ
    engine = sqlalchemy.create_engine(url)
    conn = engine.connect()
    conn.execute('SELECT 1')
    mainLogger.debug('_checkConnectToPostgis(): Connected to %s' % (engine.url))

def _executorCallback(future):
    """The callback function called when a job has succesfully completed."""
    ticket, result, success, comment, rows = future.result()
    with app.app_context():
        dbc = db.get_db()
        time = dbc.execute('SELECT requested_time FROM tickets WHERE ticket = ?;', [ticket]).fetchone()['requested_time']
        execution_time = round((datetime.now(timezone.utc) - time.replace(tzinfo=timezone.utc)).total_seconds(),3)
        dbc.execute('UPDATE tickets SET result=?, success=?, status=1, execution_time=?, comment=? WHERE ticket=?;', [result, success, execution_time, comment, ticket])
        dbc.commit()
        accountLogger(ticket=ticket, success=success, execution_start=time, execution_time=execution_time, comment=comment, rows=rows)

def _ingestAndPublish(src_file, ticket, env):
    """Ingest file content to PostgreSQL and publish to geoserver.
    Parameters:
        src_file (string): Full path to source file.
        ticket (string): The ticket of the request that will be also used as table and layer name.
        env (dict): Contains enviroment variables.
    Raises:
        Exception: In case postgres or geoserver requests fail.
    Returns:
        (dict) The GeoServer endpoints for WMS and WFS services.
    """
    # First, check if source file is compressed
    src_path = path.dirname(src_file)
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
        postgres = Postgres(user=env['POSTGIS_USER'], password=env['POSTGIS_PASS'], db=env['POSTGIS_DB_NAME'], schema=env['POSTGIS_DB_SCHEMA'], host=env['POSTGIS_HOST'], port=env['POSTGIS_PORT'])
        rows = postgres.ingest(src_file, ticket)
        geoserver = Geoserver(env['GEOSERVER_URL'], username=env['GEOSERVER_USER'], password=env['GEOSERVER_PASS'])
        geoserver.createWorkspace(env['GEOSERVER_WORKSPACE'])
        geoserver.createStore(name=env['GEOSERVER_STORE'], pg_db=env['POSTGIS_DB_NAME'], pg_user=env['POSTGIS_USER'], pg_password=env['POSTGIS_PASS'], workspace=env['GEOSERVER_WORKSPACE'], pg_host=env['POSTGIS_HOST'], pg_port=env['POSTGIS_PORT'], pg_schema=env['POSTGIS_DB_SCHEMA'])
        geoserver.publish(store=env['GEOSERVER_STORE'], table=ticket, workspace=env['GEOSERVER_WORKSPACE'])
    except Exception as e:
        raise Exception(e)
    return ({
        "WMS": '{0}/wms?service=WMS&request=GetMap&layers={0}:{1}'.format(env['GEOSERVER_WORKSPACE'], ticket),
        "WFS": '{0}/ows?service=WFS&request=GetFeature&typeName={0}:{1}'.format(env['GEOSERVER_WORKSPACE'], ticket)
    }, rows)

# Read (required) environment parameters
env = {}
for variable in [
    'POSTGIS_HOST', 'POSTGIS_USER', 'POSTGIS_PASS', 'POSTGIS_PORT', 'POSTGIS_DB_NAME', 'POSTGIS_DB_SCHEMA',
    'GEOSERVER_URL', 'GEOSERVER_USER', 'GEOSERVER_PASS', 'GEOSERVER_WORKSPACE', 'GEOSERVER_STORE'
]:
    env[variable] = getenv(variable)
    if env[variable] is None:
        raise Exception('Environment variable {} is not set.'.format(variable))


# OpenAPI documentation
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
app = Flask(__name__, instance_relative_config=True)
app.config.from_mapping(
    SECRET_KEY=getenv('SECRET_KEY'),
    DATABASE=getenv('DATABASE'),
)

# Ensure the instance folder exists and initialize application, db and executor.
_makeDir(app.instance_path)
db.init_app(app)
executor = Executor(app)
executor.add_default_done_callback(_executorCallback)

#Enable CORS
if getenv('CORS') is not None:
    if getenv('CORS')[0:1] == '[':
        origins = json.loads(getenv('CORS'))
    else:
        origins = getenv('CORS')
    cors = CORS(app, origins=origins)

@executor.job
def enqueue(src_file, ticket, env):
    """Enqueue a transform job (in case requested response type is 'deferred')."""
    dbc = db.get_db()
    dbc.execute('INSERT INTO tickets (ticket) VALUES(?);', [ticket])
    dbc.commit()
    try:
        endpoints, rows = _ingestAndPublish(src_file, ticket, env)
    except Exception as e:
        return (ticket, None, 0, str(e), None)
    return (ticket, json.dumps(endpoints), 1, None, rows)

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
              examples:
                example-1:
                  value: |-
                    {"status": "OK"}
    """
    mainLogger.info('Performing health checks...')
    # Check that temp directory is writable
    try: 
        _checkDirectoryWritable(_getTempDir())
    except Exception as exc:
        return make_response({'status': 'FAILED', 'reason': 'temp directory not writable', 'details': str(exc)}, 200); 
    # Check that we can connect to our PostGIS backend
    try:
        _checkConnectToPostgis()
    except Exception as exc:
        return make_response({'status': 'FAILED', 'reason': 'cannot connect to PostGIS backend', 'details': str(exc)}, 200);
    # Check that we can connect to our Geoserver backend
    # Todo ...
    return make_response({'status': 'OK'}, 200)

@app.route("/ingest", methods=["POST"])
def ingest():
    """The ingest endpoint.
    ---
    post:
      summary: Ingest a vector file (Shapefile/KML) into PostGIS and publish the layer to GeoServer.
      tags:
        - Ingest
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
              required:
                - resource
      responses:
        200:
          description: Ingestion / publication completed.
          content:
            application/json:
              schema:
                type: object
                properties:
                  WMS:
                    type: string
                    description: WMS endpoint
                  WFS:
                    type: string
                    description: WFS endpoint
        202:
          description: Accepted for processing, but ingestion/publish has not been completed.
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
                  endpoints:
                    type: string
                    description: The WMS/WFS endpoints to get the resulting resource when ready.
          links:
            GetStatus:
              operationId: getStatus
              parameters:
                ticket: '$response.body#/ticket'
              description: The `ticket` value returned in the response can be used as the `ticket` parameter in `GET /status/{ticket}`.
        400:
          description: Client error.
    """

    # Create a unique ticket for the request
    ticket = md5(str(uuid4()).encode()).hexdigest()
    # ticket = str(uuid4()).replace('-', '')

    # Get the type of the response
    response = request.values.get('response') or 'prompt'
    if response != 'prompt' and response != 'deferred':
        mainLogger.info('Client error: %s', "Wrong value for parameter 'response'")
        return make_response("Parameter 'response' can take one of: 'prompt', 'deferred'", 400)
    # Form the source full path of the uploaded file
    if request.values.get('resource') is not None:
        src_file = request.values.get('resource')
    else:
        resource = request.files['resource']
        if resource is None:
            mainLogger.info('Client error: %s', 'Resource not uploaded')
            return make_response({"Error": "Resource not uploaded."}, 400)

        # Create tmp directory and store the uploaded file.
        tempdir = _getTempDir();
        tempdir = path.join(tempdir, 'ingest')
        src_path = path.join(tempdir, 'src', ticket)
        _makeDir(src_path)
        src_file = path.join(src_path, secure_filename(resource.filename))
        resource.save(src_file)

    if response == 'prompt':
        start_time = datetime.now()
        try:
            endpoints, rows = _ingestAndPublish(src_file, ticket, env)
        except Exception as e:
            execution_time = round((datetime.now() - start_time).total_seconds(), 3)
            accountLogger(ticket=ticket, success=False, execution_start=start_time, execution_time=execution_time, comment=str(e))
            return make_response(str(e), 500)
        execution_time = round((datetime.now() - start_time).total_seconds(), 3)
        accountLogger(ticket=ticket, success=True, execution_start=start_time, execution_time=execution_time, rows=rows)
        return make_response(endpoints, 200)
    else:
        enqueue.submit(src_file, ticket, env)
        return make_response({"ticket": ticket, "status": "/status/{}".format(ticket), "endpoints": "/endpoints/{}".format(ticket)}, 202)

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
                  execution_time(s):
                    type: integer
                    description: The execution time in seconds.
        404:
          description: Ticket not found.
    """
    if ticket is None:
        return make_response('Ticket is missing.', 400)
    dbc = db.get_db()
    results = dbc.execute('SELECT status, success, requested_time, execution_time, comment FROM tickets WHERE ticket = ?', [ticket]).fetchone()
    if results is not None:
        if results['success'] is not None:
            success = bool(results['success'])
        else:
            success = None
        return make_response({"completed": bool(results['status']), "success": success, "requested": results['requested_time'], "execution_time(s)": results['execution_time'], "comment": results['comment']}, 200)
    return make_response('Not found.', 404)

@app.route("/endpoints/<ticket>")
def endpoints(ticket):
    """Get the resulted endpoints associated with a specific ticket.
    ---
    get:
      summary: Get the result of a request.
      description: Returns the WMS/WFS endpoints resulted from a ingestion/publication request corresponding to a specific ticket.
      tags:
        - Endpoints
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
                  WMS:
                    type: string
                    description: WMS endpoint
                  WFS:
                    type: string
                    description: WFS endpoint
        404:
          description: Ticket not found or transform has not been completed.
    """
    if ticket is None:
        return make_response('Resource ticket is missing.', 400)
    dbc = db.get_db()
    result = dbc.execute('SELECT result FROM tickets WHERE ticket = ?', [ticket]).fetchone()['result']
    if result is None:
        return make_response('Not found.', 404)
    return make_response(json.loads(result), 200)

with app.test_request_context():
    spec.path(view=ingest)
    spec.path(view=status)
    spec.path(view=endpoints)
    spec.path(view=health_check)

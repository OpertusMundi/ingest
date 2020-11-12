from flask import Flask
from flask import request, current_app, make_response
from werkzeug.utils import secure_filename
from flask_cors import CORS
from os import path, getenv, makedirs
from shutil import move
from tempfile import gettempdir
from uuid import uuid4
from hashlib import md5
from datetime import datetime, timezone
from flask_executor import Executor
import zipfile
import tarfile
import json
from . import db
from .postgres import Postgres
from .geoserver import Geoserver

def mkdir(path):
    """Creates recursively the path, ignoring warnings for existing directories."""
    try:
        makedirs(path)
    except OSError:
        pass

def executorCallback(future):
    """The callback function called when a job has succesfully completed."""
    ticket, result, success, comment = future.result()
    with app.app_context():
        dbc = db.get_db()
        time = dbc.execute('SELECT requested_time FROM tickets WHERE ticket = ?;', [ticket]).fetchone()['requested_time']
        execution_time = round((datetime.now(timezone.utc) - time.replace(tzinfo=timezone.utc)).total_seconds())
        dbc.execute('UPDATE tickets SET result=?, success=?, status=1, execution_time=?, comment=? WHERE ticket=?;', [result, success, execution_time, comment, ticket])
        dbc.commit()

def ingestAndPublish(src_file, ticket, env):
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
        postgres = Postgres(user=env['DB_USER'], password=env['DB_PASS'], db=env['DB_NAME'], schema=env['DB_SCHEMA'], host=env['DB_HOST'], port=env['DB_PORT'])
        postgres.ingest(src_file, ticket)
        geoserver = Geoserver(env['GS_URL'], username=env['GS_USER'], password=env['GS_PASS'])
        geoserver.createWorkspace(env['GS_WORKSPACE'])
        geoserver.createStore(name=env['GS_STORE'], pg_db=env['DB_NAME'], pg_user=env['DB_USER'], pg_password=env['DB_PASS'], workspace=env['GS_WORKSPACE'], pg_host=env['DB_HOST'], pg_port=env['DB_PORT'], pg_schema=env['DB_SCHEMA'])
        geoserver.publish(store=env['GS_STORE'], table=ticket, workspace=env['GS_WORKSPACE'])
    except Exception as e:
        raise Exception(e)
    return {
        "WMS": '{0}/wms?service=WMS&request=GetMap&layers={0}:{1}'.format(env['GS_WORKSPACE'], ticket),
        "WFS": '{0}/ows?service=WFS&request=GetFeature&typeName={0}:{1}'.format(env['GS_WORKSPACE'], ticket)
    }

# Read (required) environment parameters
env = {}
for variable in [
    'DB_HOST', 'DB_USER', 'DB_PASS', 'DB_PORT', 'DB_NAME', 'DB_SCHEMA',
    'GS_URL', 'GS_USER', 'GS_PASS', 'GS_WORKSPACE', 'GS_STORE'
]:
    env[variable] = getenv(variable)
    if env[variable] is None:
        raise Exception('Environment variable {} is not set.'.format(variable))

app = Flask(__name__, instance_relative_config=True)
app.config.from_mapping(
    SECRET_KEY='dev',
    DATABASE=path.join(app.instance_path, 'ingest.sqlite'),
)

# Ensure the instance folder exists and initialize application, db and executor.
mkdir(app.instance_path)
db.init_app(app)
executor = Executor(app)
executor.add_default_done_callback(executorCallback)

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
        result = ingestAndPublish(src_file, ticket, env)
    except Exception as e:
        return (ticket, None, 0, str(e))
    return (ticket, json.dumps(result), 1, None)

@app.route("/")
def index():
    """The index route, gives info about the API endpoints."""
    ingest_params = {"response": "<prompt|deferred>"}
    get = {
        "/ingest": {"params": {**ingest_params, "src": "<source file path>"}, "response": "json"},
        "/status/<ticket>": {"response": "json"},
        "/endpoints/<ticket>": {"response": "json"}
    }
    post = {"/ingest": {"params": ingest_params, "response": "json"}}
    response = make_response({"GET": get, "POST": post}, 200)
    return response

@app.route("/ingest", methods=["POST"])
def ingest():
    """The ingest endpoint.
    It expects the following HTTP form parameters:
        -resource (required): A resovable path of source file or the source file itself.
        -response: prompt (default) or deferred.
    """

    # Create a unique ticket for the request
    ticket = md5(str(uuid4()).encode()).hexdigest()
    # ticket = str(uuid4()).replace('-', '')

    # Get the type of the response
    response = request.values.get('response') or 'prompt'
    if response != 'prompt' and response != 'deferred':
        return make_response("Parameter 'response' can take one of: 'prompt', 'deferred'", 400)
    # Form the source full path of the uploaded file
    if request.values.get('resource') is not None:
        src_file = request.values.get('resource')
    else:
        resource = request.files['resource']
        if resource is None:
            return make_response({"Error": "Resource not uploaded."}, 400)

        # Create tmp directory and store the uploaded file.
        tempdir = getenv('TEMPDIR') or gettempdir()
        tempdir = path.join(tempdir, 'ingest')
        src_path = path.join(tempdir, 'src', ticket)
        mkdir(src_path)
        src_file = path.join(src_path, secure_filename(resource.filename))
        resource.save(src_file)

    if response == 'prompt':
        try:
            result = ingestAndPublish(src_file, ticket, env)
        except Exception as e:
            return make_response(str(e), 500)
        return make_response(result, 200)
    else:
        enqueue.submit(src_file, ticket, env)
        return make_response({"ticket": ticket, "status": "/status/{}".format(ticket), "endpoints": "/endpoints/{}".format(ticket)}, 200)

@app.route("/status/<ticket>")
def status(ticket):
    """Get the status of a specific ticket."""
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
    """Get the resulted endpoints associated with a specific ticket."""
    if ticket is None:
        return make_response('Resource ticket is missing.', 400)
    dbc = db.get_db()
    result = dbc.execute('SELECT result FROM tickets WHERE ticket = ?', [ticket]).fetchone()['result']
    if result is None:
        return make_response('Not found.', 404)
    return make_response(json.loads(result), 200)

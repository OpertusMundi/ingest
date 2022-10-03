import logging
import json
import os
import time
import sqlalchemy
from uuid import uuid4
import fiona.errors
import string
import random

from ingest.app import app
from ingest.postgres import Postgres
from ingest.geoserver import Geoserver

postgis = Postgres.makeFromEnv()

geoserver = Geoserver.makeFromEnv()

workspace = os.getenv('GEOSERVER_DEFAULT_WORKSPACE') or str(uuid4())

dirname = os.path.dirname(__file__)
input_dir = os.path.join(dirname, '..', 'test_data')


name_alphabet = string.ascii_lowercase + string.digits
def _random_name(n):
    return ''.join(random.choice(name_alphabet) for i in range(n)) 

# Setup/Teardown

def setup_module():
    print(" == Setting up tests for {0}".format(__name__))
    app.config['TESTING'] = True
    print(" == database URL: {0}".format(app.config['SQLALCHEMY_DATABASE_URI']))
    print(" == PostGIS database URL: {0!r}".format(postgis.urlFor()))
    print(" == Geoserver URL: {0!r}".format(geoserver.urlFor()))
    print(" == workspace: {0}".format(workspace))
    pass

def teardown_module():
    print(" == Tearing down tests for %s"  % (__name__))
    pass

# Tests

def test_get_documentation_1():
    with app.test_client() as client:
        res = client.get('/', query_string=dict(), headers=dict())
        assert res.status_code == 200
        r = res.get_json();
        assert not (r.get('openapi') is None)

def test_get_health_check():
    with app.test_client() as client:
        res = client.get('/_health', query_string=dict(), headers=dict())
        assert res.status_code == 200
        r = res.get_json();
        if 'reason' in r:
            logging.error('The service is unhealthy: %(reason)s\n%(detail)s', r)
        logging.debug("From /_health: %s" % (r))
        assert r['status'] == 'OK'

def test_ingest_kml_prompt():
    """Functional Test: Test KML resource using a name for existing file"""
    table_name = "geo_kml_{0}".format(_random_name(10))
    
    with app.test_client() as client:
        res = client.post('/ingest', data=dict(resource='geo.kml', workspace=workspace, table=table_name))
        assert res.status_code == 200
        r = res.get_json()
        assert r.get('schema') == workspace
        assert r.get('table') == table_name
        assert r.get('length') == 3

    assert postgis.checkIfTableExists(table_name, workspace)

def test_ingest_kml_prompt_using_upload():
    """Functional Test: Test KML resource using upload"""
    table_name = "geo_kml_{0}".format(_random_name(10))
    input_path = os.path.join(input_dir, 'geo.kml')
    
    with app.test_client() as client:
        res = client.post('/ingest', data=dict(
            resource=open(input_path, 'rb'), workspace=workspace, table=table_name))
        assert res.status_code == 200
        r = res.get_json()
        assert r.get('schema') == workspace
        assert r.get('table') == table_name
        assert r.get('length') == 3
    
    assert postgis.checkIfTableExists(table_name, workspace)
    
def test_ingest_shp_prompt():
    """Functional Test: Test SHP resource using a name for existing file"""
    table_name = "geo_shp_{0}".format(_random_name(10))
    
    with app.test_client() as client:
        res = client.post('/ingest', data=dict(resource='geo.zip', workspace=workspace, table=table_name))
        assert res.status_code == 200
        r = res.get_json()
        assert r.get('schema') == workspace
        assert r.get('table') == table_name
        assert r.get('length') == 3

    assert postgis.checkIfTableExists(table_name, workspace)

def test_ingest_shp_prompt_using_upload():
    """Functional Test: Test SHP resource using upload"""
    table_name = "geo_shp_{0}".format(_random_name(10))
    input_path = os.path.join(input_dir, 'geo.zip')
    
    with app.test_client() as client:
        res = client.post('/ingest', data=dict(
            resource=open(input_path, 'rb'), workspace=workspace, table=table_name))
        assert res.status_code == 200
        r = res.get_json()
        assert r.get('schema') == workspace
        assert r.get('table') == table_name
        assert r.get('length') == 3

    assert postgis.checkIfTableExists(table_name, workspace)

def test_ingest_shp_deferred():
    """Functional Test: Test SHP resource and expect response with ticket"""
    
    table_name = "geo_shp_{0}".format(_random_name(10))
    idempotency_key = str(uuid4())
    
    with app.test_client() as client:
        res = client.post('/ingest',
            data=dict(resource='geo.zip', response='deferred', workspace=workspace, table=table_name),
            headers={'X-Idempotency-Key': idempotency_key},
        )
        assert res.status_code == 202
        r = res.get_json()
        ticket = r.get('ticket')
        endpoint = r.get('status')
        assert ticket is not None
        assert endpoint is not None
        assert r.get('type') == 'deferred'
    
    time.sleep(1.0)
    
    with app.test_client() as client:
        res = client.get(endpoint)
        assert res.status_code == 200
        r = res.get_json()
        assert r.get('comment') is None
        assert r.get('completed') is not None
        assert r.get('executionTime') is not None
        assert r.get('requested') is not None
        assert r.get('success') is not None
        res = client.get('/ticket_by_key/%s' % (idempotency_key))
        assert res.status_code == 200
        r = res.get_json()
        assert r.get('request') == 'ingest'
        assert r.get('ticket') == ticket



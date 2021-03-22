import logging
import json
from os import path, getenv
from time import sleep
from uuid import uuid4
import fiona.errors

from ingest.app import app
from ingest.postgres import Postgres
from ingest.app import _ingestIntoPostgis, _getWorkingPath

# Setup/Teardown

def setup_module():
    print(" == Setting up tests for %s"  % (__name__))
    app.config['TESTING'] = True
    print(" == Using database at %s"  % (app.config['DATABASE']))
    pass

def teardown_module():
    print(" == Tearing down tests for %s"  % (__name__))
    pass

# Tests

dirname = path.dirname(__file__)
kml = path.join(dirname, '..', 'test_data', 'geo.kml')
shapefile = path.join(dirname, '..', 'test_data', 'geo.zip')

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

def test_postgres_1():
    """Functional Test: Test checkIfTableExists"""
    postgres = Postgres()
    assert postgres.checkIfTableExists('spatial_ref_sys', schema="public")

def test_postgres_2():
    """Functional Test: Test KML ingest into PostGIS"""
    postgres = Postgres()
    schema, table, rows = postgres.ingest(kml, 'test_table', chunksize=1, commit=False)
    assert schema == getenv('POSTGIS_DB_SCHEMA')
    assert table == 'test_table'
    assert rows == 3
    assert not postgres.checkIfTableExists('test_table')

def test_postgres_3():
    """Functional Test: Test ingest with unsupported file type."""
    postgres = Postgres()
    try:
        schema, table, rows = postgres.ingest(shapefile, 'test_table', commit=False)
        assert False
    except Exception as e:
        assert isinstance(e, fiona.errors.DriverError)

def test_postgres_4():
    """Functional Test: Test ingest shapefile, uncompress and cleanup"""
    result = _ingestIntoPostgis(shapefile, 'ticket')
    assert 'schema' in result
    assert 'table' in result
    assert 'length' in result
    assert result['length'] == 3
    assert not path.isdir(_getWorkingPath('ticket'))

def test_ingest_1():
    """Functional Test: Test KML ingest"""
    with app.test_client() as client:
        res = client.post('/ingest', data=dict(resource='test_data/geo.kml'))
        assert res.status_code == 200
        r = res.get_json()
        assert r.get('schema') is not None
        assert r.get('table') is not None
        assert r.get('length') == 3

def test_ingest_2():
    """Functional Test: Test Shapefile ingest; streaming resource"""
    with app.test_client() as client:
        res = client.post('/ingest', data=dict(resource='test_data/geo.zip'))
        assert res.status_code == 200
        r = res.get_json()
        assert r.get('schema') is not None
        assert r.get('table') is not None
        assert r.get('length') == 3

def test_ingest_3():
    """Functional Test: Test complete ingest functionality"""
    idempotency_key = str(uuid4())
    with app.test_client() as client:
        res = client.post(
            '/ingest',
            data=dict(resource=(open(shapefile, 'rb'), 'geo.zip'), response='deferred'),
            headers={'X-Idempotency-Key': idempotency_key},
            content_type='multipart/form-data'
        )
        assert res.status_code == 202
        r = res.get_json()
        ticket = r.get('ticket')
        endpoint = r.get('status')
        assert ticket is not None
        assert endpoint is not None
        assert r.get('type') == 'deferred'
    sleep(0.5)
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

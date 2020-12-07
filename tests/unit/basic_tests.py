import logging
import json
from os import path, getenv
from fiona.errors import DriverError

from ingest.postgres import Postgres
from ingest.app import _ingestIntoPostgis, _getWorkingPath

# Setup/Teardown
def setup_module():
    print(" == Setting up tests for %s"  % (__name__))
    pass

def teardown_module():
    print(" == Tearing down tests for %s"  % (__name__))
    pass

# Tests
dirname = path.dirname(__file__)
kml = path.join(dirname, '..', 'test_data', 'geo.kml')
shapefile = path.join(dirname, '..', 'test_data', 'geo.zip')

def test_postgres_1():
    """Unit Test: Test checkIfTableExists"""
    postgres = Postgres()
    assert postgres.checkIfTableExists('spatial_ref_sys', schema="public")

def test_postgres_2():
    """Unit Test: Test KML ingest into PostGIS"""
    postgres = Postgres()
    schema, table, rows = postgres.ingest(kml, 'test_table', chunksize=1, commit=False)
    assert schema == getenv('POSTGIS_DB_SCHEMA')
    assert table == 'test_table'
    assert rows == 3
    assert not postgres.checkIfTableExists('test_table')

def test_postgres_3():
    """Unit Test: Test ingest with unsupported file type."""
    postgres = Postgres()
    try:
        schema, table, rows = postgres.ingest(shapefile, 'test_table', commit=False)
        assert False
    except Exception as e:
        assert isinstance(e, DriverError)

def test_postgres_4():
    """Unit Test: Test ingest shapefile, uncompress and cleanup"""
    result = _ingestIntoPostgis(shapefile, 'ticket')
    assert 'schema' in result
    assert 'table' in result
    assert 'length' in result
    assert result['length'] == 3
    assert not path.isdir(_getWorkingPath('ticket'))

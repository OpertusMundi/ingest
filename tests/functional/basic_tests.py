import logging
import json
import os
import time
import sqlalchemy
import uuid
import fiona.errors
import string
import random
import logging
import pycurl
import urllib.parse
import posixpath
import xml.etree.ElementTree

from ingest.app import app, databaseUrlFromEnv
from ingest.postgres import Postgres
from ingest.geoserver import Geoserver

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

postgis = Postgres.makeFromEnv()
geoserver = Geoserver.makeFromEnv()
geoserver_base_url = geoserver.urlFor("")
geoserver_base_url_p = urllib.parse.urlparse(geoserver_base_url)

workspace = geoserver.default_workspace or ('_' + str(uuid.uuid4()))

dirname = os.path.dirname(__file__)
input_dir = os.path.join(dirname, '..', 'test_data')


def _table_name_for_input(input_name):
    return 'x_{0}_{1:d}'.format(uuid.uuid5(uuid.NAMESPACE_URL, input_name), int(1000 * time.time()))

# Setup/Teardown

def setup_module():
    print(" == Setting up tests for {0} [workspace={1}]".format(__name__, workspace))
    app.config['TESTING'] = True
    #print(" == Using database URL: {0!r}".format(databaseUrlFromEnv()))
    #print(" == Using PostGIS database URL: {0!r}".format(postgis.urlFor()))
    #print(" == Using Geoserver URL: {0!r}".format(geoserver.urlFor()))
    pass

def teardown_module():
    print(" == Tearing down tests for %s"  % (__name__))
    pass

# Tests

def test_get_documentation():
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

def test_ingest_prompt_from_name():
    yield _test_ingest_prompt_from_name, '1.kml', 3
    yield _test_ingest_prompt_from_name, '1.zip', 3

def _test_ingest_prompt_from_name(input_name, expected_num_of_records):
    """Functional Test: Ingest a resource using a name for existing file"""
    table_name = _table_name_for_input(input_name)
    logger.info('Testing ingest for %s: %s', input_name, table_name)

    with app.test_client() as client:
        res = client.post('/ingest', data=dict(resource=input_name, workspace=workspace, table=table_name))
        assert res.status_code == 200
        r = res.get_json()
        assert r.get('schema') == workspace
        assert r.get('table') == table_name
        assert r.get('length') == expected_num_of_records

    assert postgis.checkIfTableExists(table_name, workspace)

def test_ingest_prompt_from_upload():
    yield _test_ingest_prompt_from_upload, '1.kml', 3
    yield _test_ingest_prompt_from_upload, '1.zip', 3

def _test_ingest_prompt_from_upload(input_name, expected_num_of_records):
    """Functional Test: Ingest a resource using upload"""
    input_path = os.path.join(input_dir, input_name)
    table_name = _table_name_for_input(input_name)
    logger.info('Testing ingest for %s: %s', input_name, table_name)
    
    with app.test_client() as client:
        res = client.post('/ingest', data=dict(resource=open(input_path, 'rb'), workspace=workspace, table=table_name))
        assert res.status_code == 200
        r = res.get_json()
        assert r.get('schema') == workspace
        assert r.get('table') == table_name
        assert r.get('length') == expected_num_of_records
    
    assert postgis.checkIfTableExists(table_name, workspace)
    
def test_ingest_deferred_from_name():
    yield _test_ingest_deferred_from_name, '1.kml'
    yield _test_ingest_deferred_from_name, '1.zip'

def _test_ingest_deferred_from_name(input_name):
    """Functional Test: Ingest a resource in a deferred manner, expect response with ticket"""
    table_name = _table_name_for_input(input_name)
    logger.info('Testing ingest for %s: %s', input_name, table_name)
    idempotency_key = str(uuid.uuid4())
    
    with app.test_client() as client:
        res = client.post('/ingest',
            data=dict(resource=input_name, response='deferred', workspace=workspace, table=table_name),
            headers={
                'X-Idempotency-Key': idempotency_key
            },
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

def test_ingest_prompt_then_publish_layer():
    yield _test_ingest_prompt_then_publish_layer, '1.kml', 3
    yield _test_ingest_prompt_then_publish_layer, '1.zip', 3

def _test_ingest_prompt_then_publish_layer(input_name, expected_num_of_records):
    """Functional Test: Ingest a resource, then publish layer to Geoserver"""
    table_name = _table_name_for_input(input_name)
    
    # Ingest 

    with app.test_client() as client:
        res = client.post('/ingest', data=dict(resource=input_name, workspace=workspace, table=table_name))
        assert res.status_code == 200
        r = res.get_json()
        assert r.get('schema') == workspace
        assert r.get('table') == table_name
    
    # Publish

    describefeaturetype_url = None
    getfeature_url = None
    describelayer_url = None
    with app.test_client() as client:
        res = client.post('/publish', data=dict(workspace=workspace, table=table_name))
        assert res.status_code == 200
        r = res.get_json()
        #print(json.dumps(r, indent=2))
        assert r.get('wmsBase') is not None
        assert r.get('wfsBase') is not None
        describelayer_url = r.get('wmsDescribeLayer')
        assert describelayer_url is not None
        getfeature_url = r.get('wfsGetFeature')
        assert getfeature_url is not None
        describefeaturetype_url = r.get('wfsDescribeFeatureType')
        assert describefeaturetype_url is not None
    
    # Examine published endpoints

    ns_map = { 
        'xsd': 'http://www.w3.org/2001/XMLSchema',
        'wfs': 'http://www.opengis.net/wfs/2.0'
    }
    
    describefeaturetype_url_p = urllib.parse.urlparse(describefeaturetype_url)
    assert posixpath.split(describefeaturetype_url_p.path)[0] == workspace
    describefeaturetype_url_p = geoserver_base_url_p._replace(
        path=posixpath.join(geoserver_base_url_p.path, describefeaturetype_url_p.path), 
        query=describefeaturetype_url_p.query)
    
    getfeature_url_p = urllib.parse.urlparse(getfeature_url)
    assert posixpath.split(getfeature_url_p.path)[0] == workspace
    getfeature_url_p = geoserver_base_url_p._replace(
        path=posixpath.join(geoserver_base_url_p.path, getfeature_url_p.path), 
        query=getfeature_url_p.query)

    curl = pycurl.Curl()
    curl.setopt(pycurl.URL, describefeaturetype_url_p.geturl())
    describefeaturetype_res = curl.perform_rs()
    curl.close()
    describefeaturetype_tree = xml.etree.ElementTree.fromstring(describefeaturetype_res)
    describefeaturetype_element_node = describefeaturetype_tree.find('xsd:element', ns_map)
    assert describefeaturetype_element_node is not None
    assert describefeaturetype_element_node.attrib.get('name') == table_name
    assert describefeaturetype_element_node.attrib.get('type').startswith(workspace + ":")
     
    curl = pycurl.Curl()
    curl.setopt(pycurl.URL, getfeature_url_p.geturl())
    getfeature_res = curl.perform_rs()
    curl.close()
    getfeature_featurecollection_tree = xml.etree.ElementTree.fromstring(getfeature_res)
    assert getfeature_featurecollection_tree.tag == "{%(wfs)s}FeatureCollection" % ns_map
    getfeature_member_nodes = getfeature_featurecollection_tree.findall('wfs:member', ns_map)
    assert len(getfeature_member_nodes) == expected_num_of_records


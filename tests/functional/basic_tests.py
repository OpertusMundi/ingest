import logging
import json

from ingest.app import app

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
            logging.error('The service is unhealthy: %(reason)s (%(details)s)', r)
        logging.debug("From /_health: %s" % (r))
        assert r['status'] == 'OK'
        
# Todo Test service endpoints ...

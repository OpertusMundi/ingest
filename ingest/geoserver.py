import pycurl
import json
import posixpath
import urllib.parse
from os import environ

from .logging import mainLogger
logger = mainLogger.getChild('geoserver');

class RequestFailedException(Exception):
    
    def __init__(self, status_code, http_method, url):
        self.status_code = status_code
        self.http_method = http_method
        self.url = url
        super().__init__("Got status [{0}] for: {1} {2}".format(status_code, http_method, url))

class _DataProvider(object):
    """A DataProvider for cURL."""
    def __init__(self, data):
        self.data = data
        self.finished = False

    def read_cb(self, size):
        assert len(self.data) <= size
        if not self.finished:
            self.finished = True
            return self.data
        else:
            # Nothing more to read
            return ""

class Geoserver(object):
    """Contains methods to communicate with GeoServer REST API.
    """
    
    DEFAULT_PORT = 8080
    
    @classmethod
    def makeFromEnv(cls):
        
        username = environ['GEOSERVER_USER']
        
        password = None
        if 'GEOSERVER_PASS' in environ:
            password = environ['GEOSERVER_PASS']
        elif 'GEOSERVER_PASS_FILE' in environ:
            with open(environ['GEOSERVER_PASS_FILE'], "r") as f: password = f.read().strip();
        else:
            raise RuntimeError('missing password for Geoserver (GEOSERVER_PASS or GEOSERVER_PASS_FILE)');
            
        url_template = environ['GEOSERVER_URL'];
        
        port_map_str = environ.get("GEOSERVER_PORT_MAP")
        port_map = {}
        if port_map_str:
            port_map = dict(((t[0] or None, int(t[1])) for t in 
                (e.split(":") for e in port_map_str.split(","))));
            
        datastore_template = environ['GEOSERVER_DATASTORE'];
        default_workspace = environ['GEOSERVER_DEFAULT_WORKSPACE'];
        
        return Geoserver(url_template, username, password, port_map, datastore_template, default_workspace);
    
    def __init__(self, url_template, username, password, port_map, datastore_template, default_workspace):
        self.url_template = url_template
        self.username = username
        self.password = password
        self.userpwd = "{0}:{1}".format(username, password)
        self.port_map = port_map
        self.datastore_template = datastore_template
        self.default_workspace = default_workspace

    def urlFor(self, target_path, shard=None):
        """Build an absolute URL for a path
        
        Parameters:
            target_path: path relative to REST base path
            shard: the shard identifier, or None if no sharding is present
        """
        
        base_url = None
        if shard:
            port = self.port_map.get(shard) or self.DEFAULT_PORT;
            base_url = self.url_template.format(shard=shard, port=port);
        else:
            base_url = self.url_template;
            
        p = urllib.parse.urlparse(base_url);
        return p._replace(path=posixpath.join(p.path, 'rest', target_path)).geturl();    

    def _get(self, target_path, shard=None):
        """GET request to GeoServer.
        Parameters:
            target_path (string): relative path (to REST base path) for the request.
        Raises:
            Exception: In case HTTP code is other than 200.
        """
        
        target_url = self.urlFor(target_path, shard);
        
        conn = pycurl.Curl()
        conn.setopt(pycurl.USERPWD, self.userpwd)
        conn.setopt(conn.URL, target_url)
        
        response = conn.perform_rs()
        http_code = conn.getinfo(pycurl.HTTP_CODE)
        conn.close()
        if http_code != 200:
            raise RequestFailedException(http_code, "GET", target_url)
        
        return (target_url, http_code, response)

    def _post(self, target_path, xml_payload, shard=None):
        """POST request to GeoServer.
        Parameters:
            target_path (str): The relative (to REST url) endpoint for the request.
            xml_payload (str): The XML payload that will be passed to GeoServer.
        Raises:
            Exception: In case HTTP code is other than 2**.
        """
        
        target_url = self.urlFor(target_path, shard);
        
        conn = pycurl.Curl()
        conn.setopt(pycurl.USERPWD, self.userpwd)
        conn.setopt(conn.URL, target_url)
        conn.setopt(pycurl.HTTPHEADER, ["Content-type: text/xml"])
        conn.setopt(pycurl.POSTFIELDSIZE, len(xml_payload))
        conn.setopt(pycurl.READFUNCTION, _DataProvider(xml_payload).read_cb)
        conn.setopt(pycurl.POST, 1)
        
        conn.perform()
        http_code = conn.getinfo(pycurl.HTTP_CODE)
        conn.close()
        if http_code > 299:
            raise RequestFailedException(http_code, "POST", target_url)

    def _delete(self, target_path, shard=None):
        """DELETE request to GeoServer.
        Parameters:
            target_path (str): The relative (to REST url) endpoint for the request.
            shard (str):
        Raises:
            Exception: In case HTTP code is other than 2**
        """
        
        target_url = self.urlFor(target_path, shard);
        
        conn = pycurl.Curl()
        conn.setopt(pycurl.USERPWD, self.userpwd)
        conn.setopt(conn.URL, target_url)
        conn.setopt(pycurl.CUSTOMREQUEST, "DELETE")
        
        conn.perform()
        http_code = conn.getinfo(pycurl.HTTP_CODE)
        conn.close()
        if http_code > 299:
            raise RequestFailedException(http_code, "DELETE", target_url)

    def check(self, shard=None):
        """Checks connection to GeoServer REST API.
        Raises:
            Exception: In case HTTP code is other than 2** or metrics are not returned.
        Returns:
            (string) Rest URL
        """
        url, code, res = self._get('about/system-status', shard)
        res = json.loads(res)
        if not 'metrics' in res.keys():
            raise Exception('Metrics not found.')
        return url 

    def checkIfLayerExists(self, workspace, layer, shard=None):
        target_path = 'workspaces/{0}/layers/{1}'.format(workspace, layer)
        
        exists = True
        try:
            self._get(target_path, shard)
        except RequestFailedException as e:
            if e.status_code == 404:
                exists = False
            else:
                raise e
        return exists

    def createWorkspaceIfNotExists(self, workspace, shard=None):
        """Creates (if does not exist) a workspace in GeoServer"""
        
        xml_payload = "<workspace><name>{0}</name></workspace>".format(workspace);
        
        try:
            self._post("workspaces", xml_payload, shard)
        except RequestFailedException as e:
            if e.status_code == 409: # conflict
                pass # workspace already exists
            else:
                raise e

    def datastoreName(self, db_url, db_schema, shard=None):
        return self.datastore_template.format(database=db_url.database, schema=db_schema)
                
    def createDatastoreIfNotExists(self, name, workspace, db_url, db_schema, shard=None):
        """Creates (if it does not exist) a PostGis datastore in a GeoServer workspace."""
        
        try:
            return self._get('workspaces/{0}/datastores/{1}.json'.format(workspace, name), shard)
        except RequestFailedException as e:
            if e.status_code == 404:
                pass
            else:
                raise e;
            
        # datastore does'nt exist: create here
        
        xml_payload = '''
        <dataStore>
            <name>{name}</name>
            <connectionParameters>
                <dbtype>postgis</dbtype>
                <host>{db_url.host}</host>
                <port>{db_url.port}</port>
                <database>{db_url.database}</database>
                <schema>{db_schema}</schema>
                <user>{db_url.username}</user>
                <passwd>{db_url.password}</passwd>
            </connectionParameters>
        </dataStore>
        '''.format(name=name, db_url=db_url, db_schema=db_schema);
        
        self._post('workspaces/{0}/datastores'.format(workspace), xml_payload, shard)
        
    def publish(self, workspace, datastore, table, shard=None):
        """Publish a layer from a datastore."""
        
        xml_payload = "<featureType><name>{0}</name></featureType>".format(table)
        target_path = 'workspaces/{0}/datastores/{1}/featuretypes'.format(workspace, datastore)
        self._post(target_path, xml_payload, shard)

    def unpublish(self, workspace, datastore, layer, shard=None):
        if not self.checkIfLayerExists(workspace, layer, shard):
            return
        
        target_path = "layers/{0}:{1}.xml".format(workspace, layer) 
        self._delete(target_path, shard)
        
        target_path = 'workspaces/{0}/datastores/{1}/featuretypes/{2}.xml'.format(workspace, datastore, layer)
        self._delete(target_path, shard)


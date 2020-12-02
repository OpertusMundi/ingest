import pycurl
from os import environ

class DataProvider(object):
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
    Attributes:
        rest_url (string): The GeoServer REST full url.
        username (string): Username for GeoServer HTTP authentication.
        password (string): Password for GeoServer HTTP authentication.
    """
    def __init__(self, url=None, username=None, password=None):
        """Initiates the GeoServer class.
        Parameters:
            url (string): The GeoServer REST full url.
            username (string): Username for GeoServer HTTP authentication.
            password (string): Password for GeoServer HTTP authentication.
        """
        url = url or environ['GEOSERVER_URL']
        username = username or environ['GEOSERVER_USER']
        password = password or environ['GEOSERVER_PASS']
        self.rest_url = url + '/rest'
        self.username = username
        self.password = password

    def _get(self, endpoint):
        """GET request to GeoServer.
        Parameters:
            endpoint (string): The relative (to REST url) endpoint for the request.
        Raises:
            Exception: In case HTTP code is other than 200.
        """
        conn = pycurl.Curl()
        conn.setopt(pycurl.USERPWD, "%s:%s" % (self.username, self.password))
        conn.setopt(conn.URL, "%s/%s" % (self.rest_url, endpoint))
        conn.perform()
        http_code = conn.getinfo(pycurl.HTTP_CODE)
        conn.close()
        if http_code != 200:
            raise Exception(http_code)

    def _post(self, endpoint, xml):
        """POST request to GeoServer.
        Parameters:
            endpoint (string): The relative (to REST url) endpoint for the request.
            xml (string): The xml string that will be passed to GeoServer.
        Raises:
            Exception: In case HTTP code is other than 2**.
        """
        conn = pycurl.Curl()
        conn.setopt(pycurl.USERPWD, "%s:%s" % (self.username, self.password))
        conn.setopt(conn.URL, "%s/%s" % (self.rest_url, endpoint))
        conn.setopt(pycurl.HTTPHEADER, ["Content-type: text/xml"])
        conn.setopt(pycurl.POSTFIELDSIZE, len(xml))
        conn.setopt(pycurl.READFUNCTION, DataProvider(xml).read_cb)
        conn.setopt(pycurl.POST, 1)
        conn.perform()
        http_code = conn.getinfo(pycurl.HTTP_CODE)
        conn.close()
        if http_code > 299:
            raise Exception(http_code)

    def createWorkspace(self, workspace):
        """Creates, if it does not exist, a workspace in GeoServer.
        Parameters:
            workspace (string): The workspace name.
        """
        try:
            self._get('workspaces/{0}.json'.format(workspace))
        except Exception as e:
            workspace_xml = "<workspace><name>{0}</name></workspace>".format(workspace)
            self._post('workspaces', workspace_xml)

    def createStore(self, name, pg_db, pg_user, pg_password, workspace='default', pg_host='localhost', pg_port='5432', pg_schema='public'):
        """Creates, if it does not exist, a data store to GeoServer.
        Parameters:
            name (string): The store name.
            pg_db (string): The Postgres database.
            pg_user (string): The user with whom it will attempt a connection to DB.
            pg_password (string): The Postgres password for the user.
            workspace (string): The workspace that this store will be attached (if not provided, the default workspace is selected).
            pg_host (string): The Postgres host (default: localhost).
            pg_port (string): The Postgres port (default: 5432).
            pg_schema (string): The Postgres schema (default: public).
        """
        try:
            self._get('workspaces/{0}/datastores/{1}.json'.format(workspace, name))
        except Exception as e:
            endpoint = 'workspaces/{0}/datastores'.format(workspace)
            db_xml = '<dataStore>'\
                '<name>{0}</name>'\
                '<connectionParameters>'\
                '<host>{1}</host>'\
                '<port>{2}</port>'\
                '<database>{3}</database>'\
                '<schema>{4}</schema>'\
                '<user>{5}</user>'\
                '<passwd>{6}</passwd>'\
                '<dbtype>postgis</dbtype>'\
                '</connectionParameters>'\
                '</dataStore>'.format(name, pg_host, pg_port, pg_db, pg_schema, pg_user, pg_password)
            self._post(endpoint, db_xml)

    def publish(self, store, table, workspace='default'):
        """Publishes a layer from a datastore.
        Parameters:
            store (string): The store that will be used.
            table (string): The table from that store that will be published.
            workspace (string): The workspace that the store belongs (if it is not the default).
        """
        layer_xml = "<featureType><name>{0}</name></featureType>".format(table)
        endpoint = 'workspaces/{0}/datastores/{1}/featuretypes'.format(workspace, store)
        self._post(endpoint, layer_xml)

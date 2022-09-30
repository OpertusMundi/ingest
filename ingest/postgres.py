import geopandas as gpd
import pandas as pd
import csv
from shapely import wkt
from geoalchemy2 import Geometry, WKTElement
import sqlalchemy
import shapely
from os import path, environ
import warnings

from .logging import mainLogger
logger = mainLogger.getChild('postgres');

class SchemaException(Exception):
    pass

class InsufficientPrivilege(Exception):
    pass

class Postgres(object):
    """Creates a connection to PostgreSQL database"""
    
    DEFAULT_PORT = 5432
    
    @classmethod
    def makeFromEnv(cls):
        username = environ['POSTGIS_USER']
        
        password = None
        if 'POSTGIS_PASS' in environ:
            password = environ['POSTGIS_PASS']
        elif 'POSTGIS_PASS_FILE' in environ:
            with open(environ['POSTGIS_PASS_FILE'], "r") as f: password = f.read().strip();
        else:
            raise RuntimeError('missing password for PostGis (POSTGIS_PASS or POSTGIS_PASS_FILE)')
        
        url_template = environ['POSTGIS_URL'];
        port_map = dict(((t[0] or None, int(t[1])) for t in 
            (e.split(":") for e in environ.get("POSTGIS_PORT_MAP", "").split(","))));
        default_schema = environ.get("POSTGIS_DEFAULT_SCHEMA", "public");
        
        return Postgres(url_template, username, password, port_map, default_schema);
    
    def __init__(self, url_template, username, password, port_map, default_schema='public'):
        self.url_template = url_template;
        self.username = username;
        self.password = password;
        self.port_map = port_map;
        self.default_schema = default_schema;

    def urlFor(self, shard=None):
        url = self.url_template;
        if shard:
            port = self.port_map.get(shard) or self.DEFAULT_PORT;
            url = self.url_template.format(shard=shard, port=port);
        u = sqlalchemy.engine.url.make_url(url);
        u.username = self.username;
        u.password = self.password;
        return u;
         
    def check(self, shard=None):
        """Check database connection.
        Returns:
            (str) database URI
        """
        url = self.urlFor(shard);
        engine = sqlalchemy.create_engine(url);
        with engine.connect() as con:
            con.execute('SELECT 1')
        return url

    def checkIfTableExists(self, table, schema=None, shard=None):
        """Check if table exists.

        Returns:
            (bool) True if table exists; False otherwise.
        """
        
        schema = schema or self.default_schema
        
        sql_template = """
        SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_schema = '{0}' AND table_name = '{1}')
        """
        url = self.urlFor(shard);
        engine = sqlalchemy.create_engine(url);
        with engine.connect() as con:
            cur = con.execute(sql_template.format(schema, table))
            exists = cur.fetchone()[0]
        return exists

    def dropTable(self, table, schema=None, shard=None):
        """Drop the selected table.
        """
        
        schema = schema or self.default_schema
        
        url = self.urlFor(shard);
        engine = sqlalchemy.create_engine(url);
        with engine.connect() as con:
            cur = con.execute('DROP TABLE IF EXISTS "{0}"."{1}"'.format(schema, table))

    @staticmethod
    def _sniffCsvDelimiter(input_path):
        """ Returns the delimiter of the csv file """
        if input_path.split('.')[-1] != 'csv':
            return None
        with open(input_path) as f:
            first_line = f.readline()
            s = csv.Sniffer()
            return str(s.sniff(first_line).delimiter)

    def _findIndicesOfUniqueFieldsInDataframe(self, df):
        """Identifies unique fields in the dataframe"""
        index = []
        for col in df.columns:
            if col == 'geometry':
                continue
            if df[col].is_unique:
                index.append(col)
        return index

    def ingest(self, input_path, table, schema, shard=None, chunksize=5000, commit=True, replace=False, **kwargs):
        """Creates a DB table and ingests a vector file into it.

        It reads a vector file with geopandas (fiona) and writes the attributes into a database table.
        The table will contain an indexed geometry column, and also indices for the fields identified as
        unique (if they exist). The first of them will be the primary key.

        Parameters:
            input_path (str): The path of the vector file.
            table (str): The table name (it will be created if does not exist).
            schema (str): The database schema
            shard (str): The shard identifier, or None if no sharding is used
            chunksize (int): Number of records that will be read from the file in each turn.
            commit (bool, optional): If False, the database changes will roll back.
            replace (bool, optional): If True, the table will be replace if it exists.
            **kwargs: Additional arguments for GeoPandas read file.

        Returns:
            (tuple) The schema, the table name, and number of rows
        """
        import pyproj
        
        schema = schema or self.default_schema
        
        url = self.urlFor(shard);
        engine = sqlalchemy.create_engine(url);
        
        extension = path.splitext(input_path)[1]
        if extension == '.kml':
            gpd.io.file.fiona.drvsupport.supported_drivers['KML'] = 'r'

        eof = False
        i = 0
        rows = 0
        with engine.connect() as con:
            trans = con.begin()
            # Create schema if not exists
            con.execute('CREATE SCHEMA IF NOT EXISTS "{0}"'.format(schema))
            # Read input
            while not eof:
                with warnings.catch_warnings():
                    warnings.filterwarnings("ignore", category=RuntimeWarning)
                    if extension == ".csv":
                        df = pd.read_csv(input_path, sep=self._sniffCsvDelimiter(input_path))
                        df['geometry'] = df['wkt'].apply(wkt.loads)
                        df.drop('wkt', axis=1, inplace=True)  # Drop WKT column
                        # Geopandas GeoDataFrame
                        df = gpd.GeoDataFrame(df, geometry='geometry')
                    else:
                        df = gpd.read_file(input_path, rows=slice(i*chunksize, (i+1)*chunksize), **kwargs)
                    length = len(df)
                    if length == 0:
                        eof = True
                        continue
                    
                    logger.info("Processing a chunk of %d rows for table %s.%s", length, schema, table)
                    
                    rows = rows + length
                    crs = kwargs.pop('crs', None)
                    crs = pyproj.crs.CRS.from_user_input(crs) if crs is not None else df.crs
                    srid = 4326 if crs is None else crs.to_epsg()

                    if extension == '.kml':
                        df.geometry = df.geometry.map(lambda polygon: shapely.ops.transform(lambda x, y: (x, y), polygon))
                    df['geom'] = df['geometry'].apply(lambda x: WKTElement(x.wkt, srid=srid))
                    if i == 0:
                        indices = self._findIndicesOfUniqueFieldsInDataframe(df)
                        gtype = df.geometry.geom_type.unique()
                        if len(gtype) == 1:
                            gtype = gtype[0]
                        else:
                            gtype = 'GEOMETRY'
                        if_exists = 'fail' if not replace else 'replace'
                    else:
                        if_exists = 'append'
                    df.drop('geometry', 1, inplace=True)
                    try:
                        df.to_sql(table, con=con, schema=schema, if_exists=if_exists, index=False,
                                  dtype={'geom': Geometry(gtype, srid=srid)})
                    except ValueError as e:
                        raise ValueError(e)
                    except sqlalchemy.exc.ProgrammingError as e:
                        if 'InvalidSchemaName' in str(e):
                            raise SchemaException('Schema "%s" does not exist.' % (schema))
                        elif 'InsufficientPrivilege' in str(e):
                            raise InsufficientPrivilege('Permission denied for schema "%s".' % (schema))
                        else:
                            raise e
                    if extension == ".csv":
                        eof = True
                    i += 1
            
            logger.info("Processed all %d rows for table %s.%s", rows, schema, table)    

            if commit:
                trans.commit()

                # Try to create unique indices
                primary = False
                for index in indices:
                    try:
                        if primary == False:
                            con.execute('ALTER TABLE {0}."{1}" ADD PRIMARY KEY ("{2}")'.format(schema, table, index))
                            primary = True
                        else:
                            con.execute('CREATE UNIQUE INDEX ON {0}."{1}" ("{2}")'.format(schema, table, index))
                    except Exception as e:
                        pass
            else:
                trans.rollback()
            trans.close()

        return (schema, table, rows)

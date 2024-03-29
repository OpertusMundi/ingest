from random import sample

import geopandas as gpd
import pandas as pd
import csv
from shapely import wkt
from geoalchemy2 import Geometry, WKTElement
import sqlalchemy
import shapely
from os import path, environ, listdir
import warnings

from valentine.algorithms import Coma
from yaml import safe_load
from valentine import valentine_match

from .logging import mainLogger
logger = mainLogger.getChild('postgres')


class GeometricColumnNotFound(Exception):
    pass


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

        port_map_str = environ.get("POSTGIS_PORT_MAP")
        port_map = {}
        if port_map_str:
            port_map = dict(((t[0] or None, int(t[1])) for t in 
                (e.split(":") for e in port_map_str.split(","))));
        
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
        return u.set(username=self.username, password=self.password);
         
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

    @staticmethod
    def _findCSVGeomColumn(df) -> str:
        """Detect the name of the column containing the geometric information"""
        NUMBER_OF_SAMPLES = 1000
        IS_GEOM_THRESHOLD = 0.9
        SET_OF_ACCEPTABLE_SHAPES = ['point', 'linestring', 'multipoint', 'multilinestring',
                                    'polygon', 'multipolygon', 'geometrycollection', 'linearring']

        def is_geom(column_data):
            if column_data.dtype != 'object':
                return False
            try:
                matches = 0
                for row in sample(list(column_data), NUMBER_OF_SAMPLES):
                    is_shape = row.strip().lower().split('(')[0].strip() in SET_OF_ACCEPTABLE_SHAPES
                    if row.strip().endswith(')') and is_shape:
                        matches += 1
                return matches > IS_GEOM_THRESHOLD * NUMBER_OF_SAMPLES
            except AttributeError:
                return False
        schema = df.columns
        if 'wkt' in schema:
            return 'wkt'
        elif 'WKT' in schema:
            return 'WKT'
        elif 'geometry' in schema:
            return 'geometry'
        elif 'GEOMETRY' in schema:
            return 'GEOMETRY'
        for column in schema:
            if is_geom(df[column]):
                return column
        return 'wkt'

    def ingest(self, input_path, table, schema, shard=None, csv_geom_column_name=None,
               chunksize=5000, commit=True, replace=False, match_into_wks=False, **kwargs):
        """Creates a DB table and ingests a vector file into it.

        It reads a vector file with geopandas (fiona) and writes the attributes into a database table.
        The table will contain an indexed geometry column, and also indices for the fields identified as
        unique (if they exist). The first of them will be the primary key.

        Parameters:
            input_path (str): The path of the vector file.
            table (str): The table name (it will be created if does not exist).
            schema (str): The database schema
            shard (str): The shard identifier, or None if no sharding is used
            csv_geom_column_name (str): The geometric column name in the case of a csv file
            chunksize (int): Number of records that will be read from the file in each turn.
            commit (bool, optional): If False, the database changes will roll back.
            replace (bool, optional): If True, the table will be replace if it exists.
            match_into_wks (bool, optional): If True, the table will be attempted to be matched into a well known schema
            **kwargs: Additional arguments for GeoPandas read file.

        Returns:
            (tuple) The schema, the table name, and number of rows
        """
        import pyproj
        
        schema = schema or self.default_schema
        
        url = self.urlFor(shard)
        engine = sqlalchemy.create_engine(url)
        
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
                        if csv_geom_column_name is None:
                            csv_geom_column_name = self._findCSVGeomColumn(df)
                        try:
                            df['geometry'] = df[csv_geom_column_name].apply(wkt.loads)
                        except KeyError:
                            raise GeometricColumnNotFound(f'{csv_geom_column_name} is not the column containing'
                                                          f' the geometric information')
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
                    if crs is None:
                        crs = df.crs
                    else:
                        try:
                            crs = int(crs)
                        except ValueError:
                            pass
                        crs = pyproj.crs.CRS.from_user_input(crs)
                    
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
                        if match_into_wks:
                            df = match_wks(df)
                        df.to_sql(table, con=con, schema=schema, if_exists=if_exists, index=False,
                                  dtype={'geom': Geometry(gtype, srid=srid)})
                    except ValueError as e:
                        raise e
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
            
            logger.info("Processed all %d rows for table \"%s\".\"%s\" on shard [%s]", rows, schema, table, shard or '')

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


def match_wks(df2):
    max_matches = 0
    max_matches_column_names = tuple()
    for f in listdir('/home/kyriakos/PycharmProjects/profile/geoprofile/schemata'):
        p = path.join('/home/kyriakos/PycharmProjects/profile/geoprofile/schemata', f)
        if path.isfile(p):
            with open(p, 'r') as schema_file:
                column_names = list(pd.json_normalize(safe_load(schema_file)['attributes'])['name'])
                df = pd.DataFrame(columns=column_names)
                matcher = Coma()
                matches = {match: sim for match, sim in valentine_match(df, df2, matcher).items()}
                cleaned_matches = {match[0][1]: match[1][1] for match in matches.keys()}
                if max_matches < len(cleaned_matches):
                    max_matches = len(cleaned_matches)
                    max_matches_column_names = (column_names, cleaned_matches)

    column_names, cleaned_matches = max_matches_column_names
    df = pd.DataFrame(columns=column_names)
    for df1_col, df2_col in cleaned_matches.items():
        df[df1_col] = df2[df2_col]
    return df

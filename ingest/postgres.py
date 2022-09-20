import geopandas as gpd
import pandas as pd
import csv
from shapely import wkt
from geoalchemy2 import Geometry, WKTElement
from sqlalchemy import *
from sqlalchemy.exc import ProgrammingError
import shapely
from os import path, environ
import warnings

class SchemaException(Exception):
    pass
class InsufficientPrivilege(Exception):
    pass

class Postgres(object):
    """Creates a connection to PostgreSQL database.
    Attributes:
        engine (object): The SQLAlchemy pool and dialect to database.
        schema (string): The active database schema.
    """

    def __init__(self, database_url=None, schema=None):
        """The postgres constructor to initiate a (lazy) connection."""
        if database_url is None:
            database_url = 'postgresql://%(POSTGIS_USER)s:%(POSTGIS_PASS)s@%(POSTGIS_HOST)s:%(POSTGIS_PORT)s/%(POSTGIS_DB_NAME)s' % environ
        self.engine = create_engine(database_url)
        if schema is None:
            if environ['POSTGIS_DB_SCHEMA'] is None:
                schema = 'public'
            else:
                schema = environ['POSTGIS_DB_SCHEMA']
        self.schema = schema

    def check(self):
        """Check database engine connection.

        Returns:
            (str) Engine URI
        """
        with self.engine.connect() as con:
            con.execute('SELECT 1')
        return self.engine.url

    def checkIfTableExists(self, table, schema=None):
        """Check if table exists.

        Parameters:
            table (str): Table name
            schema (str, optional): Database schema

        Returns:
            (bool) True if table exists; False otherwise.
        """
        schema = schema or self.schema
        with self.engine.connect() as con:
            cur = con.execute("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_schema = '%s' AND table_name = '%s');" % (schema, table))
            exists = cur.fetchone()[0]
        return exists

    def drop(self, table, schema=None):
        """Drop the selected table.

        Parameters:
            table (str): Table name
            schema (str, optional): Database schema.
        """
        schema = schema or self.schema
        with self.engine.connect() as con:
            cur = con.execute('DROP TABLE IF EXISTS "%s"."%s"' % (schema, table))

    @staticmethod
    def get_delimiter(ds_path: str):
        """ Returns the delimiter of the csv file """
        if ds_path.split('.')[-1] != 'csv':
            return None
        with open(ds_path) as f:
            first_line = f.readline()
            s = csv.Sniffer()
            return str(s.sniff(first_line).delimiter)

    def ingest(self, file, table, schema=None, chunksize=100000, commit=True, replace=False, **kwargs):
        """Creates a DB table and ingests a vector file into it.

        It reads a vector file with geopandas (fiona) and writes the attributes into a database table.
        The table will contain an indexed geometry column, and also indices for the fields identified as
        unique (if they exist). The first of them will be the primary key.

        Parameters:
            file (string): The path of the vector file.
            table (string): The table name (it will be created if does not exist).
            schema (string): The DB schema to be used (if declared, it will bypass the class attribute).
            chunksize (int): Number of records that will be read from the file in each turn.
            commit (bool, optional): If False, the database changes will roll back.
            replace (bool, optional): If True, the table will be replace if it exists.
            **kwargs: Additional arguments for GeoPandas read file.

        Returns:
            (tuple) The schema, the table name, and number of rows
        """
        import pyproj
        schema = schema or self.schema
        extension = path.splitext(file)[1]
        if extension == '.kml':
            gpd.io.file.fiona.drvsupport.supported_drivers['KML'] = 'r'
        eof = False
        i = 0
        rows = 0
        with self.engine.connect() as con:
            trans = con.begin()
            while not eof:
                with warnings.catch_warnings():
                    warnings.filterwarnings("ignore", category=RuntimeWarning)
                    if extension == ".csv":
                        df = pd.read_csv(file, sep=self.get_delimiter(file))
                        df['geometry'] = df['wkt'].apply(wkt.loads)
                        df.drop('wkt', axis=1, inplace=True)  # Drop WKT column
                        # Geopandas GeoDataFrame
                        df = gpd.GeoDataFrame(df, geometry='geometry')
                    else:
                        df = gpd.read_file(file, rows=slice(i*chunksize, (i+1)*chunksize), **kwargs)
                    length = len(df)
                    if length == 0:
                        eof = True
                        continue
                    rows = rows + length
                    crs = kwargs.pop('crs', None)
                    crs = pyproj.crs.CRS.from_user_input(crs) if crs is not None else df.crs
                    srid = 4326 if crs is None else crs.to_epsg()

                    if extension == '.kml':
                        df.geometry = df.geometry.map(lambda polygon: shapely.ops.transform(lambda x, y: (x, y), polygon))
                    df['geom'] = df['geometry'].apply(lambda x: WKTElement(x.wkt, srid=srid))
                    if i == 0:
                        indices = self._findIndex(df)
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
                    except ProgrammingError as e:
                        if 'InvalidSchemaName' in str(e):
                            raise SchemaException('Schema "%s" does not exist.' % (schema))
                        elif 'InsufficientPrivilege' in str(e):
                            raise InsufficientPrivilege('Permission denied for schema "%s".' % (schema))
                        else:
                            raise e
                    if extension == ".csv":
                        eof = True
                    i += 1

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

    def _findIndex(self, df):
        """Identifies unique fields in the dataframe"""
        index = []
        for col in df.columns:
            if col == 'geometry':
                continue
            if df[col].is_unique:
                index.append(col)
        return index

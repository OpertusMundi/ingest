import setuptools

setuptools.setup(
    name='ingest',
    version='0.1',
    description='Ingest shp/kml into postgis/geoserver microservice',
    author='Pantelis Mitropoulos',
    author_email='pmitropoulos@getmap.gr',
    license='MIT',
    packages=setuptools.find_packages(),
    install_requires=[
        'geopandas>=0.8.1,<0.8.2',
        'Flask>=1.1.2,<1.1.3',
        'flask-executor>=0.9.3,<0.9.4',
        'sqlalchemy>=1.3.19,<1.4',
        'geoalchemy2>=0.8.4,<0.8.5',
        'psycopg2-binary>=2.8.5,<2.8.6',
        'shapely>=1.7.0,<1.7.1',
        'pycurl>=7.43.0.6,<7.43.1',
    ],
    python_requires='>=3.7',
    zip_safe=False,
)
import setuptools

setuptools.setup(
    name='ingest',
    version='0.2.1',
    description='Ingest SHP/KML into postgis/geoserver',
    author='Pantelis Mitropoulos',
    author_email='pmitropoulos@getmap.gr',
    license='MIT',
    packages=setuptools.find_packages(exclude=('tests*',)),
    install_requires=[
        # moved to requirements.txt
    ],
    package_data={'ingest': [
        'logging.conf', 'schema.sql'
    ]},
    python_requires='>=3.7',
    zip_safe=False,
)

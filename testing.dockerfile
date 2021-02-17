# vim: set syntax=dockerfile:

# see https://github.com/OpertusMundi/docker-library/blob/gdal-python-builder/gdal-python-builder/Dockerfile
FROM opertusmundi/gdal-python-builder:1-3.1 as build-stage-1

FROM osgeo/gdal:alpine-normal-3.1.0
ARG VERSION

RUN apk update && \
  apk add --no-cache sqlite openssl postgresql-dev curl py3-yaml py3-numpy py3-psycopg2 py3-sqlalchemy

ENV VERSION="${VERSION}"
ENV PYTHON_VERSION="3.8"
ENV PYTHONPATH="/usr/local/lib/python${PYTHON_VERSION}/site-packages"

COPY --from=build-stage-1 /usr/local/ /usr/local/

COPY setup.py requirements.txt requirements-testing.txt ./
RUN pip3 install --upgrade pip && \
  pip3 install --no-cache-dir --prefix=/usr/local -r requirements.txt -r requirements-testing.txt

ENV FLASK_APP="ingest" \
    FLASK_ENV="testing" \
    FLASK_DEBUG="false" \
    TEMPDIR="" 

COPY run-nosetests.sh /
RUN chmod a+x /run-nosetests.sh
ENTRYPOINT ["/run-nosetests.sh"]

# vim: set syntax=dockerfile:

FROM osgeo/gdal:alpine-normal-3.1.0 as build-stage-1

RUN apk update && \
  apk add gcc g++ musl-dev python3-dev openssl openssl-dev curl curl-dev geos geos-dev py3-numpy py3-numpy-dev
RUN pip3 install --upgrade pip && \
  pip3 install --prefix=/usr/local "pycurl>=7.43.0.6,<7.43.1" "pyproj==2.2.0" "pandas==0.23.0" "geopandas==0.8.1"


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

ENV FLASK_APP="ingest" FLASK_ENV="testing" FLASK_DEBUG="false"
ENV TEMPDIR="" SECRET_KEY_FILE="./secret_key" TLS_CERTIFICATE="" TLS_KEY=""

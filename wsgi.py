#!/usr/bin/env python
import os
import logging
import logging.config

# Configure logging before loading the application module
# see https://flask.palletsprojects.com/en/1.1.x/logging/#basic-configuration

logging.config.fileConfig(os.getenv('LOGGING_FILE_CONFIG'));

from ingest.app import app

if __name__ == '__main__':
    ssl_context = None
    port = 5000
    tls_cert = os.environ.get('TLS_CERTIFICATE');
    tls_key = os.environ.get('TLS_KEY');
    if tls_cert and tls_key:
        ssl_context = (tls_cert, tls_key);
        port = 5443;
    # Run development server
    app.run(host="0.0.0.0", port=port, ssl_context=ssl_context);

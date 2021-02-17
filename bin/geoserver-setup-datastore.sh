#!/bin/sh

set -e
set -u

function _generate_request_for_datastore()
{
    cat  <<-EOD
	<dataStore>
	    <name>${GEOSERVER_STORE}</name>
	    <connectionParameters>
	        <dbtype>postgis</dbtype>
	        <host>${POSTGRES_HOST}</host>
	        <port>${POSTGRES_PORT}</port>
	        <database>${DATASTORE_DATABASE}</database> 
	        <user>${DATASTORE_USER}</user>
	        <passwd>${DATASTORE_PASSWORD}</passwd> 
	    </connectionParameters>
	</dataStore>
	EOD
}

# Create datastore in given workspace 
# see https://docs.geoserver.org/stable/en/user/rest/

_generate_request_for_datastore | \
    curl -s -S --netrc -H "content-type: application/xml" -d @- -X POST ${GEOSERVER_URL}/rest/workspaces/${GEOSERVER_WORKSPACE}/datastores | \
    tee && echo

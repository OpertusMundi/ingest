#!/bin/sh

set -e
set -u

function _generate_request_for_workspace()
{
    cat  <<-EOD
	<workspace>
	    <name>${GEOSERVER_WORKSPACE}</name>
	</workspace>
	EOD
}

# Create workspace 
# see https://docs.geoserver.org/stable/en/user/rest/

_generate_request_for_workspace | \
    curl -s -S --netrc -H "content-type: application/xml" -d @- -X POST ${GEOSERVER_URL}/rest/workspaces | \
    tee && echo

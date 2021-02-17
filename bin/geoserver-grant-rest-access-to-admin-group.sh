#!/bin/sh

set -e
set -u

function _generate_request_for_security_acls()
{
    cat <<-EOD
	<rules>
	    <rule resource="/**:GET">ADMIN,GROUP_ADMIN</rule>
	    <rule resource="/**:POST,DELETE,PUT">ADMIN,GROUP_ADMIN</rule>
	</rules>
	EOD
}

# Grant access to the REST API to all members of GROUP_ADMIN
# see https://docs.geoserver.org/stable/en/api/#1.0.0/security.yaml

_generate_request_for_security_acls | \
    curl -s -S --netrc -H 'content-type: application/xml' -d @- -X PUT ${GEOSERVER_URL}/rest/security/acl/rest

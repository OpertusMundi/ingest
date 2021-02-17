#!/bin/sh

set -e
set -u

function _generate_request_for_new_user()
{
    cat  <<-EOD
	<user>
	    <userName>${GEOSERVER_USER}</userName>
	    <password>${GEOSERVER_PASSWORD}</password>
	    <enabled>true</enabled>
	</user>
	EOD
}

# Create new user
# see https://docs.geoserver.org/stable/en/api/#1.0.0/usergroup.yaml

_generate_request_for_new_user | \
    curl -s -S --netrc -H "content-type: application/xml" -d @- -X POST ${GEOSERVER_URL}/rest/security/usergroup/users 

# Add user in GROUP_ADMIN (to be able to ingest into a datastore)
# see https://docs.geoserver.org/stable/en/api/#1.0.0/roles.yaml 

curl -s -S --netrc -X POST ${GEOSERVER_URL}/rest/security/roles/role/GROUP_ADMIN/user/${GEOSERVER_USER} 

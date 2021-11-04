from flask import current_app as app
import click

@app.cli.command()
def init_db():
	"""Initialize database."""
	from ingest.database import db
	db.create_all()

@app.cli.command()
@click.argument("path")
def create_doc(path):
    """Write OpenAPI documentation to file.

    Arguments:
        path (str): Destination of documentation file (including filename).
    """
    import json
    from ingest.app import spec
    with open(path, 'w') as specfile:
        json.dump(spec.to_dict(), specfile)
    print("Wrote OpenAPI specification to {path}.".format(path=path))

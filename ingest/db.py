import sqlite3

import click
from flask import current_app, g
from flask.cli import with_appcontext

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(
            current_app.config['DATABASE'],
            detect_types=sqlite3.PARSE_DECLTYPES
        )
        g.db.row_factory = sqlite3.Row

    return g.db


def close_db(e=None):
    db = g.pop('db', None)

    if db is not None:
        db.close()

def init_db():
    db = get_db()

    with current_app.open_resource('schema.sql') as f:
        db.executescript(f.read().decode('utf8'))


@click.command('init-db')
@click.option('-f', '--force', is_flag=True, show_default=True, help='Clean the database if exists.')
@with_appcontext
def init_db_command(force):
    """Initialize database."""
    dbc = get_db()
    table = dbc.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tickets';").fetchone()
    if table is None or force:
        init_db()
        click.echo('Initialized the database.')
    else:
        click.echo('Database already exists.')

def init_app(app):
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)

from . import db

def create_app():
    from . import app
    return app.app

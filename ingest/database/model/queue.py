from sqlalchemy import ForeignKey, event
from sqlalchemy.sql import expression
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import func
from sqlalchemy.schema import DDL
import uuid
from hashlib import md5
from ingest.database import db

class Queue(db.Model):
    """Queue Model

    Extends:
        db.Model

    Attributes:
        id (int): Primary Key.
        ticket (str): The ticket assigned to the request.
        idempotency_key (str): An idempotency key sent along with the request.
        request (str): The request type.
        initiated (datetime): The timestamp of the request.
        execution_time (float): The execution time in seconds.
        completed (bool): Whether the process has been completed.
        success (bool): The status of the process.
        error_msg (str): The error message in case of failure.
        result (str): The path of the result.
        rows (int): Number of records.
    """
    __tablename__ = "ingest_queue"
    id = db.Column(db.BigInteger(), primary_key=True)
    ticket = db.Column(db.String(511), default=lambda: md5(str(uuid.uuid4()).encode()).hexdigest(), nullable=False, unique=True)
    idempotency_key = db.Column(db.String(511), nullable=True, unique=True)
    request = db.Column(db.String(511), nullable=False)
    initiated = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    execution_time = db.Column(db.Float(), nullable=True)
    completed = db.Column(db.Boolean(), server_default=expression.false(), nullable=False)
    success = db.Column(db.Boolean(), nullable=True)
    error_msg = db.Column(db.Text(), nullable=True)
    result = db.Column(db.Text(), nullable=True)
    rows = db.Column(db.Integer(), nullable=True)

    def __iter__(self):
        for key in ['ticket', 'idempotency_key', 'request', 'initiated', 'execution_time', 'completed', 'success', 'error_msg', 'result', 'rows']:
            yield (key, getattr(self, key))

    def get(self, **kwargs):
        queue = self.query.filter_by(**kwargs).first()
        if queue is None:
            return None
        return dict(queue)

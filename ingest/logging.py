import os
import sys
import traceback
import logging
import datetime
from itertools import chain
from flask import has_request_context, request


APP_NAME = os.getenv('FLASK_APP')

#
# Utilities
#

def exception_as_rfc5424_structured_data(ex):
    
    tb = traceback.format_exception(*sys.exc_info());
    
    return {
        'structured_data': {
            'mdc': {
                'exception-message': str(ex),
                'exception': '|'.join(chain.from_iterable((s.splitlines() for s in tb[1:]))),
            }
        }
    };

#
# Context filters for loggers
#

class AccountingContextFilter(logging.Filter):
    """A filter injecting contextual information for accounting purposes"""

    _ATTRS_ = [
        'remote_addr', 'method', 'path', 'remote_user', 'authorization', 'content_length', 'referrer', 'user_agent'
    ]
    
    def filter(self, record):
        in_request = has_request_context()
        for attr in self._ATTRS_:
            if in_request:
                value = getattr(request, attr)
                if value is not None:
                    setattr(record, attr, value)
                else:
                    setattr(record, attr, '-')
            else:
                setattr(record, attr, None)
        return True


class Rfc5424MdcContextFilter(logging.Filter):
    """A filter injecting diagnostic context suitable for RFC5424 messages"""
    
    def filter(self, record):
        record.msgid = APP_NAME
        if not hasattr(record, 'structured_data'):
            record.structured_data = {'mdc': {}}
        mdc = record.structured_data.get('mdc') 
        if mdc is None:
            mdc = record.structured_data['mdc'] = {}
        mdc.update({
            'logger': record.name,
            'thread': record.threadName
        })
        return True;

#
# Initialize loggers in module level
#

mainLogger = logging.getLogger(APP_NAME)
mainLogger.addFilter(Rfc5424MdcContextFilter())

_accountingLogger = logging.getLogger(APP_NAME + '.accounting')
_accountingLogger.addFilter(AccountingContextFilter())

def accountingLogger(execution_start, execution_time, rows=None, ticket='-', success=1, comment=None):
    assert isinstance(execution_start, datetime.date)
    success = bool(success)
    execution_start = execution_start.strftime("%Y-%m-%d %H:%M:%S")
    _accountingLogger.info("ticket=%s, success=%s, execution_start=%s, execution_time=%ss, comment=%s, rows=%s", 
        ticket, success, execution_start, execution_time, comment, rows)



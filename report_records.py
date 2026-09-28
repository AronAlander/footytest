"""Stage records during rendering; persist only after a successful publish build.

Rendering an individual panel is read-only. A capture context also lets later
panels read today's staged snapshot without writing either committed log.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy

_pending = ContextVar('report_records', default=None)


def load(module):
    pending = _pending.get()
    if pending is not None and module in pending:
        return deepcopy(pending[module])
    return module.load()


def stage(module, rows):
    pending = _pending.get()
    if pending is not None:
        pending[module] = deepcopy(rows)


@contextmanager
def capture():
    pending = {}
    token = _pending.set(pending)
    try:
        yield pending
    finally:
        _pending.reset(token)


def persist(pending):
    for module, rows in pending.items():
        module.save(rows)

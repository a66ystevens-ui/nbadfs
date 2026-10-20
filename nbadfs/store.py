import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from contextvars import ContextVar

_session_path = ContextVar('courtvision_database',default=None)


@contextmanager
def session_database(path):
    token = _session_path.set(path)
    try:
        yield
    finally:
        _session_path.reset(token)


@contextmanager
def database():
    path = Path(_session_path.get() or os.environ.get('NBADFS_DB', 'data/research.sqlite'))
    path.parent.mkdir(parents=True,exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, received_at TEXT, as_of TEXT, request TEXT, result TEXT)')
    try:
        with db:
            yield db
    finally:
        db.close()


def save_run(request, result):
    run_id = uuid4().hex
    with database() as db:
        db.execute('INSERT INTO runs VALUES (?,?,?,?,?)',
                   (run_id,datetime.now(timezone.utc).isoformat(),request.as_of.isoformat(),
                    request.model_dump_json(),json.dumps(result,allow_nan=False)))
    return run_id


def load_run(run_id):
    with database() as db:
        row = db.execute('SELECT result FROM runs WHERE id=?',(run_id,)).fetchone()
    if not row:
        raise KeyError(run_id)
    return json.loads(row[0])


def load_record(run_id):
    with database() as db:
        row = db.execute('SELECT received_at, as_of, request, result FROM runs WHERE id=?',(run_id,)).fetchone()
    if not row:
        raise KeyError(run_id)
    return dict(run_id=run_id, received_at=row[0], as_of=row[1],
                request=json.loads(row[2]), result=json.loads(row[3]))

"""Append-only source observations. Receipt time is assigned by this process."""
import hashlib
import json
from datetime import datetime, timezone
from uuid import uuid4

from .models import utc
from .store import database

SCHEMA = '''
CREATE TABLE IF NOT EXISTS source_blobs (
    sha256 TEXT PRIMARY KEY, payload BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS source_snapshots (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
    provider TEXT NOT NULL, feed TEXT NOT NULL, resource TEXT NOT NULL,
    received_at TEXT NOT NULL, source_generated_at TEXT,
    published_at TEXT, status INTEGER NOT NULL, content_type TEXT,
    etag TEXT, last_modified TEXT, sha256 TEXT NOT NULL REFERENCES source_blobs(sha256)
);
CREATE INDEX IF NOT EXISTS snapshots_lookup ON source_snapshots(provider,feed,resource,received_at);
CREATE TABLE IF NOT EXISTS source_records (
    snapshot_id TEXT NOT NULL REFERENCES source_snapshots(id),
    ordinal INTEGER NOT NULL, kind TEXT NOT NULL, entity_id TEXT NOT NULL,
    data TEXT NOT NULL, PRIMARY KEY(snapshot_id,ordinal)
);
CREATE TABLE IF NOT EXISTS source_attempts (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
    provider TEXT NOT NULL, feed TEXT NOT NULL, resource TEXT NOT NULL,
    attempted_at TEXT NOT NULL, outcome TEXT NOT NULL,
    snapshot_id TEXT, detail TEXT, retry_after REAL
);
'''


def now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def initialize(db):
    # Schema operations participate in the caller's transaction (no executescript commit).
    for statement in SCHEMA.split(';'):
        if statement.strip():
            db.execute(statement)
    for table in ('source_blobs','source_snapshots','source_records','source_attempts'):
        for operation in ('UPDATE','DELETE'):
            db.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{operation.lower()} "
                       f"BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'Source archive is append-only'); END")


def capture(provider, feed, resource, payload, *, status=200, headers=None,
            published_at=None, source_generated_at=None):
    """Deduplicate bytes, but retain every observation, even unchanged responses."""
    headers = headers or {}
    received_at = now()
    digest = hashlib.sha256(payload).hexdigest()
    snapshot_id = uuid4().hex
    with database() as db:
        initialize(db)
        # Do not use REPLACE: that could erase an earlier source observation.
        db.execute('INSERT OR IGNORE INTO source_blobs VALUES (?,?)',(digest,payload))
        db.execute('''INSERT INTO source_snapshots
            (id,provider,feed,resource,received_at,source_generated_at,published_at,status,
             content_type,etag,last_modified,sha256) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
            (snapshot_id,provider,feed,resource,received_at,source_generated_at,published_at,status,
             headers.get('content-type'),headers.get('etag'),headers.get('last-modified'),digest))
    return snapshot_id


def append_records(snapshot_id, records):
    with database() as db:
        initialize(db)
        db.executemany('INSERT INTO source_records VALUES (?,?,?,?,?)',
            [(snapshot_id,i,row['kind'],str(row['entity_id']),json.dumps(row,allow_nan=False))
             for i,row in enumerate(records)])


def attempt(provider, feed, resource, outcome, *, snapshot_id=None, detail=None, retry_after=None):
    with database() as db:
        initialize(db)
        db.execute('''INSERT INTO source_attempts
            (id,provider,feed,resource,attempted_at,outcome,snapshot_id,detail,retry_after)
            VALUES (?,?,?,?,?,?,?,?,?)''',
            (uuid4().hex,provider,feed,resource,now(),outcome,snapshot_id,detail,retry_after))


def _metadata(row):
    if row is None:
        raise KeyError('Snapshot not found')
    return dict(zip(('sequence','id','provider','feed','resource','received_at',
                     'source_generated_at','published_at','status','content_type','etag',
                     'last_modified','sha256'),row))


def snapshot(snapshot_id, include_payload=True):
    with database() as db:
        initialize(db)
        result = _metadata(db.execute('SELECT * FROM source_snapshots WHERE id=?',(snapshot_id,)).fetchone())
        result['records'] = [json.loads(r[0]) for r in db.execute(
            'SELECT data FROM source_records WHERE snapshot_id=? ORDER BY ordinal',(snapshot_id,))]
        if include_payload:
            payload = db.execute('SELECT payload FROM source_blobs WHERE sha256=?',(result['sha256'],)).fetchone()[0]
            if hashlib.sha256(payload).hexdigest() != result['sha256']:
                raise ValueError('Archived content failed its integrity check')
            result['payload'] = payload
    return result


def list_snapshots(limit=30, before=None):
    with database() as db:
        initialize(db)
        rows = db.execute('SELECT * FROM source_snapshots WHERE sequence < ? ORDER BY sequence DESC LIMIT ?',
                          (before if before is not None else 2**63-1,limit)).fetchall()
    return [_metadata(row) for row in rows]


def as_of(provider, feed, resource, at):
    at = utc(at).isoformat(timespec='microseconds')
    with database() as db:
        initialize(db)
        # Exclude HTTP failures and parse failures. Never mistake a corrected historical
        # payload received today for something known before today's receipt time.
        row = db.execute('''SELECT s.* FROM source_snapshots s
            WHERE provider=? AND feed=? AND resource=? AND received_at<=?
            AND (published_at IS NULL OR published_at<=?) AND status=200
            AND EXISTS (SELECT 1 FROM source_attempts a WHERE a.snapshot_id=s.id AND a.outcome='success')
            ORDER BY received_at DESC,sequence DESC LIMIT 1''',(provider,feed,resource,at,at)).fetchone()
    if row is None:
        raise KeyError('No successful snapshot known at that time')
    return snapshot(_metadata(row)['id'],include_payload=False)


def status():
    with database() as db:
        initialize(db)
        counts = {name: db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                  for name,table in [('snapshots','source_snapshots'),('unique_payloads','source_blobs'),
                                     ('records','source_records'),('attempts','source_attempts')]}
        rows = db.execute('''SELECT provider,feed,resource,attempted_at,outcome,detail,snapshot_id
            FROM source_attempts ORDER BY sequence DESC LIMIT 20''').fetchall()
    return counts | {'recent_attempts':[dict(zip(('provider','feed','resource','attempted_at',
                 'outcome','detail','snapshot_id'),row)) for row in rows]}


def archive_csv(feed, text):
    sid = capture('upload',feed,'manual',text.encode('utf-8'),headers={'content-type':'text/csv; charset=utf-8'})
    return sid


def validated_reference(snapshot_id, at):
    item = snapshot(snapshot_id,include_payload=False)
    if datetime.fromisoformat(item['received_at']) > utc(at):
        raise ValueError('Source snapshot was received after the forecast as_of time')
    if item['published_at'] and datetime.fromisoformat(item['published_at']) > utc(at):
        raise ValueError('Source snapshot was published after the forecast as_of time')
    with database() as db:
        valid = db.execute("SELECT 1 FROM source_attempts WHERE snapshot_id=? AND outcome='success' LIMIT 1",(snapshot_id,)).fetchone()
    if not valid or item['status'] != 200:
        raise ValueError('Source snapshot did not complete a successful import or normalization')
    return {k:item[k] for k in ('id','provider','feed','resource','received_at','sha256')}

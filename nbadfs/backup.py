"""Portable data-only archive checkpoints. Never deserialize executable objects."""
import base64
import gzip
import hashlib
import io
import json
import math
import sqlite3

from .archive import initialize
from .store import database

MAX_SIZE = 50_000_000
TABLES = {
    'source_blobs':('sha256','payload'),
    'source_snapshots':('id','provider','feed','resource','received_at','source_generated_at',
                        'published_at','status','content_type','etag','last_modified','sha256'),
    'source_records':('snapshot_id','ordinal','kind','entity_id','data'),
    'source_attempts':('id','provider','feed','resource','attempted_at','outcome','snapshot_id','detail','retry_after'),
    'runs':('id','received_at','as_of','request','result')}


def identity(table, row):
    return str(row[0])+':'+str(row[1]) if table == 'source_records' else str(row[0])


def export_checkpoint(known=None):
    known=known or {}
    data={}
    keys={}
    with database() as db:
        initialize(db)
        for table,columns in TABLES.items():
            rows=db.execute(f'SELECT {",".join(columns)} FROM {table}').fetchall()
            keys[table]={identity(table,r) for r in rows}
            selected=[list(r) for r in rows if identity(table,r) not in known.get(table,set())]
            if table == 'source_blobs':
                for row in selected:
                    row[1]=base64.b64encode(row[1]).decode('ascii')
            data[table]=selected
    payload=json.dumps({'version':1,'tables':data},allow_nan=False,separators=(',',':')).encode()
    if len(payload)>MAX_SIZE:
        raise ValueError('Checkpoint exceeds the 50 MB limit; reduce the imported archive or use dedicated storage')
    return gzip.compress(payload,mtime=0),keys,sum(len(v) for v in data.values())


def _restore_checkpoint(payload):
    if len(payload)>MAX_SIZE:
        raise ValueError('Compressed checkpoint exceeds size limit')
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(payload)) as stream:
            raw=stream.read(MAX_SIZE+1)
        if len(raw)>MAX_SIZE:
            raise ValueError('Expanded checkpoint exceeds size limit')
        parsed=json.loads(raw)
    except (OSError,EOFError,ValueError) as exc:
        raise ValueError('Invalid or oversized archive checkpoint') from None
    if not isinstance(parsed,dict) or parsed.get('version') != 1 or set(parsed.get('tables',{})) != set(TABLES):
        raise ValueError('Unsupported archive checkpoint schema')
    decoded={}
    for table,columns in TABLES.items():
        rows=parsed['tables'][table]
        if not isinstance(rows,list):
            raise ValueError('Invalid checkpoint table')
        decoded[table]=[]
        for row in rows:
            if not isinstance(row,list) or len(row)!=len(columns) or any(not isinstance(v,(str,int,float,type(None))) for v in row):
                raise ValueError('Invalid checkpoint row')
            if any(isinstance(v,float) and not math.isfinite(v) for v in row):
                raise ValueError('Invalid non-finite checkpoint value')
            if table=='source_blobs':
                try:
                    row[1]=base64.b64decode(row[1],validate=True)
                except (ValueError,TypeError):
                    raise ValueError('Invalid archived payload encoding') from None
                if hashlib.sha256(row[1]).hexdigest()!=row[0]:
                    raise ValueError('Archived payload failed its SHA-256 check')
            decoded[table].append(row)
    # All tables are restored in one transaction; source data cannot be overwritten.
    count=0
    with database() as db:
        initialize(db)
        for table,columns in TABLES.items():
            for row in decoded[table]:
                key_columns=columns[:2] if table=='source_records' else columns[:1]
                existing=db.execute(f'SELECT {",".join(columns)} FROM {table} WHERE '+
                    ' AND '.join(c+'=?' for c in key_columns),row[:len(key_columns)]).fetchone()
                if existing is not None:
                    if tuple(row)!=existing:
                        raise ValueError('Checkpoint conflicts with existing immutable data')
                    continue
                db.execute(f'INSERT INTO {table} ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})',row)
                count+=1
    return count


def restore_checkpoint(payload):
    try:
        return _restore_checkpoint(payload)
    except sqlite3.DatabaseError:
        raise ValueError('Checkpoint has invalid or missing linked records; no rows were overwritten') from None

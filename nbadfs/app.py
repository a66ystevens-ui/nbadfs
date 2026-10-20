import csv
import io
from pathlib import Path
from threading import Lock

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import Field

from .ingest import history_rows, parse_players, walk_forward
from .models import RunRequest, StrictModel, SLOTS
from .pipeline import run
from .store import load_record, load_run, save_run
from . import archive
from .providers import FeedError, SyncRequest, sync
from .slate import SlateRequest, build_slate
from . import credentials

app = FastAPI(title='CourtVision · NBA DFS Research', version='0.1.0')
static = Path(__file__).parent / 'static'
app.mount('/static',StaticFiles(directory=static),name='static')
run_lock = Lock()
sync_lock = Lock()


@app.get('/')
def index():
    return FileResponse(static / 'index.html')


@app.get('/api/health')
def health():
    return {'status':'ok','version':'0.1.0'}


class ImportRequest(StrictModel):
    csv_text: str = Field(max_length=2_000_000)
    history_csv: str = Field(default='',max_length=10_000_000)
    as_of: str


@app.post('/api/import')
def import_csv(request: ImportRequest):
    from datetime import datetime
    from .models import utc
    ids = [archive.archive_csv('player_csv',request.csv_text)]
    if request.history_csv:
        ids.append(archive.archive_csv('history_csv',request.history_csv))
    try:
        players = parse_players(request.csv_text,utc(datetime.fromisoformat(request.as_of)),request.history_csv)
        for sid in ids:
            item = archive.snapshot(sid,include_payload=False)
            archive.attempt('upload',item['feed'],'manual','success',snapshot_id=sid)
        return {'players':[p.model_dump(mode='json') for p in players],'source_snapshot_ids':ids}
    except ValueError as exc:
        for sid in ids:
            item = archive.snapshot(sid,include_payload=False)
            archive.attempt('upload',item['feed'],'manual','normalization_error',snapshot_id=sid,detail='CSV validation failed')
        raise HTTPException(422,str(exc)) from exc


class BacktestRequest(StrictModel):
    history_csv: str = Field(max_length=10_000_000)


@app.post('/api/backtest')
def backtest(request: BacktestRequest):
    sid = archive.archive_csv('history_csv',request.history_csv)
    try:
        result = walk_forward(history_rows(request.history_csv))
        archive.attempt('upload','history_csv','manual','success',snapshot_id=sid)
        return result | {'source_snapshot_ids':[sid]}
    except ValueError as exc:
        archive.attempt('upload','history_csv','manual','normalization_error',snapshot_id=sid,detail='History validation failed')
        raise HTTPException(422,str(exc)) from exc


@app.post('/api/run')
def build(request: RunRequest):
    if not run_lock.acquire(blocking=False):
        raise HTTPException(409,'A simulation is already running; retry when it completes')
    try:
        sources = []
        for sid in request.source_snapshot_ids:
            try:
                sources.append(archive.validated_reference(sid,request.as_of))
            except KeyError:
                raise ValueError('Unknown source snapshot ID') from None
        result = run(request)
        result['source_snapshots'] = sources
        # Persist exact expanded demo inputs as well as all settings for replay.
        if request.players is None:
            from .demo import demo_players
            saved_request = request.model_copy(update={'players':demo_players(request.as_of)})
        else:
            saved_request = request
        result['run_id'] = save_run(saved_request,result)
        return result
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc
    finally:
        run_lock.release()


@app.get('/api/data/status')
def data_status():
    return archive.status() | {'providers':{
        'nba':{'key_required':False,'host':'cdn.nba.com'},
        'sportsdataio':{'key_required':True,**credentials.status(),
                       'host':'api.sportsdata.io'}},
        'polling':'Use python -m nbadfs.collector --help; no collector starts automatically'}


@app.post('/api/data/credentials/sportsdataio')
async def session_key(request: Request):
    # Parse manually: validation error objects must never echo a rejected secret.
    # Require JSON and a same-origin browser request when Origin is present.
    origin = request.headers.get('origin')
    if origin and origin != f'{request.url.scheme}://{request.url.netloc}':
        raise HTTPException(403,'Cross-origin credential updates are not allowed')
    if request.headers.get('content-type','').split(';')[0].strip() != 'application/json':
        raise HTTPException(415,'Use application/json')
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body)>1024:
            raise HTTPException(413,'Credential request is too large')
    import json
    import re
    try:
        data=json.loads(body)
    except (ValueError,UnicodeError):
        raise HTTPException(422,'Invalid credential request') from None
    if not isinstance(data,dict) or set(data) != {'key'}:
        raise HTTPException(422,'Supply exactly the key field')
    value=data['key']
    if value is not None and (not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,256}',value)):
        raise HTTPException(422,'Invalid key format')
    credentials.set_sportsdataio_key(value)
    return credentials.status() | {'storage':'server memory only; cleared on restart'}


@app.post('/api/data/slate')
def provider_slate(request: SlateRequest):
    sid = archive.archive_csv('player_csv',request.csv_text)
    try:
        result = build_slate(request)
        archive.attempt('upload','player_csv','manual','success',snapshot_id=sid)
        result['source_snapshot_ids'].append(sid)
        return result
    except (KeyError,ValueError,TypeError) as exc:
        archive.attempt('upload','player_csv','manual','normalization_error',snapshot_id=sid,detail='Provider slate join failed')
        raise HTTPException(422,str(exc)) from None


@app.post('/api/data/sync')
def sync_data(request: SyncRequest):
    if not sync_lock.acquire(blocking=False):
        raise HTTPException(409,'A feed refresh is already running')
    try:
        return sync(request)
    except FeedError as exc:
        raise HTTPException(502,{'message':str(exc),'snapshot_id':exc.snapshot_id,'retry_after':exc.retry_after}) from None
    finally:
        sync_lock.release()


@app.get('/api/data/snapshots')
def snapshots(limit: int = Query(30,ge=1,le=100), before: int | None = Query(None,ge=1)):
    rows = archive.list_snapshots(limit,before)
    return {'snapshots':rows,'next_before':rows[-1]['sequence'] if rows else None}


@app.get('/api/data/as-of')
def known_at(provider: str, feed: str, resource: str, at: str):
    from datetime import datetime
    try:
        return archive.as_of(provider,feed,resource,datetime.fromisoformat(at))
    except KeyError as exc:
        raise HTTPException(404,str(exc)) from None
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from None


@app.get('/api/data/snapshots/{snapshot_id}')
def source_snapshot(snapshot_id: str):
    try:
        return archive.snapshot(snapshot_id,include_payload=False)
    except KeyError:
        raise HTTPException(404,'Snapshot not found') from None


@app.get('/api/data/snapshots/{snapshot_id}/raw')
def source_payload(snapshot_id: str):
    try:
        item = archive.snapshot(snapshot_id)
    except KeyError:
        raise HTTPException(404,'Snapshot not found') from None
    return Response(item['payload'],media_type='application/octet-stream',headers={
        'Content-Disposition':f'attachment; filename="{item["id"]}.bin"',
        'X-Content-SHA256':item['sha256']})


@app.get('/api/runs/{run_id}')
def record(run_id: str):
    try:
        return load_record(run_id)
    except KeyError as exc:
        raise HTTPException(404,'Run not found') from exc


@app.get('/api/runs/{run_id}/export')
def export(run_id: str):
    try:
        result = load_run(run_id)
    except KeyError as exc:
        raise HTTPException(404,'Run not found') from exc
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(SLOTS)
    for lineup in result['lineups']:
        values = [lineup['slots'][s] for s in SLOTS]
        # Avoid spreadsheet formula execution for untrusted imported IDs.
        writer.writerow(["'"+v if v.startswith(('=','+','-','@','\t','\r')) else v for v in values])
    prefix = 'synthetic-demo' if result['synthetic'] else 'nba-classic'
    return Response(output.getvalue(),media_type='text/csv',
                    headers={'Content-Disposition':f'attachment; filename="{prefix}-lineups.csv"'})

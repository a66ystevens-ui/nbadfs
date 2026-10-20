import json
import io
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from nbadfs import archive
from nbadfs import credentials
from nbadfs.app import app
from nbadfs.providers import FeedError, SyncRequest, normalize_sportsdataio, sync
from nbadfs.store import database
from nbadfs.slate import SlateRequest, build_slate


def game(score=0):
    return {'gameId':'0022600001','gameTimeUTC':'2026-10-20T23:00:00Z','gameStatus':1,
        'homeTeam':{'teamTricode':'BOS','score':score},'awayTeam':{'teamTricode':'ATL','score':0}}


class DataTests(unittest.TestCase):
    def setUp(self):
        credentials.set_sportsdataio_key(None)
        self.directory = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ,{'NBADFS_DB':str(Path(self.directory.name)/'sources.sqlite')})
        self.env.start()

    def tearDown(self):
        credentials.set_sportsdataio_key(None)
        self.env.stop()
        self.directory.cleanup()

    def client(self, payload, status=200, headers=None):
        def handle(request):
            return httpx.Response(status,content=payload,headers=headers)
        return httpx.Client(transport=httpx.MockTransport(handle))

    def test_unchanged_polls_and_corrections_are_not_overwritten(self):
        first = json.dumps({'scoreboard':{'games':[game()]}}).encode()
        second = json.dumps({'scoreboard':{'games':[game(2)]}}).encode()
        ids=[]
        for data in [first,first,second]:
            with self.client(data) as client:
                ids.append(sync(SyncRequest(),client=client)['snapshot']['id'])
        self.assertEqual(len(set(ids)),3)
        self.assertEqual(archive.status()['unique_payloads'],2)
        self.assertEqual(archive.snapshot(ids[0])['payload'],first)
        self.assertEqual(archive.snapshot(ids[2])['records'][0]['home_score'],2)
        with database() as db:
            for table in ['source_snapshots','source_blobs','source_records','source_attempts']:
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute(f'DELETE FROM {table}')
                with self.assertRaises(sqlite3.IntegrityError):
                    field = 'payload' if table == 'source_blobs' else ('data' if table == 'source_records' else 'id')
                    db.execute(f'UPDATE {table} SET {field}={field}')

    def test_as_of_uses_receipt_not_historical_event_time(self):
        t1='2026-10-20T20:00:00.000000+00:00'
        t2='2026-10-20T20:10:00.000000+00:00'
        with patch('nbadfs.archive.now',return_value=t1):
            with self.client(json.dumps({'scoreboard':{'games':[game()]}}).encode()) as client:
                first=sync(SyncRequest(),client=client)['snapshot']['id']
        with patch('nbadfs.archive.now',return_value=t2):
            with self.client(json.dumps({'scoreboard':{'games':[game(9)]}}).encode()) as client:
                sync(SyncRequest(),client=client)
        known=archive.as_of('nba','scoreboard','current',datetime.fromisoformat(t1)+timedelta(minutes=5))
        self.assertEqual(known['id'],first)
        with self.assertRaises(KeyError):
            archive.as_of('nba','scoreboard','current',datetime.fromisoformat(t1)-timedelta(seconds=1))

    def test_error_and_invalid_response_are_archived_but_not_replayed(self):
        for payload,status in [(b'{not json',200),(b'quota exceeded',429)]:
            with self.client(payload,status,{'retry-after':'120'}) as client:
                with self.assertRaises(FeedError) as ctx:
                    sync(SyncRequest(),client=client)
                self.assertIsNotNone(ctx.exception.snapshot_id)
        self.assertEqual(archive.status()['snapshots'],2)
        self.assertEqual(archive.status()['attempts'],2)
        with self.assertRaises(KeyError):
            archive.as_of('nba','scoreboard','current',datetime.now(timezone.utc))

    def test_network_failure_is_recorded_without_sensitive_exception_text(self):
        def fail(request):
            raise httpx.ProxyError('credential-in-proxy-URL')
        with httpx.Client(transport=httpx.MockTransport(fail)) as client:
            with self.assertRaises(FeedError):
                sync(SyncRequest(),client=client)
        status=archive.status()
        self.assertEqual(status['snapshots'],0)
        self.assertNotIn('credential-in-proxy-URL',json.dumps(status))

    def test_boxscore_components_and_minutes(self):
        g=game();g['gameStatus']=3
        p={'personId':123,'name':'Example','statistics':{'minutes':'PT32M30.00S',
            'points':30,'threePointersMade':4,'reboundsTotal':10,'assists':10,'steals':2,'blocks':1,'turnovers':4}}
        g['homeTeam']['players']=[p];g['awayTeam']['players']=[]
        with self.client(json.dumps({'game':g}).encode()) as client:
            result=sync(SyncRequest(feed='boxscore',game_id=g['gameId']),client=client)
        row=result['snapshot']['records'][1]
        self.assertEqual(row['minutes'],32.5)
        self.assertEqual(row['dk_points'],68)
        self.assertTrue(row['final'])

    def test_sportsdataio_auth_header_and_normalization(self):
        def handle(request):
            self.assertEqual(request.headers['Ocp-Apim-Subscription-Key'],'private-test-key')
            self.assertNotIn('private-test-key',str(request.url))
            self.assertEqual(request.url.path,'/v3/nba/scores/json/InjuredPlayers')
            return httpx.Response(200,json=[{'PlayerID':1,'FirstName':'Test','LastName':'Player',
                'Team':'ATL','InjuryStatus':'Out','InjuryNotes':'Test fixture'}])
        with patch.dict(os.environ,{'SPORTSDATAIO_API_KEY':'private-test-key'}), httpx.Client(transport=httpx.MockTransport(handle)) as client:
            result=sync(SyncRequest(provider='sportsdataio',feed='injuries'),client=client)
        self.assertEqual(result['snapshot']['records'][0]['injury_status'],'Out')
        self.assertNotIn('private-test-key',archive.snapshot(result['snapshot']['id'])['payload'].decode())

    def test_missing_key_and_echoed_secret_are_not_logged(self):
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaises(FeedError):
                sync(SyncRequest(provider='sportsdataio',feed='players'))
        with patch.dict(os.environ,{'SPORTSDATAIO_API_KEY':'private-test-key'}), self.client(b'private-test-key') as client:
            with self.assertRaises(FeedError):
                sync(SyncRequest(provider='sportsdataio',feed='players'),client=client)
        self.assertEqual(archive.status()['snapshots'],0)
        self.assertNotIn('private-test-key',json.dumps(archive.status()))

    def test_sportsdataio_dates_and_projection_not_actual(self):
        request=SyncRequest(provider='sportsdataio',feed='player_stats',date='2026-10-20')
        self.assertTrue(request.url.endswith('/2026-OCT-20'))
        rows=normalize_sportsdataio('projections',[{'PlayerID':1,'GameID':2,'Minutes':30,'FantasyPointsDraftKings':40}])
        self.assertTrue(rows[0]['is_projection'])
        self.assertEqual(rows[0]['kind'],'projection')
        with self.assertRaises(ValueError):
            SyncRequest(provider='sportsdataio',feed='players',date='2026-10-20')

    def test_authentication_errors_are_classified_without_echoing_provider_text(self):
        cases=[('API key missing in request','key_not_received'),
               ('Invalid subscription key: unrelated-sensitive-value','key_rejected'),
               ('Your trial has expired','expired_access'),
               ('You are not subscribed to this product','product_access_rejected'),
               ('unrelated-sensitive-value','authentication_rejected')]
        with patch.dict(os.environ,{'SPORTSDATAIO_API_KEY':'private-test-key'}):
            for description,reason in cases:
                raw=json.dumps({'Description':description}).encode()
                with self.client(raw,401) as client:
                    with self.assertRaises(FeedError) as ctx:
                        sync(SyncRequest(provider='sportsdataio',feed='players'),client=client)
                self.assertEqual(ctx.exception.reason_code,reason)
                self.assertEqual(ctx.exception.status_code,401)
                self.assertNotIn('unrelated-sensitive-value',str(ctx.exception))
                self.assertNotIn('private-test-key',str(ctx.exception))
                self.assertIsNotNone(ctx.exception.snapshot_id)

    def test_csv_archival_api_raw_export_and_provenance_gate(self):
        client=TestClient(app)
        text='id,name,team,game,positions,salary,start,minutes,fppm\n123,Example,ATL,ATL-BOS,PG,6000,2026-10-21T23:00:00Z,30,1.1'
        response=client.post('/api/import',json={'csv_text':text,'as_of':'2026-10-20T00:00:00Z'})
        self.assertEqual(response.status_code,200,response.text)
        sid=response.json()['source_snapshot_ids'][0]
        self.assertEqual(client.get(f'/api/data/snapshots/{sid}/raw').content,text.encode())
        response=client.post('/api/run',json={'source_snapshot_ids':[sid],'as_of':'2026-10-20T00:00:00Z'})
        self.assertEqual(response.status_code,422)
        self.assertIn('received after',response.text)
        self.assertEqual(len(client.get('/api/data/snapshots').json()['snapshots']),1)
        self.assertEqual(client.get('/api/data/snapshots?limit=1000').status_code,422)
        bad=client.post('/api/import',json={'csv_text':'bad headers\nvalue','as_of':'2026-10-20T00:00:00Z'})
        self.assertEqual(bad.status_code,422)
        self.assertEqual(archive.status()['snapshots'],2)

    def test_salary_projection_join_requires_exact_identity(self):
        g=[{'GameID':2,'DateTimeUTC':'2026-10-21T23:00:00','HomeTeam':'ATL','AwayTeam':'BOS','Status':'Scheduled'}]
        p=[{'PlayerID':10,'GameID':2,'Name':'Example','Team':'ATL','Minutes':30,'FantasyPointsDraftKings':36}]
        for feed,data in [('games',g),('projections',p)]:
            with patch.dict(os.environ,{'SPORTSDATAIO_API_KEY':'private-test-key'}),self.client(json.dumps(data).encode()) as client:
                sync(SyncRequest(provider='sportsdataio',feed=feed,date='2026-10-21'),client=client)
        csv_text='ID,Name,TeamAbbrev,Position,Salary\n123,Example,ATL,PG/SG,6000'
        with self.assertRaisesRegex(ValueError,'no exact projection match'):
            build_slate(SlateRequest(csv_text=csv_text,date='2026-10-21'))
        result=build_slate(SlateRequest(csv_text=csv_text,date='2026-10-21',identity_csv='id,provider_player_id\n123,10'))
        self.assertEqual(result['players'][0]['id'],'123')
        self.assertEqual(result['players'][0]['fppm'],1.2)
        self.assertEqual(len(result['source_snapshot_ids']),3)
        self.assertEqual(result['players'][0]['start'],'2026-10-21T23:00:00Z')

    def test_failed_snapshot_cannot_be_attached_to_forecast(self):
        sid=archive.capture('nba','scoreboard','current',b'bad response')
        archive.attempt('nba','scoreboard','current','normalization_error',snapshot_id=sid)
        with self.assertRaisesRegex(ValueError,'successful import'):
            archive.validated_reference(sid,datetime.now(timezone.utc))

    def test_polling_backs_off_and_respects_retry_after(self):
        from nbadfs.collector import main
        class StopAfterTwo:
            def __init__(self): self.delays=[]
            def is_set(self): return len(self.delays)>=2
            def set(self): pass
            def wait(self,delay): self.delays.append(delay)
        stop=StopAfterTwo()
        result={'snapshot':{'id':'fixture','received_at':'2026-10-20T20:00:00Z'},'record_count':0}
        with patch('sys.argv',['collector','--watch']),patch('nbadfs.collector.Event',return_value=stop),patch('nbadfs.collector.signal.signal'),patch('nbadfs.collector.sync',side_effect=[FeedError('quota',retry_after=300),result]),patch('sys.stdout',io.StringIO()),patch('sys.stderr',io.StringIO()):
            self.assertEqual(main(),0)
        self.assertEqual(stop.delays,[300,60])

    def test_session_key_is_used_without_persistence_or_echo(self):
        client=TestClient(app)
        response=client.post('/api/data/credentials/sportsdataio',json={'key':'session-test-key-123'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(response.json()['key_present'])
        self.assertEqual(response.json()['key_source'],'session')
        self.assertNotIn('session-test-key-123',response.text)
        def handle(request):
            self.assertEqual(request.headers['Ocp-Apim-Subscription-Key'],'session-test-key-123')
            return httpx.Response(200,json=[])
        with httpx.Client(transport=httpx.MockTransport(handle)) as transport:
            result=sync(SyncRequest(provider='sportsdataio',feed='players'),client=transport)
        self.assertNotIn('session-test-key-123',archive.snapshot(result['snapshot']['id'])['payload'].decode())
        response=client.post('/api/data/credentials/sportsdataio',json={'key':None})
        self.assertEqual(response.status_code,200)
        self.assertNotEqual(response.json()['key_source'],'session')

    def test_bad_credential_requests_never_echo_the_supplied_input(self):
        client=TestClient(app)
        for payload in [{'key':'sensitive key with spaces'},{'key':{'secret':'sensitive key with spaces'}},{'key':'sensitive key with spaces','extra':True}]:
            response=client.post('/api/data/credentials/sportsdataio',json=payload)
            self.assertEqual(response.status_code,422)
            self.assertNotIn('sensitive key',response.text)
        response=client.post('/api/data/credentials/sportsdataio',json={'key':'session-test-key-123'},headers={'Origin':'https://unrelated.example'})
        self.assertEqual(response.status_code,403)
        self.assertNotIn('session-test-key-123',response.text)


if __name__ == '__main__':
    unittest.main()

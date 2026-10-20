import gzip
import hashlib
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import httpx
from streamlit.testing.v1 import AppTest

from nbadfs import archive
from nbadfs.backup import export_checkpoint, restore_checkpoint
from nbadfs.cloud_archive import SupabaseArchive
from nbadfs.credentials import session_credentials, sportsdataio_key
from nbadfs.providers import FeedError
from nbadfs.store import database, session_database


class HostingTests(unittest.TestCase):
    def test_parallel_session_credentials_and_database_paths_are_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            def worker(label):
                with session_database(Path(directory)/(label+'.sqlite')),session_credentials(label):
                    archive.capture('test','feed','current',label.encode())
                    return sportsdataio_key(),archive.status()['snapshots']
            with ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(worker,['session-one','session-two']))
            self.assertEqual(results,[('session-one',1),('session-two',1)])
            with session_credentials(None):
                self.assertIsNone(sportsdataio_key())

    def test_portable_backup_roundtrip_delta_and_integrity(self):
        with tempfile.TemporaryDirectory() as directory:
            with session_database(Path(directory)/'first.sqlite'):
                sid=archive.capture('test','feed','current',b'original')
                archive.append_records(sid,[{'kind':'test','entity_id':'one'}])
                archive.attempt('test','feed','current','success',snapshot_id=sid)
                first,known,_=export_checkpoint()
                _,_,count=export_checkpoint(known)
                self.assertEqual(count,0)
                archive.capture('test','feed','current',b'next')
                delta,_,_=export_checkpoint(known)
            with session_database(Path(directory)/'second.sqlite'):
                restore_checkpoint(first)
                self.assertEqual(restore_checkpoint(first),0)
                restore_checkpoint(delta)
                self.assertEqual(archive.status()['snapshots'],2)
                self.assertEqual(archive.snapshot(sid)['payload'],b'original')
                corrupted=json.loads(gzip.decompress(first))
                corrupted['tables']['source_blobs'][0][1]='dGFtcGVyZWQ='
                with self.assertRaisesRegex(ValueError,'SHA-256'):
                    restore_checkpoint(gzip.compress(json.dumps(corrupted).encode()))

    def test_restore_conflicts_roll_back_instead_of_overwriting(self):
        with tempfile.TemporaryDirectory() as directory,session_database(Path(directory)/'db.sqlite'):
            sid=archive.capture('test','feed','current',b'original')
            payload,_,_=export_checkpoint()
            data=json.loads(gzip.decompress(payload))
            data['tables']['source_snapshots'][0][1]='different-provider'
            with self.assertRaisesRegex(ValueError,'conflicts'):
                restore_checkpoint(gzip.compress(json.dumps(data).encode()))
            self.assertEqual(archive.snapshot(sid)['provider'],'test')

    def test_supabase_append_only_checkpoints_and_restore(self):
        objects={}
        def handle(request):
            self.assertEqual(request.headers['apikey'],'fixture-service-role')
            if '/object/list/' in request.url.path:
                return httpx.Response(200,json=[{'name':name.rsplit('/',1)[1]} for name in sorted(objects)])
            name=request.url.path.split('/object/',1)[1]
            if request.method=='POST':
                self.assertEqual(request.headers['x-upsert'],'false')
                self.assertNotIn(name,objects)
                objects[name]=request.content
                return httpx.Response(200,json={'Key':name})
            return httpx.Response(200,content=objects[name])
        with tempfile.TemporaryDirectory() as directory,httpx.Client(transport=httpx.MockTransport(handle)) as client:
            remote=SupabaseArchive('https://fixtureproject.supabase.co','fixture-service-role',client=client)
            with session_database(Path(directory)/'first.sqlite'):
                archive.capture('test','feed','current',b'one')
                known,count=remote.checkpoint({})
                self.assertGreater(count,0)
                remote.checkpoint(known)
                self.assertEqual(len(objects),1)
                archive.capture('test','feed','current',b'two')
                remote.checkpoint(known)
                self.assertEqual(len(objects),2)
            with session_database(Path(directory)/'restored.sqlite'):
                _,count=remote.restore()
                self.assertGreater(count,0)
                self.assertEqual(archive.status()['snapshots'],2)

    def test_streamlit_demo_generation_and_sessions(self):
        app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'streamlit_app.py'),default_timeout=60).run()
        self.assertEqual(len(app.exception),0)
        for widget in app.number_input:
            if widget.label=='Entries': widget.set_value(2)
            if widget.label=='Candidates': widget.set_value(4)
            if widget.label=='Simulations': widget.set_value(100)
        app.button(key='FormSubmitter:portfolio-Generate portfolio').click().run()
        self.assertEqual(len(app.exception),0)
        self.assertEqual(len(app.session_state['result']['lineups']),2)
        other=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'streamlit_app.py'),default_timeout=60).run()
        self.assertNotEqual(app.session_state['db_path'],other.session_state['db_path'])
        self.assertNotIn('result',other.session_state)

    def test_streamlit_password_gate_and_private_key(self):
        app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'streamlit_app.py'),default_timeout=60)
        app.secrets['APP_PASSWORD']='fixture-app-password'
        app.run()
        self.assertEqual(len(app.exception),0)
        self.assertEqual(len(app.tabs),0)
        app.text_input[0].set_value('fixture-app-password')
        app.button(key='FormSubmitter:login-Sign in').click().run()
        self.assertEqual(len(app.tabs),3)
        with patch('nbadfs.providers.sync',side_effect=FeedError('fixture connection failure')):
            for widget in app.text_input:
                if widget.label=='API key': widget.set_value('fixture-private-key')
            app.button(key='FormSubmitter:credential-Connect & test').click().run()
        self.assertEqual(len(app.exception),0)
        self.assertEqual(app.session_state['api_key'].get_secret_value(),'fixture-private-key')
        self.assertNotIn('fixture-private-key',' '.join(e.value for e in app.error))
        with session_credentials(None):
            self.assertIsNone(sportsdataio_key())


if __name__=='__main__':
    unittest.main()

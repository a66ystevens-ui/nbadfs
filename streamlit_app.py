"""Browser-only CourtVision entry point for Streamlit Community Cloud."""
import csv
import hmac
import io
import json
import re
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
from fastapi import HTTPException
from pydantic import SecretStr
from streamlit.errors import StreamlitSecretNotFoundError

from nbadfs import archive
from nbadfs.app import ImportRequest, build, import_csv, provider_slate
from nbadfs.backup import export_checkpoint, restore_checkpoint
from nbadfs.cloud_archive import CloudArchiveError, SupabaseArchive
from nbadfs.credentials import session_credentials
from nbadfs.models import Contest, Rules, RunRequest, SLOTS
from nbadfs.providers import FeedError, SyncRequest, sync
from nbadfs.slate import SlateRequest
from nbadfs.store import database, load_record, session_database

st.set_page_config(page_title='CourtVision · NBA DFS',page_icon='🏀',layout='wide')


def secret(name,default=None):
    try:
        return st.secrets.get(name,default)
    except (StreamlitSecretNotFoundError,FileNotFoundError):
        return default


def login():
    password=secret('APP_PASSWORD')
    if password and not st.session_state.get('authenticated'):
        st.title('CourtVision')
        with st.form('login',clear_on_submit=True):
            entered=st.text_input('App password',type='password')
            submitted=st.form_submit_button('Sign in')
        if submitted:
            if hmac.compare_digest(entered.encode('utf-8'),str(password).encode('utf-8')):
                st.session_state['authenticated']=True
                st.rerun()
            st.error('Incorrect password')
        st.stop()
    return bool(password)


def error_message(exc):
    if isinstance(exc,HTTPException):
        detail=exc.detail
        return detail if isinstance(detail,str) else 'The request could not be completed; check the selected inputs.'
    return str(exc)


def export_lineups(result):
    output=io.StringIO()
    writer=csv.writer(output)
    writer.writerow(SLOTS)
    for lineup in result['lineups']:
        ids=[str(lineup['slots'][s]) for s in SLOTS]
        writer.writerow(["'"+v if v.startswith(('=','+','-','@','\t','\r')) else v for v in ids])
    return output.getvalue()


def main():
    protected=login()
    st.title('CourtVision')
    st.caption('NBA Classic · Projections → simulation → lineup selection')
    st.info('Research baseline. Demo data is fictional; modeled returns are uncalibrated.')
    if 'db_path' not in st.session_state:
        st.session_state['db_path']=str(Path(tempfile.mkdtemp(prefix='courtvision-'))/'research.sqlite')
    day=datetime.now(ZoneInfo('America/New_York')).date()
    with st.sidebar:
        st.header('SportsDataIO')
        with st.form('credential',clear_on_submit=True):
            entered=st.text_input('API key',type='password',help='Private to this browser session. Clears when the session ends.')
            connect=st.form_submit_button('Connect & test')
        if connect:
            if not re.fullmatch(r'[A-Za-z0-9_-]{16,256}',entered.strip()):
                st.error('Enter a valid API key; its value is never included in errors.')
                connect=False
            else:
                st.session_state['api_key']=SecretStr(entered.strip())
        if st.button('Clear session key'):
            st.session_state.pop('api_key',None)
            connect=False
        private=st.session_state.get('api_key')
        key=private.get_secret_value() if private is not None else (secret('SPORTSDATAIO_API_KEY') if protected else None)
        st.caption('Key configured' if key else 'No active key · demo and CSV imports available')
        if secret('SPORTSDATAIO_API_KEY') and not protected:
            st.warning('Configure APP_PASSWORD before enabling a shared server API key. It is currently disabled.')
        game_date=st.date_input('Slate date (New York)',value=day)
        st.caption('Fetches run only when requested. Free hosting does not provide continuous polling.')

    with session_database(st.session_state['db_path']),session_credentials(key):
        remote=None
        if secret('SUPABASE_URL') and secret('SUPABASE_SERVICE_ROLE_KEY'):
            if protected:
                try:
                    remote=SupabaseArchive(secret('SUPABASE_URL'),secret('SUPABASE_SERVICE_ROLE_KEY'),
                        secret('SUPABASE_BUCKET','courtvision'),secret('ARCHIVE_NAMESPACE','owner'))
                    if not st.session_state.get('cloud_restored'):
                        with st.spinner('Restoring your cloud archive…'):
                            known,_=remote.restore()
                        st.session_state['cloud_known']=known
                        st.session_state['cloud_restored']=True
                        st.session_state.pop('cloud_error',None)
                except (CloudArchiveError,ValueError) as exc:
                    st.session_state['cloud_error']=str(exc)
            else:
                st.warning('Set APP_PASSWORD to enable the private Supabase archive. Shared storage is disabled until then.')
        if remote and st.session_state.get('cloud_restored') and not st.session_state.get('cloud_error'):
            st.caption('Supabase checkpoints enabled · new observations and runs are saved after each action.')
        else:
            st.warning('Session storage can disappear when free hosting restarts. Download an archive or configure private Supabase storage.')
        if st.session_state.get('cloud_error'):
            st.error('Cloud archive is not current: '+st.session_state['cloud_error'])

        def checkpoint():
            if remote is not None and st.session_state.get('cloud_restored'):
                try:
                    known,_=remote.checkpoint(st.session_state.get('cloud_known',{}))
                    st.session_state['cloud_known']=known
                    st.session_state.pop('cloud_error',None)
                except (CloudArchiveError,ValueError) as exc:
                    st.session_state['cloud_error']=str(exc)
                    st.error('Cloud checkpoint failed; changes remain in this session. '+str(exc))

        def action(callback):
            try:
                with st.spinner('Working…'):
                    return callback()
            except (HTTPException,FeedError,ValueError,KeyError,TypeError) as exc:
                st.error(error_message(exc))
                return None
            finally:
                checkpoint()

        if connect:
            response=action(lambda:sync(SyncRequest(provider='sportsdataio',feed='players')))
            if response:
                st.success(f"Connected. Archived {response['record_count']} player records.")

        data_tab,build_tab,archive_tab=st.tabs(['Data & imports','Build portfolio','Archive & saved runs'])
        with data_tab:
            st.subheader('Fetch and preserve a feed')
            with st.form('feed'):
                provider=st.selectbox('Provider',['sportsdataio','nba'])
                feed=st.selectbox('Feed',['players','injuries','games','player_stats','projections','scoreboard','schedule','boxscore'])
                gid=st.text_input('NBA game ID (public boxscore only)')
                refresh=st.form_submit_button('Fetch & archive')
            if refresh:
                def fetch():
                    return sync(SyncRequest(provider=provider,feed=feed,
                        date=game_date if provider=='sportsdataio' and feed in {'games','player_stats','projections'} else None,
                        game_id=gid.strip() if gid.strip() else None))
                response=action(fetch)
                if response:
                    st.success(f"Archived {response['record_count']} records. Snapshot {response['snapshot']['id']}")
                    st.dataframe(pd.DataFrame(response['snapshot']['records']),hide_index=True)
            st.subheader('Import a slate')
            method=st.radio('Import format',['Normalized projections CSV','DraftKings salaries + archived SportsDataIO projections'])
            salary=st.file_uploader('Player / salary CSV',type=['csv'],max_upload_size=2)
            history=st.file_uploader('Historical logs CSV (optional)',type=['csv'],max_upload_size=10)
            identity=st.file_uploader('Identity mapping CSV (optional)',type=['csv'],max_upload_size=1)
            probabilities=st.text_area('Play-probability overrides for unresolved injuries (JSON)',value='{}')
            if st.button('Import selected slate'):
                def perform_import():
                    if salary is None:
                        raise ValueError('Choose a player CSV first')
                    text=salary.getvalue().decode('utf-8-sig')
                    if method.startswith('Normalized'):
                        return import_csv(ImportRequest(csv_text=text,history_csv=history.getvalue().decode('utf-8-sig') if history else '',
                            as_of=datetime.now(ZoneInfo('UTC')).isoformat()))
                    return provider_slate(SlateRequest(csv_text=text,date=game_date.isoformat(),
                        identity_csv=identity.getvalue().decode('utf-8-sig') if identity else '',play_probabilities=json.loads(probabilities)))
                imported=action(perform_import)
                if imported:
                    st.session_state['players']=imported['players']
                    st.session_state['source_ids']=imported['source_snapshot_ids']
                    st.session_state['import_notes']=imported.get('warnings',[])
                    st.session_state.pop('result',None)
                    st.success(f"Imported {len(imported['players'])} players")
            if st.button('Use synthetic demo'):
                for name in ('players','source_ids','import_notes','result'):
                    st.session_state.pop(name,None)
                st.success('Synthetic demo selected')
            st.caption('Identity mapping uses exact IDs. Projections require the relevant SportsDataIO subscription. See docs/data.md for CSV formats.')

        with build_tab:
            st.caption('Imported slate' if st.session_state.get('players') else 'Synthetic demo slate')
            with st.form('portfolio'):
                left,right=st.columns(2)
                with left:
                    entries=st.number_input('Entries',min_value=1,max_value=20,value=3)
                    candidate_count=st.number_input('Candidates',min_value=1,max_value=80,value=24)
                    draws=st.number_input('Simulations',min_value=100,max_value=10000,value=1000,step=100)
                    mode=st.selectbox('Objective',['gpp','cash'])
                    exposure=st.slider('Maximum player exposure (%)',1,100,100)
                    minimum=st.number_input('Minimum salary',min_value=0,max_value=50000,value=45000,step=100)
                with right:
                    locks=st.text_input('Lock IDs (comma-separated)')
                    excludes=st.text_input('Exclude IDs (comma-separated)')
                    unique=st.number_input('Minimum unique players',min_value=1,max_value=8,value=1)
                    contest_json=st.text_area('Contest JSON',value=Contest().model_dump_json(),height=140)
                    advanced=st.text_area('Advanced run JSON: seed, as_of, current_lineup, max_from_team',value='{}')
                generate=st.form_submit_button('Generate portfolio',type='primary')
            if generate:
                def generate_run():
                    settings=json.loads(advanced)
                    max_team=settings.pop('max_from_team',8)
                    return build(RunRequest(**settings,players=st.session_state.get('players'),
                        source_snapshot_ids=st.session_state.get('source_ids',[]),entries=entries,candidates=candidate_count,
                        simulations=draws,mode=mode,contest=Contest.model_validate(json.loads(contest_json)),
                        rules=Rules(locks=[x.strip() for x in locks.split(',') if x.strip()],
                            excludes=[x.strip() for x in excludes.split(',') if x.strip()],min_unique=unique,
                            max_exposure=exposure/100,min_salary=minimum,max_from_team=max_team)))
                result=action(generate_run)
                if result:
                    st.session_state['result']=result
            result=st.session_state.get('result')
            if result:
                a,b,c=st.columns(3)
                a.metric('Players',len(result['players']))
                b.metric('Candidates',result['candidate_count'])
                c.metric('Modeled portfolio net EV (uncalibrated)',f"${result['portfolio']['ev']:.2f}")
                st.caption(('SYNTHETIC DEMO · ' if result['synthetic'] else 'IMPORTED DATA · ')+result['as_of'])
                st.dataframe(pd.DataFrame([{k:p[k] for k in ['id','name','team','salary','projected_minutes','projection','floor','ceiling','ownership']} for p in result['players']]),hide_index=True)
                lookup={p['id']:p for p in result['players']}
                for i,lineup in enumerate(result['lineups'],1):
                    with st.expander(f"Lineup {i} · ${lineup['salary']:,} · mean {lineup['projection']:.1f}"):
                        st.table([{'Slot':s,'Player':lookup[pid]['name'],'ID':pid,'Team':lookup[pid]['team']} for s,pid in lineup['slots'].items()])
                        st.caption(f"Modeled EV ${lineup['ev']:.2f} · simulation SE ${lineup['ev_standard_error']:.2f} · cash probability {lineup['cash_probability']:.1%}")
                prefix='synthetic-demo' if result['synthetic'] else 'nba-classic'
                st.download_button('Download lineup CSV',export_lineups(result),file_name=prefix+'-lineups.csv',mime='text/csv')
                with st.expander('Assumptions and import notes'):
                    for note in st.session_state.get('import_notes',[])+result['warnings']:
                        st.write('• '+note)

        with archive_tab:
            state=archive.status()
            st.write({k:state[k] for k in ('snapshots','unique_payloads','records','attempts')})
            st.dataframe(pd.DataFrame(archive.list_snapshots()),hide_index=True)
            if state['recent_attempts']:
                st.dataframe(pd.DataFrame(state['recent_attempts']),hide_index=True)
            bundle,_,_=export_checkpoint()
            st.download_button('Download complete archive',bundle,file_name='courtvision-archive.json.gz',mime='application/gzip')
            upload=st.file_uploader('Restore a CourtVision archive',type=['gz'],max_upload_size=50)
            if st.button('Restore uploaded archive'):
                def restore():
                    if upload is None:
                        raise ValueError('Choose a CourtVision .json.gz archive first')
                    return restore_checkpoint(upload.getvalue())
                count=action(restore)
                if count is not None:
                    st.success(f'Restored {count} rows without overwriting existing data')
                    st.rerun()
            if remote and st.button('Retry cloud checkpoint'):
                checkpoint()
            with database() as db:
                run_ids=[r[0] for r in db.execute('SELECT id FROM runs ORDER BY received_at DESC')]
            if run_ids:
                selected=st.selectbox('Saved run',run_ids)
                if st.button('Load saved run'):
                    record=load_record(selected)
                    st.session_state['result']=record['result']
                    st.session_state['players']=record['request']['players']
                    st.session_state['source_ids']=record['request'].get('source_snapshot_ids',[])
                    st.success('Saved result loaded in Build portfolio')


if __name__=='__main__':
    main()

"""SportsDataIO and public NBA JSON feeds with raw-response provenance."""
import json
import re
from datetime import date as CalendarDate, datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Literal

import httpx
from pydantic import Field, model_validator

from . import archive
from .models import StrictModel
from .scoring import dk_points
from .credentials import sportsdataio_key

BASE = 'https://cdn.nba.com/static/json/'
MAX_BYTES = 20_000_000


class SyncRequest(StrictModel):
    provider: Literal['nba','sportsdataio'] = 'nba'
    feed: Literal['scoreboard','schedule','boxscore','players','injuries','games','player_stats','projections'] = 'scoreboard'
    game_id: str | None = Field(default=None,pattern=r'^\d{10}$')
    date: CalendarDate | None = None

    @model_validator(mode='after')
    def check_game(self):
        if self.provider == 'nba':
            if self.feed not in {'scoreboard','schedule','boxscore'}:
                raise ValueError('NBA public feeds: scoreboard, schedule, boxscore')
            if (self.feed == 'boxscore') != (self.game_id is not None) or self.date is not None:
                raise ValueError('Only NBA boxscore requires game_id; public feeds do not accept a date')
        else:
            if self.feed not in {'players','injuries','games','player_stats','projections'}:
                raise ValueError('SportsDataIO feeds: players, injuries, games, player_stats, projections')
            if (self.feed in {'games','player_stats','projections'}) != (self.date is not None) or self.game_id is not None:
                raise ValueError('SportsDataIO games, player_stats, projections require date; other feeds accept neither date nor game_id')
        return self

    @property
    def resource(self):
        return self.game_id or (self.date.isoformat() if self.date else
                               ('current' if self.feed in {'scoreboard','players','injuries'} else 'league'))

    @property
    def url(self):
        if self.provider == 'sportsdataio':
            paths = {'players':'scores/json/Players','injuries':'scores/json/InjuredPlayers',
                'games':'scores/json/GamesByDate', 'player_stats':'stats/json/PlayerGameStatsByDate',
                'projections':'projections/json/PlayerGameProjectionStatsByDate'}
            suffix = ''
            if self.date:
                months = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC']
                suffix = f'/{self.date.year}-{months[self.date.month-1]}-{self.date.day:02}'
            return 'https://api.sportsdata.io/v3/nba/' + paths[self.feed] + suffix
        paths = {'scoreboard':'liveData/scoreboard/todaysScoreboard_00.json',
                 'schedule':'staticData/scheduleLeagueV2.json',
                 'boxscore':f'liveData/boxscore/boxscore_{self.game_id}.json'}
        return BASE + paths[self.feed]


class FeedError(Exception):
    def __init__(self, detail, *, snapshot_id=None, retry_after=None, reason_code=None, status_code=None):
        super().__init__(detail)
        self.snapshot_id = snapshot_id
        self.retry_after = retry_after
        self.reason_code = reason_code
        self.status_code = status_code


def authentication_error(raw,request,status):
    """Classify known provider errors without echoing arbitrary response text or keys."""
    try:
        payload=json.loads(raw)
        description=payload.get('Description','') if isinstance(payload,dict) else ''
        description=description.lower() if isinstance(description,str) else ''
    except (ValueError,TypeError):
        description=''
    if 'missing' in description and ('key' in description or 'token' in description):
        reason='key_not_received'
        text='SportsDataIO reports that it did not receive an API key.'
    elif 'expired' in description:
        reason='expired_access'
        text='SportsDataIO reports expired key or subscription access.'
    elif 'invalid' in description and ('key' in description or 'token' in description):
        reason='key_rejected'
        text='SportsDataIO reports that the API key is invalid for this request.'
    elif any(term in description for term in ['subscription','not authorized','not authorised','not subscribed','permission']):
        reason='product_access_rejected'
        text='SportsDataIO reports that this key does not have the required product access.'
    else:
        reason='authentication_rejected'
        text='SportsDataIO rejected this key or its access to the selected NBA feed.'
    return reason,(f'{text} HTTP {status}; NBA feed: {request.feed}. '
        'Copy the key from your active NBA trial/subscription and test the same endpoint in SportsDataIO API Explorer. '
        'Access to another sport or product does not establish access to this feed. Do not share the key in chat.')


def timestamp(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z','+00:00'))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc).isoformat(timespec='microseconds')
    except ValueError:
        return None


def minutes(value):
    if isinstance(value,(int,float)):
        return float(value)
    match = re.fullmatch(r'PT(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?',str(value))
    if not match:
        raise ValueError('Unrecognized NBA minutes value')
    h,m,s = [float(v or 0) for v in match.groups()]
    return h*60+m+s/60


def normalize(feed, payload):
    if not isinstance(payload,dict):
        raise ValueError('NBA response must be a JSON object')
    records = []
    def game_record(game):
        gid = str(game['gameId'])
        start = timestamp(game.get('gameTimeUTC') or game.get('gameDateTimeUTC'))
        return dict(kind='game',entity_id=gid,game_id=gid,event_at=start,
            home=game['homeTeam']['teamTricode'],away=game['awayTeam']['teamTricode'],
            status=game.get('gameStatus'),status_text=game.get('gameStatusText'),
            home_score=game['homeTeam'].get('score'),away_score=game['awayTeam'].get('score'))
    if feed == 'scoreboard':
        games = payload['scoreboard']['games']
        if not isinstance(games,list):
            raise ValueError('Missing scoreboard games array')
        records.extend(game_record(game) for game in games)
    elif feed == 'schedule':
        dates = payload['leagueSchedule']['gameDates']
        if not isinstance(dates,list):
            raise ValueError('Missing schedule dates array')
        for date in dates:
            records.extend(game_record(game) for game in date['games'])
    else:
        game = payload['game']
        records.append(game_record(game))
        for side in ['homeTeam','awayTeam']:
            for player in game[side]['players']:
                stats = player.get('statistics') or {}
                row = dict(kind='player_boxscore',entity_id=f"{game['gameId']}:{player['personId']}",
                    nba_player_id=str(player['personId']),game_id=str(game['gameId']),
                    team=game[side]['teamTricode'],name=player.get('name'),
                    event_at=records[0]['event_at'],game_status=game.get('gameStatus'),
                    final=game.get('gameStatus') == 3,player_status=player.get('status'),
                    not_playing_reason=player.get('notPlayingReason'))
                fields = ['points','threePointersMade','reboundsTotal','assists','steals','blocks','turnovers']
                # Missing stats remain unknown, never fabricated as zero.
                if stats.get('minutes') is not None and all(stats.get(k) is not None for k in fields):
                    row.update(minutes=minutes(stats['minutes']),dk_points=float(dk_points(*[stats[k] for k in fields])),
                               statistics={k:stats[k] for k in fields})
                records.append(row)
    return records


def normalize_sportsdataio(feed, payload):
    if not isinstance(payload,list):
        raise ValueError('SportsDataIO response must be a JSON array')
    records = []
    def utc_field(value):
        # Only fields explicitly documented as UTC may omit the suffix.
        if not value:
            return None
        parsed = datetime.fromisoformat(value)
        return (parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed).astimezone(timezone.utc).isoformat(timespec='microseconds')
    for item in payload:
        if feed == 'games':
            records.append(dict(kind='game',entity_id=str(item['GameID']),game_id=str(item['GameID']),
                event_at=utc_field(item.get('DateTimeUTC')),home=item.get('HomeTeam'),away=item.get('AwayTeam'),
                status=item.get('Status'),home_score=item.get('HomeTeamScore'),away_score=item.get('AwayTeamScore')))
            continue
        pid = str(item['PlayerID'])
        row = dict(kind={'players':'player','injuries':'availability','player_stats':'player_boxscore',
                         'projections':'projection'}[feed],entity_id=pid,
            provider_player_id=pid,nba_player_id=str(item['NbaDotComPlayerID']) if item.get('NbaDotComPlayerID') else None,
            draftkings_player_id=str(item['DraftKingsPlayerID']) if item.get('DraftKingsPlayerID') else None,
            name=item.get('Name') or ' '.join(filter(None,[item.get('FirstName'),item.get('LastName')])),
            team=item.get('Team'),position=item.get('Position'),
            injury_status=item.get('InjuryStatus'),injury_body_part=item.get('InjuryBodyPart'),
            injury_notes=item.get('InjuryNotes'),provider_updated_raw=item.get('Updated'))
        if feed in {'player_stats','projections'}:
            row.update(entity_id=f"{item.get('GameID',item.get('Date'))}:{pid}",game_id=str(item['GameID']) if item.get('GameID') else None,
                event_at=utc_field(item.get('DateTimeUTC')),date_raw=item.get('Date'),
                minutes=item.get('Minutes'),dk_points=item.get('FantasyPointsDraftKings'),
                draftkings_salary=item.get('DraftKingsSalary'),is_projection=feed == 'projections')
            if item.get('Minutes') is not None and item.get('Seconds') is not None:
                row['minutes'] = item['Minutes'] + item['Seconds']/60
            # Projection fantasy points already include the provider's bonus expectation.
            fields = ['Points','ThreePointersMade','Rebounds','Assists','Steals','BlockedShots','Turnovers']
            if feed == 'player_stats' and all(item.get(k) is not None for k in fields):
                row['dk_points'] = float(dk_points(*[item[k] for k in fields]))
        records.append(row)
    return records


def retry_delay(header):
    try:
        return max(0,float(header))
    except (ValueError,TypeError):
        try:
            return max(0,(parsedate_to_datetime(header)-datetime.now(timezone.utc)).total_seconds())
        except (ValueError,TypeError,OverflowError):
            return None


def sync(request, *, client=None):
    provider = request.provider
    auth = sportsdataio_key() if provider == 'sportsdataio' else None
    if provider == 'sportsdataio' and not auth:
        archive.attempt(provider,request.feed,request.resource,'configuration_error',detail='SPORTSDATAIO_API_KEY is not configured')
        raise FeedError('Add SPORTSDATAIO_API_KEY securely in environment settings; do not paste it into chat')
    owned = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(30,connect=10),follow_redirects=False)
    sid = None
    try:
        try:
            headers = {'Accept':'application/json','User-Agent':'CourtVision/0.1 (NBA research)'}
            if auth:
                headers['Ocp-Apim-Subscription-Key'] = auth
            with client.stream('GET',request.url,headers=headers) as response:
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise FeedError('Provider response exceeded the 20 MB limit; payload was not archived')
                    chunks.append(chunk)
                raw = b''.join(chunks)
                # Preserve the response before normalizing. Error bodies and malformed JSON
                # are useful evidence too. Never archive request headers or proxy settings.
                if auth and auth.encode() in raw:
                    archive.attempt(provider,request.feed,request.resource,'security_error',detail='Provider response echoed a credential; response was not stored')
                    raise FeedError('Provider echoed a credential; response was not stored')
                try:
                    parsed = json.loads(raw)
                    generated_at = timestamp((parsed.get('meta') or {}).get('time')) if isinstance(parsed,dict) else None
                except (ValueError,TypeError,AttributeError):
                    generated_at = None
                sid = archive.capture(provider,request.feed,request.resource,raw,status=response.status_code,
                                      headers=response.headers,source_generated_at=generated_at)
                retry_after = retry_delay(response.headers.get('retry-after'))
                if response.status_code != 200:
                    archive.attempt(provider,request.feed,request.resource,'http_error',snapshot_id=sid,
                                    detail=f'HTTP {response.status_code}',retry_after=retry_after)
                    reason=None
                    message=f'{provider} returned HTTP {response.status_code}'
                    if provider=='sportsdataio' and response.status_code in {401,403}:
                        reason,message=authentication_error(raw,request,response.status_code)
                    raise FeedError(message,snapshot_id=sid,retry_after=retry_after,
                                    reason_code=reason,status_code=response.status_code)
        except httpx.HTTPError as exc:
            # Exception strings may include proxy credentials. Persist only the class.
            archive.attempt(provider,request.feed,request.resource,'network_error',detail=type(exc).__name__)
            host = 'api.sportsdata.io' if provider == 'sportsdataio' else 'cdn.nba.com'
            raise FeedError(f'{provider} connection failed ({type(exc).__name__}); check network access to {host}') from None
        try:
            payload = json.loads(raw)
            rows = normalize_sportsdataio(request.feed,payload) if provider == 'sportsdataio' else normalize(request.feed,payload)
            generated_at = timestamp((payload.get('meta') or {}).get('time')) if isinstance(payload,dict) else None
            # Generation time is a source claim, distinct from publication and receipt.
            for row in rows:
                row['source_generated_at'] = generated_at
                row['normalizer_version'] = provider + '-1'
            archive.append_records(sid,rows)
        except (ValueError,TypeError,KeyError,OverflowError) as exc:
            archive.attempt(provider,request.feed,request.resource,'normalization_error',snapshot_id=sid,
                            detail=type(exc).__name__)
            raise FeedError('Provider response was archived but did not match the supported schema',snapshot_id=sid) from None
        archive.attempt(provider,request.feed,request.resource,'success',snapshot_id=sid)
        return {'snapshot':archive.snapshot(sid,include_payload=False),'record_count':len(rows)}
    except FeedError as exc:
        if sid is None and not isinstance(exc.__cause__, httpx.HTTPError) and '20 MB' in str(exc):
            archive.attempt(provider,request.feed,request.resource,'size_error',detail='Response exceeded byte limit')
        raise
    finally:
        if owned:
            client.close()

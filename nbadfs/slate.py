"""Join a real DK salary slate to archived SportsDataIO projections, without name guesses."""
import csv
import io
from datetime import datetime, timezone

from . import archive
from .models import Player, StrictModel
from pydantic import Field


class SlateRequest(StrictModel):
    csv_text: str = Field(max_length=2_000_000)
    date: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    identity_csv: str = Field(default='',max_length=500_000)
    # Do not invent play probabilities for unresolved injury statuses.
    play_probabilities: dict[str,float] = Field(default_factory=dict)


def build_slate(request):
    at = datetime.now(timezone.utc)
    games = archive.as_of('sportsdataio','games',request.date,at)
    projections = archive.as_of('sportsdataio','projections',request.date,at)
    source_ids = [games['id'],projections['id']]
    game_map = {g['game_id']:g for g in games['records']}
    team_games = {}
    for g in games['records']:
        for team in [g['home'],g['away']]:
            team_games.setdefault(team,[]).append(g)
    explicit = {}
    if request.identity_csv:
        for row in csv.DictReader(io.StringIO(request.identity_csv.lstrip('\ufeff'))):
            if not row.get('id') or not row.get('provider_player_id'):
                raise ValueError('Identity CSV requires id,provider_player_id columns')
            if row['id'] in explicit:
                raise ValueError('Duplicate DraftKings ID in identity CSV')
            explicit[row['id']] = row['provider_player_id']
    by_provider = {}
    by_dk = {}
    for row in projections['records']:
        pid = row['provider_player_id']
        if pid in by_provider:
            raise ValueError('Projection response has duplicate player IDs; select a single-game-date slate')
        by_provider[pid] = row
        if row.get('draftkings_player_id'):
            dk = row['draftkings_player_id']
            if dk in by_dk:
                raise ValueError('Projection response has duplicate DraftKings player IDs')
            by_dk[dk] = row
    players, skipped, used_provider_ids = [], [], set()
    for r in csv.DictReader(io.StringIO(request.csv_text.lstrip('\ufeff'))):
        pid = r.get('ID') or r.get('id')
        projection = by_provider.get(explicit[pid]) if pid in explicit else by_dk.get(pid)
        if projection is None:
            raise ValueError(f'Salary ID {pid}: no exact projection match. Supply identity CSV id,provider_player_id; names are never matched automatically.')
        if projection['provider_player_id'] in used_provider_ids:
            raise ValueError('Salary file maps multiple rows to the same real player')
        used_provider_ids.add(projection['provider_player_id'])
        minutes,points = projection.get('minutes'),projection.get('dk_points')
        if minutes is None or points is None:
            raise ValueError(f'{pid}: provider projection is missing minutes or DraftKings points')
        if minutes <= 0:
            skipped.append(pid)
            continue
        team = r.get('TeamAbbrev') or r.get('team')
        if team != projection['team']:
            raise ValueError(f'{pid}: team abbreviation differs between sources; resolve it explicitly before importing')
        g = game_map.get(projection.get('game_id'))
        if g is None:
            choices = team_games.get(team,[])
            if len(choices) != 1:
                raise ValueError(f'{pid}: cannot determine a unique scheduled game')
            g = choices[0]
        if team not in {g['home'],g['away']} or not g['event_at']:
            raise ValueError(f'{pid}: missing UTC game time or inconsistent team/game')
        injury = (projection.get('injury_status') or '').lower()
        probability = request.play_probabilities.get(pid)
        if probability is None:
            if injury in {'out','o'}:
                probability=0
            elif injury in {'','healthy','active'}:
                probability=1
            else:
                raise ValueError(f'{pid}: unresolved injury status {injury}; supply play_probabilities for this DraftKings ID')
        positions = r.get('Position') or r.get('positions') or ''
        players.append(Player(id=pid,name=r.get('Name') or r.get('name'),team=team,
            game=g['game_id'],positions=positions.split('/'),salary=int(r.get('Salary') or r.get('salary')),
            start=g['event_at'],minutes=minutes,fppm=points/minutes,play_probability=probability,
            received_at=max(datetime.fromisoformat(games['received_at']),datetime.fromisoformat(projections['received_at']))))
    if not players or len({p.id for p in players}) != len(players):
        raise ValueError('Salary CSV must contain unique player rows')
    if len(players)>300:
        raise ValueError('Maximum 300 players per slate')
    if request.identity_csv:
        sid=archive.archive_csv('identity_csv',request.identity_csv)
        archive.attempt('upload','identity_csv','manual','success',snapshot_id=sid)
        source_ids.append(sid)
    return {'players':[p.model_dump(mode='json') for p in players],
        'source_snapshot_ids':source_ids,
        'warnings':['Provider projections are imported estimates, not a CourtVision-trained model.',
                    'Ownership is an uncalibrated default; use a licensed ownership source before tournament EV decisions.',
                    'Injury handling uses the projection snapshot; fetch a fresh snapshot after news. Separate injury refreshes do not silently override projections.',
                    f'{len(skipped)} zero-minute projected players omitted; verify full remaining rotations.']}

import csv
import io
from datetime import datetime

import numpy as np

from .models import Player, utc


def history_rows(text):
    required = {'player_id','game_date','available_at','minutes','dk_points'}
    reader = csv.DictReader(io.StringIO(text.lstrip('\ufeff')))
    if not required <= set(reader.fieldnames or []):
        raise ValueError('History requires player_id,game_date,available_at,minutes,dk_points')
    rows = []
    for row in reader:
        event = utc(datetime.fromisoformat(row['game_date']))
        available = utc(datetime.fromisoformat(row['available_at']))
        minutes, points = float(row['minutes']), float(row['dk_points'])
        if not np.isfinite(minutes) or not np.isfinite(points) or not 0 <= minutes <= 70:
            raise ValueError('Invalid historical minutes or fantasy points')
        if available < event:
            raise ValueError('Historical outcome cannot be available before its game')
        rows.append(dict(player_id=row['player_id'], game_date=event,
                         available_at=available, minutes=minutes, dk_points=points))
    if len({(r['player_id'],r['game_date']) for r in rows}) != len(rows):
        raise ValueError('Duplicate player/game observations in history')
    return sorted(rows, key=lambda r: r['game_date'])


def forecast_history(rows, player_id, as_of):
    usable = [r for r in rows if r['player_id'] == player_id
              and r['game_date'] < as_of and r['available_at'] <= as_of and r['minutes'] > 0][-20:]
    if not usable:
        raise ValueError(f'{player_id}: no prior available history; supply minutes and fppm explicitly')
    weights = np.power(.9, np.arange(len(usable)-1,-1,-1))
    minutes = np.array([r['minutes'] for r in usable])
    points = np.array([r['dk_points'] for r in usable])
    m = float(np.average(minutes,weights=weights))
    # Shrink per-minute rates toward an explicit neutral prior (20 player-minutes).
    fppm = float((np.dot(weights,points)+20) / (np.dot(weights,minutes)+20))
    sd = float(np.sqrt(np.average((minutes-m)**2,weights=weights)))
    return dict(minutes=min(48,m), fppm=max(.01,fppm), minutes_sd=min(20,max(1,sd)))


def parse_players(text, as_of, history=''):
    reader = csv.DictReader(io.StringIO(text.lstrip('\ufeff')))
    rows = history_rows(history) if history.strip() else []
    players = []
    for line, raw in enumerate(reader, 2):
        if line > 302:
            raise ValueError('Maximum 300 player rows')
        r = {k.strip(): v.strip() for k,v in raw.items() if k is not None and v is not None}
        try:
            # Official salary exports require augmentation with game/start and forecasts.
            pid = r.get('id') or r.get('ID')
            positions = r.get('positions') or r.get('Position') or ''
            model = dict(id=pid, name=r.get('name') or r.get('Name'),
                         team=r.get('team') or r.get('TeamAbbrev'),
                         game=r.get('game'), start=r.get('start'),
                         positions=positions.split('/'), salary=int(r.get('salary') or r.get('Salary')))
            if r.get('minutes') and r.get('fppm'):
                model.update(minutes=float(r['minutes']),fppm=float(r['fppm']))
            else:
                model.update(forecast_history(rows,pid,as_of))
            for key in ['minutes_sd','play_probability','ownership']:
                if r.get(key):
                    model[key] = float(r[key])
            for key in ['source_published_at','received_at']:
                if r.get(key):
                    model[key] = r[key]
            rate_keys = ['two','three','ft','reb','ast','stl','blk','tov']
            if any(r.get(k) for k in rate_keys):
                model['rates'] = {k: float(r[k]) for k in rate_keys}
            players.append(Player.model_validate(model))
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError(f'CSV row {line}: {exc}') from exc
    if not players:
        raise ValueError('CSV has no players')
    if len({p.id for p in players}) != len(players):
        raise ValueError('Duplicate player IDs')
    return players


def walk_forward(rows, min_games=3):
    errors = []
    for row in rows:
        previous = [r for r in rows if r['player_id'] == row['player_id']
                    and r['game_date'] < row['game_date'] and r['available_at'] <= row['game_date']
                    and r['minutes'] > 0]
        if len(previous) < min_games:
            continue
        predicted = forecast_history(previous,row['player_id'],row['game_date'])
        errors.append((predicted['minutes']-row['minutes'],
                       predicted['minutes']*predicted['fppm']-row['dk_points']))
    if not errors:
        raise ValueError('No eligible holdouts: need at least four chronological games per player')
    e = np.array(errors)
    return dict(evaluated_rows=len(e), skipped_rows=len(rows)-len(e),
                minutes_mae=float(np.abs(e[:,0]).mean()), minutes_rmse=float(np.sqrt((e[:,0]**2).mean())),
                points_mae=float(np.abs(e[:,1]).mean()), points_rmse=float(np.sqrt((e[:,1]**2).mean())))

from datetime import timedelta

from .models import Player


def demo_players(as_of):
    """Fictional, deliberately reproducible full rotations. Never live projections."""
    result = []
    positions = [['PG'], ['SG'], ['SF'], ['PF'], ['C'], ['PG', 'SG'],
                 ['SG', 'SF'], ['SF', 'PF'], ['PF', 'C'], ['C']]
    minutes = [34, 33, 31, 30, 29, 23, 20, 17, 13, 10]
    for t, team in enumerate(['ATL', 'BOS', 'CHI', 'DAL', 'DEN', 'NYK']):
        game = ['ATL-BOS', 'CHI-DAL', 'DEN-NYK'][t // 2]
        for i, pos in enumerate(positions):
            rate = 1.35 - i * .065 + (t % 3) * .04
            result.append(Player(id=f'demo-{t}-{i}', name=f'{team} Player {i+1:02}',
                team=team, game=game, positions=pos,
                salary=100 * round((9300 - i * 650 + (t % 3) * 250) / 100),
                start=as_of + timedelta(hours=2 + t // 2), minutes=minutes[i],
                fppm=rate, ownership=.035 + (9-i)*.022,
                rates={k: v * rate for k,v in dict(two=.24, three=.075, ft=.10,
                      reb=.20, ast=.13, stl=.023, blk=.018, tov=.065).items()}))
    return result

"""Approximate joint simulator. Calibration requires historical observations."""
import numpy as np

from .scoring import dk_points


def allocate_minutes(weights, active):
    """Capped proportional allocation: 240 minutes, no player above 48."""
    weights = np.where(active, np.maximum(weights, .001), 0.)
    if np.any(active.sum(axis=1) < 5):
        raise ValueError('Availability scenario has fewer than five active players')
    result = np.zeros_like(weights)
    free = active.copy()
    for _ in range(weights.shape[1] + 1):
        remaining = 240 - result.sum(axis=1)
        w = np.where(free, weights, 0)
        proposal = w / np.maximum(w.sum(axis=1, keepdims=True), 1e-12) * remaining[:, None]
        over = (proposal > 48 + 1e-9) & free
        done = ~over.any(axis=1)
        result[done] += proposal[done]
        free[done] = False
        result[over] = 48
        free[over] = False
        if not free.any():
            break
    if not np.allclose(result.sum(axis=1), 240) or np.any(result > 48 + 1e-8):
        raise ValueError('Unable to allocate valid rotations')
    return result


def simulate(players, draws, seed):
    rng = np.random.default_rng(seed)
    n = len(players)
    mins = np.zeros((draws, n))
    scores = np.zeros_like(mins)
    for team in sorted({p.team for p in players}):
        idx = [i for i, p in enumerate(players) if p.team == team]
        group = [players[i] for i in idx]
        probabilities = np.array([p.play_probability for p in group])
        if (probabilities > 0).sum() < 5:
            raise ValueError(f'{team}: supply a full rotation with at least five potentially active players')
        active = rng.random((draws, len(idx))) < probabilities
        # Condition on a playable rotation rather than silently inventing players.
        for _ in range(100):
            bad = active.sum(axis=1) < 5
            if not bad.any():
                break
            active[bad] = rng.random((bad.sum(), len(idx))) < probabilities
        if (active.sum(axis=1) < 5).any():
            raise ValueError(f'{team}: availability inputs rarely produce a playable rotation')
        weights = rng.normal([p.minutes for p in group], [p.minutes_sd for p in group],
                             size=(draws, len(idx)))
        mins[:, idx] = allocate_minutes(weights, active)
    game_factors = {g: rng.lognormal(-.5*.065**2, .065, draws)
                    for g in sorted({p.game for p in players})}
    for i, p in enumerate(players):
        factor = game_factors[p.game] * rng.lognormal(-.5*.12**2, .12, draws)
        opportunity = mins[:, i] * factor
        if p.rates:
            stats = {k: rng.poisson(opportunity * v) for k, v in p.rates.items()}
            pts = 2 * stats['two'] + 3 * stats['three'] + stats['ft']
            scores[:, i] = dk_points(pts, stats['three'], stats['reb'], stats['ast'],
                                     stats['stl'], stats['blk'], stats['tov'])
        else:
            # Aggregate baseline does not separately model milestone bonuses.
            scores[:, i] = opportunity * p.fppm * rng.gamma(12, 1/12, draws)
    return scores, mins


def summarize(players, scores, minutes):
    result = []
    for i, p in enumerate(players):
        q = np.quantile(scores[:, i], [.1, .5, .9, .99])
        result.append(p.model_dump(mode='json') | {
            'projected_minutes': round(float(minutes[:, i].mean()), 2),
            'projection': round(float(scores[:, i].mean()), 2),
            'floor': round(float(q[0]), 2), 'median': round(float(q[1]), 2),
            'ceiling': round(float(q[2]), 2), 'p99': round(float(q[3]), 2),
            'value': round(float(scores[:, i].mean()) / max(p.salary / 1000, 1), 2)})
    return result

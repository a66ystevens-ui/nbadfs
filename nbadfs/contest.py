"""Legal field sampler, exact tie splitting, and constrained portfolio selection."""
import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

from .models import Rules
from .optimizer import solve


def field_lineups(players, means, size, rng, as_of):
    result = []
    # Construction distribution is a heuristic, not a calibrated ownership model.
    ownership = np.clip([p.ownership for p in players], .001, .999)
    logits = np.log(ownership / (1-ownership))
    for _ in range(size):
        objective = .12*means + logits + rng.gumbel(size=len(players))*2.5
        result.append(solve(players, objective, Rules(min_salary=45000), as_of=as_of))
    return result


def lineup_scores(scores, lineups):
    return np.stack([scores[:, lineup].sum(axis=1) for lineup in lineups], axis=1)


def payouts_for_scores(own, opponents, contest):
    """All own entries compete together; split the sum of tied finishing prizes."""
    ladder = np.zeros(contest.size)
    ladder[:len(contest.payouts)] = contest.payouts
    cumulative = np.r_[0, np.cumsum(ladder)]
    all_scores = np.concatenate([opponents, own], axis=1)
    if all_scores.shape[1] != contest.size:
        raise ValueError('Field and own entries must equal the actual contest size')
    payouts = np.zeros_like(own)
    cash = np.zeros_like(own, dtype=bool)
    wins = np.zeros_like(own, dtype=bool)
    for j in range(own.shape[1]):
        target = own[:,j,None]
        ahead = (all_scores > target + 1e-8).sum(axis=1)
        tied = (np.abs(all_scores-target) <= 1e-8).sum(axis=1)
        payouts[:,j] = (cumulative[ahead+tied]-cumulative[ahead])/tied
        cash[:,j] = payouts[:,j] > 0
        wins[:,j] = ahead == 0
    return payouts, cash, wins


def evaluate_candidates(scores, candidates, field, contest):
    own_scores = lineup_scores(scores,candidates)
    field_scores = lineup_scores(scores,field)
    if len(field) != contest.size-1:
        raise ValueError('Candidate evaluation needs contest.size - 1 opponents')
    returns = []
    for j in range(len(candidates)):
        p,c,w = payouts_for_scores(own_scores[:,j:j+1],field_scores,contest)
        net = p[:,0]-contest.entry_fee
        returns.append({'ev': float(net.mean()), 'roi': float(net.mean()/contest.entry_fee),
                        'cash_probability': float(c.mean()), 'win_or_tie_probability': float(w.mean()),
                        'ev_standard_error': float(net.std(ddof=1)/np.sqrt(len(net)))})
    return returns


def select_portfolio(players, candidates, metrics, entries, max_exposure, mode, means):
    n = len(candidates)
    if n < entries:
        raise ValueError('Too few unique candidates for requested entries')
    # Hard integer exposure limit: a 50% cap over 3 entries permits only 1 use.
    cap = int(np.floor(entries * max_exposure + 1e-9))
    membership = np.array([[int(i in l) for l in candidates] for i in range(len(players))])
    rows = np.vstack([np.ones(n),membership])
    objective = np.array([x['ev'] for x in metrics]) if mode == 'gpp' else np.array([
        means[l].sum() for l in candidates])
    result = milp(-objective, integrality=np.ones(n), bounds=Bounds(0,1),
                  constraints=LinearConstraint(rows,np.r_[entries,np.zeros(len(players))],
                                               np.r_[entries,np.full(len(players),cap)]),
                  options={'time_limit': 15})
    if result.x is None:
        raise ValueError('Exposure cap is infeasible for this candidate pool; increase candidates or exposure')
    return list(np.flatnonzero(result.x > .5))

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

from .models import ELIGIBILITY, SLOTS


def validate_lineup(players, lineup, rules=None):
    if len(lineup) != 8 or len(set(lineup)) != 8:
        raise ValueError('A lineup must contain eight distinct players in slot order')
    if any(i < 0 or i >= len(players) for i in lineup):
        raise ValueError('Unknown player in lineup')
    chosen = [players[i] for i in lineup]
    if any(not (set(p.positions) & ELIGIBILITY[s]) for p,s in zip(chosen,SLOTS)):
        raise ValueError('Invalid slot eligibility')
    salary = sum(p.salary for p in chosen)
    if salary > 50000 or len({p.game for p in chosen}) < 2:
        raise ValueError('Lineup violates salary cap or two-game requirement')
    if rules:
        ids = {p.id for p in chosen}
        if salary < rules.min_salary or not set(rules.locks) <= ids or ids & set(rules.excludes):
            raise ValueError('Lineup violates salary floor, locks, or exclusions')
        if max(sum(p.team == t for p in chosen) for t in {p.team for p in chosen}) > rules.max_from_team:
            raise ValueError('Lineup violates team limit')
        if any(chosen[SLOTS.index(s)].id != pid for s,pid in rules.locked_slots.items()):
            raise ValueError('Lineup moved a locked roster slot')


def solve(players, objective, rules, previous=(), as_of=None):
    n = len(players)
    pairs = [(i,s) for i,p in enumerate(players) for s,slot in enumerate(SLOTS)
             if set(p.positions) & ELIGIBILITY[slot]]
    m = len(pairs)
    if not m:
        raise ValueError('No eligible players')
    ids = {p.id: i for i,p in enumerate(players)}
    known = set(ids)
    if (set(rules.locks) | set(rules.excludes) | set(rules.locked_slots.values())) - known:
        raise ValueError('Rules reference an unknown player ID')
    if set(rules.locked_slots) - set(SLOTS):
        raise ValueError('Rules reference an unknown roster slot')
    if set(rules.locks) & set(rules.excludes):
        raise ValueError('A player cannot be locked and excluded')
    rows, lower, upper = [], [], []
    def add(values, lo, hi):
        rows.append(values); lower.append(lo); upper.append(hi)
    for s in range(8):
        add([int(slot == s) for _,slot in pairs], 1, 1)
    for i,p in enumerate(players):
        locked = p.id in rules.locks or p.id in rules.locked_slots.values()
        blocked = p.id in rules.excludes or (p.play_probability == 0 and p.id not in rules.locked_slots.values())
        # Players from started games may only remain in their original locked slots.
        if as_of and p.start <= as_of and p.id not in rules.locked_slots.values():
            blocked = True
        if locked and blocked:
            raise ValueError(f'{p.id}: locked player is unavailable or excluded')
        add([int(j == i) for j,_ in pairs], int(locked), 0 if blocked else 1)
    for slot,pid in rules.locked_slots.items():
        add([int(players[i].id == pid and SLOTS[s] == slot) for i,s in pairs], 1, 1)
    add([players[i].salary for i,_ in pairs], rules.min_salary, 50000)
    for game in sorted({p.game for p in players}):
        add([int(players[i].game == game) for i,_ in pairs], 0, 7)
    for team in sorted({p.team for p in players}):
        add([int(players[i].team == team) for i,_ in pairs], 0, rules.max_from_team)
    for old in previous:
        add([int(i in old) for i,_ in pairs], 0, 8-rules.min_unique)
    from scipy.sparse import csc_matrix
    result = milp(-np.array([objective[i] for i,_ in pairs]), integrality=np.ones(m),
                  bounds=Bounds(0,1), constraints=LinearConstraint(csc_matrix(rows),lower,upper),
                  options={'time_limit': 8, 'mip_rel_gap': 0.001})
    if result.x is None:
        raise ValueError('No feasible lineup found within the solver time limit; loosen rules or enlarge the player pool')
    lineup = [-1]*8
    for value,(i,s) in zip(result.x,pairs):
        if value > .5:
            lineup[s] = i
    validate_lineup(players,lineup,rules)
    return lineup


def candidates(players, scores, rules, count, rng, as_of):
    result = []
    mean = scores.mean(axis=0)
    for k in range(count):
        objective = mean if k == 0 else .45*mean + .55*scores[rng.integers(len(scores))]
        try:
            result.append(solve(players,objective,rules,result,as_of))
        except ValueError:
            if not result:
                raise
            break
    return result

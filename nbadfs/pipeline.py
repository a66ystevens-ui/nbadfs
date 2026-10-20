import numpy as np

from .contest import (evaluate_candidates, field_lineups, lineup_scores,
                      payouts_for_scores, select_portfolio)
from .demo import demo_players
from .models import SLOTS
from .optimizer import candidates, validate_lineup
from .simulation import simulate, summarize

VERSION = 'baseline-0.1.0'


def run(request):
    players = request.players if request.players is not None else demo_players(request.as_of)
    if not players or len({p.id for p in players}) != len(players):
        raise ValueError('Supply a nonempty pool with unique player IDs')
    for p in players:
        if any(t and t > request.as_of for t in [p.source_published_at,p.received_at]):
            raise ValueError(f'{p.id}: snapshot was not available at the requested as_of time')
    for team in {p.team for p in players}:
        group = [p for p in players if p.team == team]
        if len({(p.game,p.start) for p in group}) != 1:
            raise ValueError(f'{team}: inconsistent game or start time')
    for game in {p.game for p in players}:
        group = [p for p in players if p.game == game]
        if len({p.team for p in group}) != 2 or len({p.start for p in group}) != 1:
            raise ValueError(f'{game}: requires exactly two teams with matching start times')
    rules = request.rules.model_copy(deep=True)
    if request.current_lineup:
        if not request.contest.late_swap:
            raise ValueError('This contest does not support late swap')
        if set(request.current_lineup) != set(SLOTS):
            raise ValueError('Late swap requires all eight current roster slots')
        lookup = {p.id:i for i,p in enumerate(players)}
        try:
            original = [lookup[request.current_lineup[s]] for s in SLOTS]
        except KeyError as exc:
            raise ValueError('Current lineup references unknown players') from exc
        validate_lineup(players,original)
        for s,i in zip(SLOTS,original):
            if players[i].start <= request.as_of:
                if s in rules.locked_slots and rules.locked_slots[s] != players[i].id:
                    raise ValueError('Cannot change a started roster slot')
                rules.locked_slots[s] = players[i].id
    elif rules.locked_slots:
        raise ValueError('Provide current_lineup to verify exact late-swap assignments')
    if not request.current_lineup and any(p.start <= request.as_of for p in players):
        raise ValueError('Slate contains started games; provide current_lineup for late swap')
    seed = np.random.SeedSequence(request.seed).spawn(5)
    generation, _ = simulate(players,max(200,request.simulations//2),seed[0])
    pool = candidates(players,generation,rules,request.candidates,np.random.default_rng(seed[1]),request.as_of)
    # Independent draws prevent selection against the candidate generation sample.
    evaluation, minutes = simulate(players,request.simulations,seed[2])
    # Field opponents are unconstrained by the user's player rules.
    # For late swap, a pre-lock field approximation is used and explicitly disclosed.
    field_as_of = min(p.start for p in players) if request.current_lineup else request.as_of
    from datetime import timedelta
    field = field_lineups(players,generation.mean(axis=0),request.contest.size-1,
                         np.random.default_rng(seed[3]),field_as_of-timedelta(microseconds=1))
    metrics = evaluate_candidates(evaluation,pool,field,request.contest)
    chosen = select_portfolio(players,pool,metrics,request.entries,rules.max_exposure,
                              request.mode,evaluation.mean(axis=0))
    final_pool = [pool[i] for i in chosen]
    # Fresh finalist simulation; include self-competition among selected entries.
    final, _ = simulate(players,request.simulations,seed[4])
    returns,cash,wins = payouts_for_scores(lineup_scores(final,final_pool),
        lineup_scores(final,field[:request.contest.size-request.entries]),request.contest)
    lineups = []
    for j, i in enumerate(chosen):
        lineup = pool[i]
        net = returns[:,j]-request.contest.entry_fee
        lineups.append(dict(slots={s:players[k].id for s,k in zip(SLOTS,lineup)},
            salary=sum(players[k].salary for k in lineup),
            projection=float(final[:,lineup].sum(axis=1).mean()),
            selection_ev=metrics[i]['ev'], ev=float(net.mean()), roi=float(net.mean()/request.contest.entry_fee),
            ev_standard_error=float(net.std(ddof=1)/np.sqrt(len(net))),
            cash_probability=float(cash[:,j].mean()), win_or_tie_probability=float(wins[:,j].mean())))
    exposures = {p.id:sum(i in l for l in final_pool)/len(final_pool) for i,p in enumerate(players)}
    field_ownership = {p.id:sum(i in l for l in field)/len(field) for i,p in enumerate(players)}
    portfolio_net = returns.sum(axis=1)-request.entries*request.contest.entry_fee
    warnings = [
        'Synthetic demonstration only; no real player accuracy or profitability has been established.' if request.players is None
        else 'User-supplied data: source publication times and completeness have not been independently verified.',
        'Simulator is approximate and uncalibrated: shared game factors and 240 team-minutes; no possession conservation, overtime, or fitted teammate correlations.',
        'Synthetic field uses ownership-weighted legal constructions; achieved ownership is reported and is not calibrated to target marginals.',
        'Selection maximizes standalone estimated EV with hard exposures, then re-scores the portfolio jointly on fresh draws. It does not solve full portfolio payout interactions.',
        'EV standard errors measure simulation noise conditional on this model, excluding provider, projection, and field-model uncertainty.'
    ]
    if request.current_lineup:
        warnings.append('Late swap preserves started slots but uses pregame outcomes and a pre-lock field approximation; live score-conditional EV is not implemented.')
    if len(pool) < request.candidates:
        warnings.append(f'Only {len(pool)} unique candidates were feasible before solver limits.')
    if any(p.rates is None for p in players):
        warnings.append('Some players use aggregate FPPM rather than component-stat simulation; milestone bonuses are implicit in their historical FPPM.')
    return dict(model_version=VERSION, synthetic=request.players is None, as_of=request.as_of.isoformat(),
        seed=request.seed, candidate_count=len(pool), simulations=request.simulations,
        players=summarize(players,evaluation,minutes), lineups=lineups, exposures=exposures,
        field_ownership=field_ownership, warnings=warnings,
        portfolio=dict(ev=float(portfolio_net.mean()),
            standard_error=float(portfolio_net.std(ddof=1)/np.sqrt(len(portfolio_net))),
            loss_probability=float((portfolio_net < 0).mean()),
            cost=request.entries*request.contest.entry_fee))

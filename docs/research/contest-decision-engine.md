Yes. To build a genuinely strong NBA DraftKings system, I would build a contest-specific decision engine—not just a better fantasy-point predictor.

The most important distinction is **profitability**: the lineup with the highest projected score, the lineup with the highest 99th-percentile score, and the lineup with the highest expected tournament payout can be three different lineups. Published DFS optimization research explicitly models opponents and payout structures for that reason. Its empirical results were on NFL contests, so it provides a mathematical foundation—not proof of an NBA edge. [columbia](http://www.columbia.edu/~mh2078/DFS_Revision_1_May2019.pdf)

Below is the research-backed architecture I would use. I’m focusing on NBA Classic, with tournament play as the main objective, and distinguishing established rules and research from my proposed engineering choices. Nobody can credibly promise the “most accurate” model without timestamped, out-of-sample comparisons against strong alternatives.

## 1. Build six connected systems

| System | What it must predict or do | Proposed source of advantage |
|---|---|---|
| Data and news | What was known, when, and how reliable it was | Avoid stale information and invalid backtests |
| Player projections | Minutes, role, and statistical production | Better estimates of opportunity |
| Joint simulation | Plausible outcomes for every player together | Realistic lineup upside and downside |
| Opponent model | Which legal lineups the field will submit | Better ownership, duplication, and payout estimates |
| Optimization and selection | Generate candidates, then choose entries | Optimize actual contest objectives |
| Late-swap engine | Reevaluate remaining decisions as information arrives | Preserve flexibility and react correctly |

The central design principle is to keep these separate enough to test independently. Otherwise, a bad field model can hide behind good projections, or an unrealistic simulator can make a weak lineup look exceptional.

## 2. First priority: collect the right historical data

I would start collecting this before building complicated models.

### Required data layers

| Layer | Information to retain |
|---|---|
| DraftKings slate | Player IDs, salaries, position eligibility, game set, start times |
| Contest | Entry fee, field size, entry limit, payout ladder, actual entries where permitted |
| Basketball | Box scores, play-by-play, substitutions, starting and closing lineups |
| Availability | Injury reports, confirmed starters, restrictions, rest announcements |
| Context | Schedule, rest, travel, roster changes, coaching changes |
| Market | Timestamped spreads, totals, and player props from licensed sources |
| External forecasts | Timestamped projection and ownership snapshots, subject to licensing |
| Outcomes | Actual scoring, ownership, lineup results, and tie-adjusted payouts |

NBA injury reporting is a formal, recurring process, not a single daily announcement. For example, the league’s published 2025–26 requirements specify prior-day and game-day reporting windows, with different treatment for back-to-backs. Use the applicable season’s official requirements rather than hard-coding an assumed update schedule. [official.nba](https://official.nba.com/nba-injury-report-2025-26-season/)

Commercial feeds can supplement official reports with news, depth charts, and expected or confirmed lineups. RotoWire’s documented integration is one example; its existence does not establish that it is the fastest or best-value provider. [developer.opticodds](https://developer.opticodds.com/docs/rotowire-news-injury-data)

### The highest-value database feature: “as-of” reconstruction

Every record should have:

- When the event happened.
- When the source published it.
- When your system received it.
- Which forecast version used it.

Then you can ask:

> “What would my model have submitted at 6:55 p.m., using only information actually available then?”

My recommendation is to reject any backtest that cannot answer that question. Final injury statuses, closing lines, and retrospectively corrected projections must not leak into earlier decisions.

## 3. Make minutes and role the foundation

My proposed projection architecture is:

\[
\text{Availability}
\rightarrow
\text{Rotation scenario}
\rightarrow
\text{Minutes}
\rightarrow
\text{Statistical opportunities}
\rightarrow
\text{Production}
\rightarrow
\text{DK points}.
\]

This matches the practical importance projection services place on minutes and availability updates, but the particular modeling approach below is my recommendation, not a demonstrated industry-best method. [dimers](https://www.dimers.com/nba/player-projections)

### A. Predict distributions of minutes

Do not store only “32 projected minutes.”

Estimate:

- Probability of playing.
- Starting probability.
- Minutes conditional on starting or coming off the bench.
- Probability of a restriction.
- Closing-lineup participation.
- Competitive-game and noncompetitive-game rotations.
- Early-exit risk.

A hypothetical questionable starter might have:

| Scenario | Probability | Conditional minutes |
|---|---:|---:|
| Out | 20% | 0 |
| Active, restricted | 30% | 23 |
| Active, normal role | 50% | 35 |

Those are illustrative numbers. The point is that “24.4 expected minutes” hides very different outcomes.

### B. Enforce team-level consistency

In each regulation simulation, allocate 240 player-minutes per team. Add overtime minutes only when overtime occurs.

If one player gains eight minutes, those minutes need to come from someone else. Otherwise, the model manufactures opportunity.

Useful candidate features include recent substitution patterns, starter status, available teammates, coach behavior, position coverage, foul history, injury return, rest, and prior closing rotations. Test whether each feature improves held-out forecasts rather than assuming it helps.

### C. Model replacements conditionally

When a starter is ruled out, update more than the backup:

- Who starts?
- Who closes?
- Who handles the ball?
- Who gains shot attempts?
- Who gains rebounding opportunities?
- Does the second unit change?
- Does another player become more likely to play out of position?

I would use hierarchical estimates: combine player-specific evidence with team and role-level priors. A tiny “without Player X” sample should not override a much larger body of evidence without strong support.

## 4. Project opportunities and statistics—not only fantasy points per minute

A direct fantasy-points model is a useful baseline, but I would also build component forecasts:

| Component | Candidate inputs |
|---|---|
| Shot attempts | Minutes, possession involvement, lineup role |
| Three-point attempts | Shot profile and opponent tendencies |
| Free throws | Driving, usage, historical foul-drawing |
| Assists | Ballhandling and teammate scoring opportunities |
| Rebounds | Available misses, lineup composition, rebound share |
| Steals and blocks | Historical rates, role, opponent opportunities |
| Turnovers | Ballhandling load and opponent pressure |

DraftKings Classic scoring includes one point per real point, 0.5 per made three, 1.25 per rebound, 1.5 per assist, two per steal or block, minus 0.5 per turnover, and double-double/triple-double bonuses. Classic uses eight players under a $50,000 salary cap. Validate the exact contest configuration against the live rules and slate file before entry. [nbastuffer](https://www.nbastuffer.com/analytics101/dk-fantasy-points-fpts/)

Apply scoring bonuses to simulated stat lines—not to rounded average stats. A mean of 9.7 rebounds does not tell you the probability of reaching ten.

### My recommended model comparison

Build three competing approaches:

1. A simple minutes × production-rate baseline.
2. A gradient-boosted model using pregame features.
3. A hierarchical component model with explicit minutes and role scenarios.

Then test an ensemble. Choose weights using chronological validation, not intuition.

I would not begin with a large neural network. First establish whether complexity improves the decisions you actually make.

## 5. Simulate games jointly

The simulator should generate coherent slates, not independent random scores for each player.

Proposed sequence:

1. Sample availability and rotation scenarios.
2. Sample game environment and possessions.
3. Allocate player minutes.
4. Allocate offensive opportunities.
5. Generate shooting, assists, rebounds, defensive stats, and turnovers.
6. Apply overtime and rotation consequences consistently.
7. Calculate DraftKings scores.

### Why this matters

Two teammates can benefit together from extra possessions while competing for the same shots or rebounds. An assist and a made basket are linked events. A blowout can reduce several starters’ minutes together.

My recommendation is to model those mechanisms where feasible, rather than attach a permanent correlation coefficient to every player pair.

The DFS research supports including the joint player-outcome distribution and the relationship between your score and opponents’ scores. It does not establish that any particular NBA possession simulator is best. [columbia](http://www.columbia.edu/~mh2078/DFS_Revision_1_May2019.pdf)

### Keep two types of uncertainty separate

- Outcome uncertainty: the role is known, but shooting and other events vary.
- Model uncertainty: the role, minutes, or underlying rates may be wrong.

Sample plausible parameters as well as game outcomes. Otherwise, a simulator can produce extremely precise estimates from an incorrect assumption.

## 6. Build an opponent-lineup model—not just an ownership column

This is a major distinction.

Player ownership marginals do not determine how players appear together. Two fields can have similar individual ownership and very different lineup duplication and construction patterns.

Published DFS research explicitly models opponent selections and their dependencies, then evaluates entries against those simulated opponents. [columbia](http://www.columbia.edu/~mh2078/DFS_Revision_1_May2019.pdf)

### Proposed field model

Condition on:

- Slate size.
- Entry fee.
- Single-entry versus multi-entry.
- Player salaries and eligibility.
- Projection/value rankings.
- Available injury news.
- Salary remaining.
- Common roster constructions.
- Player-pair and player-cluster co-ownership.
- News timing relative to lock.

Generate only legal lineups, then calibrate the synthetic field to historical contest behavior.

Validate:

- Individual ownership.
- Co-ownership.
- Salary-used distribution.
- Frequency of popular lineup cores.
- Exact lineup duplication.
- Score and rank distributions.

Do not estimate duplicate probability by simply multiplying player ownership percentages. That ignores roster constraints and selection dependencies.

### Model field reaction to news

A useful research hypothesis is that fields react differently to news depending on timing and contest type.

Test that using historical snapshots. Do not assume every late announcement creates an exploitable delay; measure whether your forecast improves on an ordinary updated ownership model.

## 7. Optimize expected payout—not a homemade “ceiling score”

For lineup \(L\), the primary tournament quantity should be:

\[
EV(L)
=
\mathbb{E}
\left[
\text{tie-adjusted contest payout for }L
\right]
-
\text{entry fee}.
\]

Each simulated contest should:

1. Use one shared simulated NBA slate.
2. Score your candidates and a simulated opponent field on that slate.
3. Rank all entries.
4. Split payouts correctly across ties.
5. Record net returns.

This follows the contest-specific expected-reward framing in the research. [columbia](http://www.columbia.edu/~mh2078/DFS_Revision_1_May2019.pdf)

### Do not select only by 95th or 99th percentile

Percentiles are useful diagnostics, but they do not tell you:

- How many opponents beat you.
- Whether your best outcomes occur when popular lineups also explode.
- How many people split the prize.
- Whether the relevant payout is first place, top ten, or merely cashing.

Two lineups with the same 99th-percentile score can have different expected payouts.

Likewise, “optimal-lineup frequency minus ownership” can be a screening statistic, but it is not a complete EV model.

## 8. Separate candidate generation from final selection

I would not ask one optimizer to solve every problem at once.

### Candidate generation

Use a mixed-integer or constraint solver to create legal lineups across:

- Mean projections.
- Different rotation scenarios.
- Simulated outcomes.
- Plausible projection perturbations.
- Different field assumptions.
- Alternative roster constructions.

Avoid restrictive rules unless they represent a real constraint or survive validation.

### Independent evaluation

Evaluate candidates using a different simulation sample from the one that generated them.

Then evaluate finalists again on fresh draws and alternative model assumptions.

This is my recommended defense against “simulation overfitting”: searching thousands of candidates until one happens to look spectacular under noisy estimates.

### Portfolio selection

For multiple entries, distinguish the objective:

| Objective | Selection emphasis |
|---|---|
| Expected total profit | Sum of entry EVs, with contest interactions handled |
| Chance of a major finish | Joint success probabilities |
| Risk-controlled growth | Returns relative to bankroll and loss tolerance |

Correlation matters for portfolio risk and the chance of at least one big finish. It is not automatically a reason to reject a high-EV entry: expected returns add, while diversification changes their joint distribution.

Compare lineups by simulated return correlation and shared dependence on uncertain assumptions—not just the number of different players.

## 9. Treat late swap as a second optimization problem

DraftKings’ general rules state that in late-swap contests individual players lock at their scheduled game start. Not every contest supports late swap, so the engine must read the specific contest settings. [draftkings](https://www.draftkings.com/help/rules/overview)

My proposed engine would update:

- News and availability.
- Remaining legal players.
- Locked roster slots.
- Your actual points so far.
- Remaining opponent outcomes and observable ownership.
- The remaining payout opportunity.

Then choose among legal continuations.

### Useful design features

- Preserve flexible roster slots for later-starting players when it does not sacrifice a better decision.
- Precompute contingency swaps for unresolved injury situations.
- Include “news arrives before lock” and “news remains unresolved” branches.
- Compare staying put with swapping.
- Do not use “behind = always get contrarian” as an automatic rule.

The right decision depends on your conditional payout distribution, not just whether your current score looks low.

A more ambitious extension is to estimate the option value of flexibility: a lineup may be worth more because it permits better decisions after future news.

## 10. Prove each advantage with honest testing

I would maintain three scorecards.

### Projection quality

- Minutes MAE and RMSE.
- Fantasy-point MAE and RMSE.
- Availability probability calibration.
- Quantile loss.
- Prediction-interval coverage.
- Performance on injury replacements and uncertain rotations.

### Simulation and opponent quality

- Player and lineup tail calibration.
- Joint-event calibration.
- Ownership error.
- Co-ownership accuracy.
- Duplication accuracy.
- Distribution of simulated contest scores.

### Financial decision quality

- Net ROI after entry fees.
- Tie-adjusted payouts.
- Performance by contest type.
- Drawdowns.
- Uncertainty around estimated returns.
- Profit concentration in a few exceptional finishes.

Use rolling chronological holdouts and keep all games from a slate together. Compare against simple baselines and licensed commercial forecasts captured at matching timestamps.

The research itself cautions that even a profitable NFL season had too much variance to support strong empirical conclusions. Your NBA evaluation should similarly report uncertainty rather than treat a winning stretch as proof. [columbia](http://www.columbia.edu/~mh2078/DFS_Revision_1_May2019.pdf)

### Run ablation tests

Remove one feature at a time:

- No props.
- No tracking features.
- No field model.
- No joint correlations.
- No scenario minutes.
- No late swap.
- No portfolio selection.

This tells you what actually earns its complexity and cost.

## 11. Where I would look for an advantage first

These are my priorities to test—not guaranteed edges:

| Priority | Hypothesis |
|---|---|
| 1 | Better minutes and closing-role forecasts improve decisions more than adding obscure matchup statistics |
| 2 | Reliable news-to-forecast updates beat slightly more elaborate but stale projections |
| 3 | Contest-specific field models improve payout estimates over generic ownership |
| 4 | Joint simulation improves lineup selection over adding player ceilings |
| 5 | Duplication-aware evaluation identifies constructions that raw score rankings miss |
| 6 | Independent simulation evaluation reduces false “best lineup” discoveries |
| 7 | Late-swap flexibility improves decisions on uncertain slates |
| 8 | Small residual corrections to strong consensus forecasts outperform rebuilding everything from scratch |

I would also investigate opportunity-adjusted production, rotation substitution patterns, and conditional teammate effects—but only keep them if they improve held-out performance.

## 12. Practical build order and implementation

### Phase 1: trustworthy baseline

- Timestamped data store.
- Exact slate and scoring validation.
- Simple minutes and production forecasts.
- Legal-lineup optimizer.
- Reproducible historical replay.

### Phase 2: better basketball uncertainty

- Availability and rotation scenarios.
- Team-constrained minutes.
- Component statistics.
- Calibrated joint simulations.

### Phase 3: better contest decisions

- Contest-specific ownership.
- Legal synthetic fields.
- Duplication and payout simulation.
- Independent candidate evaluation.

### Phase 4: live decision support

- News-triggered recomputation.
- Conditional late-swap recommendations.
- Contingency exports.
- Monitoring and audit logs.

My suggested stack is Python, Parquet with DuckDB or PostgreSQL, LightGBM/CatBoost for tabular baselines, a hierarchical modeling library where needed, and OR-Tools or a mixed-integer solver for roster construction. Start with vectorized simulation; add more compute only after measuring a bottleneck.

I would budget first for dependable historical snapshots, permitted contest data, and reliable news—not expensive hardware. The actual cost and timeline depend heavily on licensing and how much production engineering you already have.

## 13. Keep the advantage within the rules

DraftKings permits reference materials, projections, and lineup-construction tools used to help you skillfully build entries. It prohibits collusive complementary lineup coordination and entry-limit circumvention; its fair-play policy also prohibits browser scripts or bots. Keep independent judgment in the process, use permitted upload workflows, and verify current terms before automating any interaction with the platform. [draftkings](https://www.draftkings.com/community-guidelines)

My overall recommendation: build the minutes/news engine and a timestamp-correct baseline first, then add a calibrated simulator, and only then build the opponent-aware payout selector. A more complicated optimizer cannot repair stale roles, inaccurate minutes, or an unrealistic simulated field.

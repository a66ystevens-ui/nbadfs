# CourtVision · NBA DFS Lab

**Browser hosting:** the Streamlit app is ready for Streamlit Community Cloud. See [browser-only deployment instructions](docs/streamlit.md). Use `streamlit_app.py` as the entry point. Optional private Supabase checkpoints retain the archive across free-host restarts; without them, download/restore archives manually.

A working NBA DraftKings Classic research application: import a slate, forecast minutes and production, simulate outcomes, generate legal lineups, select a portfolio, and export CSV. Built from the three supplied research documents, retained under [docs/research](docs/research).

**Current status:** functioning baseline and synthetic demo. SportsDataIO/public NBA adapters and an append-only source archive are implemented; authenticated live ingestion awaits your key. There is no trained ownership model or demonstrated forecasting/profit advantage. The full development roadmap and release gates are in [the architecture](docs/architecture.md). [Provider selection](docs/providers.md) explains the next data decision.

## Run

Python 3.12 is the tested runtime. From `/workspace/nbadfs`:

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m uvicorn nbadfs.app:app --host 127.0.0.1 --port 8000
```

The server serves the workspace at `/`, OpenAPI reference at `/docs`, and health at `/api/health`. Click **Generate portfolio** for the labeled fictional slate. No API credentials are required for the demo or imports. For real feeds, follow [SportsDataIO setup and archival workflow](docs/data.md). It is a single-user local application; authentication and hosted deployment are outside this release.

The default is 3 entries selected from 24 candidates, 1,000 simulation draws per evaluation stage, and an 81-entry fictional contest. Edit the actual fee, total field size, entry limit, and full payout ladder in advanced settings. The engine does not scale a small sampled field into a large contest: it models the declared field exactly, up to 501 entries. Overlay contests are not supported yet. Solver time grows with field and candidate counts.

## Import your slate

Use a normalized CSV (UTF-8). Positions are slash-separated base positions. Ownership and play probabilities use fractions, not percentages. Every timestamp needs a timezone. Include the full rotation of each of the two teams in every game so 240 minutes can be allocated consistently. Omitted bench players otherwise cause remaining players' minutes to inflate. The parser cannot establish that your rotation is complete; verify it.

```csv
id,name,team,game,positions,salary,start,minutes,fppm,minutes_sd,play_probability,ownership,source_published_at,received_at
123456,Example Guard,ATL,ATL-BOS,PG/SG,6800,2026-10-21T23:00:00Z,33,1.10,3,1,0.18,2026-10-21T20:00:00Z,2026-10-21T20:00:05Z
```

This single example illustrates the schema; an executable slate needs enough players, teams, and games to form legal lineups. `game` must be a consistent game ID; teams in the same game must share start times. `minutes` is conditional on playing. Players ruled out retain conditional minutes but use `play_probability=0`. All IDs must be unique and match the official DraftKings slate for actual exports. The engine rejects future-dated source/received timestamps relative to `as_of`, but cannot verify claims about external publication times. Missing timestamps permit experimentation, not auditable historical results.

Official salary column aliases `ID`, `Name`, `Position`, `Salary`, and `TeamAbbrev` are accepted. A raw salary export alone is insufficient: augment it with normalized `game` and timezone-aware `start`, and supply projections or matching historical logs. The parser intentionally does not guess timezones from `Game Info`.

Optional component rates: `two,three,ft,reb,ast,stl,blk,tov`, all per minute. `two` and `three` mean **made** two- and three-point field goals, and `ft` means made free throws. Supply all eight or none. In component mode scoring comes from simulated statistics; `fppm` remains required for the common input schema but does not override component scoring. Without rates, historical FPPM already includes milestone bonuses and is simulated as an aggregate.

### Estimate forecasts from history

Attach a historical CSV alongside salaries instead of supplying `minutes` and `fppm`:

```csv
player_id,game_date,available_at,minutes,dk_points
123456,2026-10-01T23:00:00Z,2026-10-02T03:00:00Z,32,38.5
```

`game_date` is the game start time, not a date-only string; `available_at` is when this outcome was actually available to your pipeline. IDs must map to the slate. Forecasts use up to 20 prior positive-minute games, exponential decay 0.9 per game, and a 20-minute FPPM shrinkage prior of 1.0. This is an explicit baseline, not an injury-aware fitted ML model. There is no inferred usage boost from absent teammates.

`POST /api/backtest` with `{"history_csv":"..."}` runs expanding chronological player-level validation with at least three prior games. It reports evaluated/skipped observations and minutes/points MAE/RMSE. Zero eligible holdouts is an error. DNPs can be evaluated when recorded, but positive-minute training history does not itself model availability. This endpoint is a projection diagnostic, **not** a contest-profit replay.

## Rules and late swap

Locks/excludes are comma-separated IDs. `min_unique` applies between all candidate lineups and consequently selected entries. `max_exposure` is enforced by an integer portfolio solve: at 50% over three entries, a player can appear only once. Infeasible caps return an explanation instead of silently exceeding them. API rules additionally include `max_from_team`.

For late swap, send a single-entry `/api/run` request with the full player pool, current `as_of`, and `current_lineup` mapping **all eight** slots to player IDs. In advanced JSON this looks like:

```json
{"as_of":"2026-10-21T23:10:00Z","current_lineup":{"PG":"id1","SG":"id2","SF":"id3","PF":"id4","C":"id5","G":"id6","F":"id7","UTIL":"id8"}}
```

Those IDs are placeholders; use the actual imported slate. Set entries to one. Started players stay in their exact original slots; newly adding any other started player is prohibited. Contests with `late_swap=false` reject swap requests. This is a legality-preserving pregame re-optimization, **not** live-score-conditional tournament EV. Keep original started players in the input even if subsequently ruled out. `rules.locked_slots` may add restrictions, but started assignments are derived and verified against `current_lineup`.

## Outputs and replay

CSV export has ordered headers `PG,SG,SF,PF,C,G,F,UTIL` and player IDs. It is a lineup CSV, not a completed contest-entry edit template. Verify the current DraftKings template and rules before manual use. Demo IDs cannot enter real contests. There is no browser automation, account login, entry submission, or payment action.

SQLite at `data/research.sqlite` stores each successful run's exact expanded input, as-of time, actual server receipt time, model version, seed, and output. Override the path with `NBADFS_DB`. Local data and the virtual environment are ignored by Git. Stored runs are immutable through the API; `GET /api/runs/{id}` retrieves the record and `GET /api/runs/{id}/export` downloads its lineups. A run can be reproduced by submitting its stored request to `/api/run` under the pinned environment. Demo provenance remains in the original result; replaying expanded fictional inputs directly is labeled user-supplied.

## Read modeled EV correctly

Generation, candidate evaluation, and final portfolio scoring use independent seeded draws. Opponents are ownership-weighted legal lineups; repeated lineups are allowed and ties split the prizes across all tied places, including unpaid ranks. Selected entries compete with each other in the final payout evaluation.

Selection maximizes the sum of estimated standalone entry EV subject to exposures. Final evaluation accounts for self-competition, but selection is not a global solution to portfolio payout interactions or bankroll growth. Cash mode selects by mean score, with no claim that this alone maximizes double-up profit. Floor/ceiling are P10/P90, and means and uncertainty reflect the simulator. Reported EV standard errors only describe Monte Carlo noise conditional on an uncalibrated model. They exclude uncertainty in player rates, news, field construction, and data quality. A positive demo EV is not evidence of a profitable system.

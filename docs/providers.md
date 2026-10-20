# Choosing the data stack

Recommendation: evaluate **SportsDataIO NBA** first for a consolidated basketball feed, use **official DraftKings salary exports** for contest IDs/eligibility, and separately establish licensed projection/ownership and historical-contest access. Ask vendors for representative samples before committing. This is an architecture recommendation, not a current-price comparison or a claim that an account or plan was tested.

| Need | Candidates | What to establish before paying |
|---|---|---|
| Box scores, schedules, roster/availability | [SportsDataIO NBA](https://sportsdata.io/developers/api-documentation/nba), [BALLDONTLIE](https://docs.balldontlie.io/) | Exact plan coverage, seasons, stable player IDs, delay, corrections, quotas, injury history, permitted retention |
| Experimental historical stats / play-by-play | [nba_api](https://github.com/swar/nba_api), NBA official sources | Endpoint reliability, access/usage terms, rate limits, historical completeness; community library support is not an SLA |
| News, depth charts, expected/confirmed lineups | [RotoWire](https://www.rotowire.com/about/contact.php), official NBA injury reports, licensed feed integrations | Publication timestamps versus poll times, update latency, structured IDs, redistribution/training rights, backfill and outage behavior |
| Projections and ownership benchmark | Licensed offerings from established DFS projection vendors | API/download rights, historical pre-lock snapshots, contest-specific ownership, permitted model training and local archival; consumer UI access does not establish API rights |
| Odds and props (later) | [The Odds API](https://the-odds-api.com/), licensed sports feeds | NBA player market coverage, timestamps, historical snapshots, bookmaker coverage, limits and modeling rights |
| Salaries and contest results | Official DraftKings CSV workflows, permitted historical contest files | IDs, position changes, game-start rules, full payout ladder, ties, entry limits, ownership and contest-entry reuse rights |

Do not choose based only on a vendor saying it includes “injuries.” A current final status cannot reconstruct what was known 20 minutes before lock. A projection archive without original timestamps is not a usable temporal benchmark. Providers' current plans, fees, and licenses must be confirmed directly; none were purchased or connected here.

## Procurement order

1. Request a trial/sample with two ordinary slates, one major late-scratch slate, full historical player logs, injury-event revisions, and stable IDs. Determine whether you can retain raw responses and use them for private model training.
2. Verify source publication and actual receipt timestamps, distinction between unknown versus active, reliable game-start times, and historical revisions. Check how trades and multi-position changes map into DraftKings exports.
3. Import historical stats and a salary slate into the current app. Confirm full rotations, DK scoring agreement, and player-ID joins. Run chronological error reports before buying more advanced tracking data.
4. Obtain a licensed time-matched projection/ownership baseline and permitted contest exports. These are needed for field calibration and honest payout backtests; basketball stats alone do not supply them.
5. Add props/markets and more expensive tracking only after an ablation demonstrates useful additional signal.

If budget is tight, begin with NBA historical experiments plus manual salary/projection CSV imports. Treat availability as manually verified, keep original files with timestamps, and run in shadow mode. That supports development but does not establish a reliable live trading/entry workflow.

## Integration contract

Implement provider adapters as `fetch -> immutable raw snapshot -> identity mapping -> normalized records`. Never put vendor-specific IDs or responses directly into optimization logic. Capture provider/source, event time, source publication time, ingestion time, revision, and payload hash. Keep input rights and retention policy alongside source metadata.

Once a provider is selected, inspect existing secret names/bindings before configuring new credentials. Use secure environment settings, never chat or committed `.env` files. Permit only the vendor's documented HTTPS API hosts. SportsDataIO credentials are now optional for the baseline and required for its selected live feeds; their requirement and host access have been saved without values.

## Connection implemented

SportsDataIO was selected during implementation. See [the connection guide](data.md) for secure key setup, supported feeds, salary/projection joins, polling, archival behavior, and live-verification limits. The provider key requirement and API host access are saved in environment settings; credentials must be supplied securely there.

# SportsDataIO connection and source archive

## Connect your account

**Fastest option:** in CourtVision → Data connections & archive, enter the key in the password field and click **Connect & test**. The server holds it in process memory, clears the browser input, and tests NBA Players access. The key clears when the server restarts. **Clear session key** removes this override and falls back to any environment binding. It is not stored in the source archive, run inputs, or browser storage. Separate CLI collectors cannot inherit a dashboard session key; use the persistent environment method below for them.

### Persistent environment configuration

1. In SportsDataIO, open your account's API subscriptions/API keys and locate the key for your **NBA** product. Product access matters: scores, box scores, injuries, and projections may have different entitlements. Trial data may be scrambled; confirm your account's data mode before modeling real outcomes.
2. In this cloud environment's settings, enter the key securely for the saved secret requirement **`SPORTSDATAIO_API_KEY`**, scoped to **`api.sportsdata.io`**. Do not paste it into chat, a terminal command, a CSV, or a committed file. CourtVision sends the binding as the `Ocp-Apim-Subscription-Key` request header, never a URL parameter.
3. Review and save the pending environment settings, then publish as required by the environment UI. Once the settings have reached the running environment, restart CourtVision and any collectors so they inherit the updated binding. A key being present is not proof of authorization.
4. In CourtVision's **Data connections & archive** panel, choose **SportsDataIO → players → Fetch & archive**. A successful response confirms that operation and stores the raw payload plus normalized records. Then test **injuries**, and **games** for your chosen New York game date.
5. If subscribed, fetch **projections** for that same date. **player stats** are actual box-score results, not forecasts; do not use them as pregame projections. A 401/403 can mean an invalid key, missing NBA product access, or a trial limitation. Check the specific endpoint's subscription, not just whether the key works elsewhere.

No paid requests start automatically on installation or web startup. Each dashboard refresh is one request. In the current instance the unauthenticated SportsDataIO Players endpoint returned HTTP 401, establishing network reachability but not authenticated access. No authenticated request has been verified with the user's key. The dashboard entry field allows that test without placing a key in commands or source files. Public NBA CDN access returned an upstream Access Denied response and is not a working fallback here.

## Feeds

| Provider | Feed | Input | Endpoint / content |
|---|---|---|---|
| SportsDataIO | `players` | None | NBA scores `Players`; identity, team, position, availability fields where supplied |
| SportsDataIO | `injuries` | None | NBA scores `InjuredPlayers`; provider statuses/notes, not invented play probabilities |
| SportsDataIO | `games` | New York game date | NBA scores `GamesByDate`; teams, UTC start, status, scores |
| SportsDataIO | `player_stats` | Game date | NBA stats `PlayerGameStatsByDate`; actual counting statistics and DK scoring |
| SportsDataIO | `projections` | Game date | NBA projections `PlayerGameProjectionStatsByDate`; requires projection access |
| NBA public | `scoreboard` | None | Current public scoreboard; past observations come from our archive |
| NBA public | `schedule` | None | Current public league schedule |
| NBA public | `boxscore` | Ten-digit NBA game ID | Public game box score, including player IDs and stats if supplied |

SportsDataIO dates are sent as `YYYY-MMM-DD`. API requests accept ISO `YYYY-MM-DD`. Do not assume vendor IDs equal NBA IDs or slate-specific DraftKings IDs. Missing fields remain unknown; raw payloads are retained if a schema fails validation. Endpoint implementations have representative fixture coverage and still require validation against your subscription's live responses.

## Use real projections with DraftKings salaries

The **Join salaries + SportsDataIO** button joins a salary file to the latest successfully archived SportsDataIO **games** and **projections** snapshots for the selected date.

1. Fetch games and projections for the correct date in the data panel.
2. Choose the official DraftKings Classic salary CSV under **Bring your own slate**.
3. If the provider's DraftKings ID does not exactly match that salary file's ID, supply an explicit identity CSV:

   ```csv
   id,provider_player_id
   12345678,20001234
   ```

   `id` is the exact salary-file ID; `provider_player_id` is SportsDataIO's PlayerID. Do not guess mappings from names. Team abbreviation disagreements are rejected, not silently reconciled.
4. Click **Join salaries + SportsDataIO**, review import notes, then generate a portfolio. Raw salaries, mapping CSV, game/projection snapshots, and the final run request are retained. Zero-minute projected players are omitted with a count; verify the remaining rotations are complete.

Projection minutes and DraftKings points become the aggregate FPPM baseline. This does not train a CourtVision model. Ownership remains an uncalibrated default: tournament EV is still experimental. Missing injury status is not proof of confirmed availability. Out players receive zero play probability; unresolved statuses require explicit user probabilities via `/api/data/slate` (`play_probabilities`, keyed by DraftKings salary ID). Fetch updated projections after injury news; a separate injuries fetch is archived but does not silently rewrite projections. Live probabilities, role redistribution, independent ownership, and trained models remain future work.

The exact source snapshot IDs accompany imported players and are saved with the run. The API rejects unknown, failed, or future-received snapshots as forecast sources. Referencing a source describes provenance; it does not prove that the model used all its fields. Manual overrides remain visible in the saved run input.

## One-time fetches and continuous collection

From `/workspace/nbadfs`, with the credential already injected securely:

```bash
.venv/bin/python -m nbadfs.collector --provider sportsdataio --feed players
.venv/bin/python -m nbadfs.collector --provider sportsdataio --feed games --date 2026-10-20
.venv/bin/python -m nbadfs.collector --provider sportsdataio --feed player_stats --date 2026-10-19
.venv/bin/python -m nbadfs.collector --provider sportsdataio --feed injuries --watch --interval 60
```

Use the dates for the slate/history you actually want. Daily feeds without `--date` resolve the current New York date on every cycle, including after midnight. A fixed date stays fixed. Run additional feeds in separate persistent execution sessions only when desired. Keep one collector per feed/resource; multiple collectors create extra requests and observations. Check your subscription quota before choosing an interval. Installation and startup never enable polling implicitly.

Polling records each observed response, including unchanged bodies, and backs off after failures. It honors `Retry-After` on HTTP responses and shuts down on SIGINT/SIGTERM. Polling cannot recover provider revisions that appeared and disappeared **between** requests, or updates during downtime. Capturing every upstream event would require a provider-supported event stream or complete revision history; neither is implemented. Processes must be restarted after a cloud task or machine restarts.

## Archive contract

The same `NBADFS_DB` SQLite database stores:

- `source_blobs`: SHA-256-addressed response entity bytes (HTTP decompression already applied), deduplicated without overwriting.
- `source_snapshots`: every observation, resource, HTTP status, receipt time, optional generation/publication claims, and selected cache/content headers. Repeated identical responses have separate observation IDs.
- `source_records`: normalized records tied to a snapshot and normalizer version.
- `source_attempts`: success, HTTP error, network failure, missing configuration, size-limit failure, or normalization failure. Errors with responses retain the response; transport failures have no response to archive.
- Existing run records: exact inputs, as-of time, seed/model version, referenced snapshots, and outputs.

SQLite triggers reject UPDATE and DELETE of archive tables. This is application-level append-only protection, not externally tamper-proof or write-once storage: the database owner can change the schema. Raw payload integrity is checked on download. Request authorization headers, environment values, session keys, and arbitrary response headers are not stored. If a response echoes the configured key, the body is discarded and a safe failure is recorded. Responses above 20 MB are refused and logged without retaining a truncated payload.

`received_at` is assigned by CourtVision when it receives the completed response. Unknown publication times stay null. NBA `meta.time` is retained as a source generation claim, not mislabeled as publication time. SportsDataIO `Updated` is retained in its original form when its timezone is ambiguous. Event/game time can be in the future and does not determine what was known. Historical data fetched today was first observed today; it is never backdated to its game date.

CSV imports, including invalid uploads, are archived. Current API backtest inputs are archived too. Raw data stays locally in the database, not in Git. Protect/retain that database according to your provider's license. There is no automatic remote backup, retention deletion, historical contest archive, or recovery of pre-connection injury revisions. A cloud snapshot alone is not a continuously updated backup.

## Query and replay

- `GET /api/data/status`: presence-only provider configuration, archive counts, recent outcomes. It does not validate a subscription or start a poller.
- `POST /api/data/sync`: e.g. `{"provider":"sportsdataio","feed":"injuries"}` or `{"provider":"sportsdataio","feed":"games","date":"2026-10-20"}`.
- `GET /api/data/snapshots?limit=30&before=<sequence>`: bounded, descending pagination.
- `GET /api/data/snapshots/{id}`: metadata and normalized records.
- `GET /api/data/snapshots/{id}/raw`: original entity bytes as a download with checksum.
- `GET /api/data/as-of?provider=sportsdataio&feed=games&resource=2026-10-20&at=<timezone-aware timestamp>`: latest successful, normalized observation received by that time. HTTP and parsing failures are excluded. Empty-but-valid responses are real observations and are not replaced with stale data.
- `POST /api/data/slate`: `csv_text`, `date`, optional `identity_csv`, optional `play_probabilities`; returns normalized players, warnings, and source IDs.

Read `/docs` for exact request schemas. Archive availability does not imply automated news-driven re-optimization. The supported workflow remains fetch, review, import/join, generate, and manually export.

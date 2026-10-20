# Validation record

Validated in the current cloud instance with Python 3.12.14 and `requirements.lock`.

- Installed dependencies into a new isolated `.venv`; repeated installation from the fully pinned lockfile successfully. `pip check` reported no broken requirements.
- All 11 `unittest` tests passed: Classic scoring and milestone bonuses; availability and conserved rotations; deterministic simulation; positional/salary/lock/exclusion/team/diversity constraints; two-game infeasibility; tie splitting and self-competition; integer exposures; temporal history filtering; CSV and timestamp validation; future-snapshot rejection; exact late-swap locks; API, run persistence, and export.
- Started the actual Uvicorn service and exercised HTTP health, the full default build, archived input retrieval, and CSV export. That run used 60 fictional players, 24 unique candidates, 3 selected entries, and 1,000 draws per evaluation stage, and completed in about 9 seconds. This is one functional observation, not a benchmark guarantee.
- JavaScript parsed successfully with `node --check`. The HTML, CSS, and script are served locally with no external font dependency. No automated browser/layout or accessibility audit was run.
- No real NBA history, original injury snapshots, licensed ownership, actual contest fields, or live vendor credentials were available. Consequently no real-player prediction accuracy, calibrated intervals, contest ROI, live news latency, or provider integration was validated.
- FastAPI's test client emits a non-fatal Starlette warning that its `httpx` compatibility path is deprecated. The pinned client works; future dependency upgrades should migrate the test client as documented upstream.

Repeat verification from the repository root:

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m pip check
node --check nbadfs/static/app.js
```

Successful local checks do not establish publication or restoration in a new cloud task.

## Data connection and archival extension

All **25 tests** pass after adding source ingestion and dashboard credential entry. New coverage includes repeated/corrected payload retention, append-only triggers, as-of receipt boundaries, malformed and HTTP-error archival, safe network-failure logging, SportsDataIO header authentication using fixture keys, key-echo rejection, exact-ID salary joins, forecast provenance gates, retry/backoff, CSV archival, and memory-only session credentials with non-echoing validation errors and cross-origin rejection. Fixtures are representative test data, not evidence that a paid subscription was exercised.

Actual connectivity checks: SportsDataIO's unauthenticated NBA Players endpoint returned HTTP 401, proving reachability but not key authorization. NBA CDN returned an Access Denied HTTP 403; that response was archived. No real basketball records have been ingested yet. The dashboard, local health, data-status and archive listing routes respond successfully. Browser layout and user-entered live-key verification remain unrun.

## Streamlit hosting extension

All 31 tests pass with Streamlit 1.66.0 and Python 3.12. AppTest executes the UI, generates two demo entries, exercises password-gated navigation and private key entry, and verifies independent session databases. Context tests run two user scopes in parallel. Backup tests cover full/delta restoration, idempotence, hash verification, immutable conflict rejection, and mocked private Supabase upload/list/download/restore. The live Streamlit server serves both its root page and health endpoint successfully; dependency checks pass.

No Community Cloud app has been published by these checks, and no real Supabase account or authenticated SportsDataIO request was exercised. Deployment/account authorization and live persistence verification remain separate steps.

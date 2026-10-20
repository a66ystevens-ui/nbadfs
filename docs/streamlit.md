# Open CourtVision from your work browser

CourtVision now has a Streamlit entry point: **`streamlit_app.py`**. You do not need to install Python or run commands on your work computer. The existing FastAPI interface is still available for local/API development.

## Publish on Streamlit Community Cloud

1. Sign in at **https://share.streamlit.io/** using your GitHub account. Authorize access to your CourtVision repository if requested.
2. Choose **Create app** / **Deploy an app from GitHub**, then set:

   | Field | Value |
   |---|---|
   | Repository | `a66ystevens-ui/nbadfs` |
   | Branch | `main` |
   | Main file path | `streamlit_app.py` |
   | Python | `3.12` in Advanced settings |

3. In **Advanced settings → Secrets**, add `APP_PASSWORD` with a long private password you choose. Optionally add `SPORTSDATAIO_API_KEY` to save the provider key server-side. The field names are shown in [.streamlit/secrets.example.toml](../.streamlit/secrets.example.toml); enter actual values only in Streamlit's Secrets panel. Never commit `.streamlit/secrets.toml`.
4. Click **Deploy**. Streamlit will install the root `requirements.txt` and give you a browser URL. Bookmark that URL on your work computer. A configured password gates all app tabs before source data or shared credentials can be used.
5. Sign in with your app password. Enter a session SportsDataIO key in the sidebar and click **Connect & test**, or use the saved server key. Fetch **games** and **projections** for the same New York slate date if your subscription includes projections. Import the DK salary file and explicit identity mappings, then build and download lineups.

The source code must be present in the selected branch before deployment. Streamlit requires your own sign-in; local preparation or a GitHub push is not a published app. No production URL should be inferred until Streamlit actually deploys it.

The hosting app is free within Community Cloud's resource/usage limits. SportsDataIO's subscription is separate. No paid hosting plan or automatic paid upgrade is required by this implementation. Confirm current service limits in your account; hosting vendors can change them.

## Keep the archive when the app sleeps

Streamlit's local filesystem is not durable hosting storage. Each browser session has an isolated local SQLite archive and key. Session-only keys and uploads can disappear when the session ends or hosting restarts. The app shows a warning until a persistent archive is configured.

Without Supabase, use **Archive & saved runs → Download complete archive**, save the `.json.gz` file, and upload it later using **Restore a CourtVision archive**. This restores data-only records and verifies source hashes without running uploaded code. It cannot recover changes you did not download.

### Optional free Supabase Storage

1. Create a free Supabase project at **https://supabase.com/**. Use its free plan and check storage/egress limits; do not enable a paid upgrade for this setup.
2. In **Storage**, create a **private** bucket named `courtvision`. A public bucket is not suitable for private source payloads.
3. Find the project's HTTPS URL and **legacy `service_role` API key** in the project settings. Use the server-side service-role credential, not the anon key. Enter these only in Streamlit's private Secrets panel:

   ```toml
   APP_PASSWORD = "your-own-private-app-password"
   SUPABASE_URL = "https://yourproject.supabase.co"
   SUPABASE_SERVICE_ROLE_KEY = "your-server-side-service-role-key"
   SUPABASE_BUCKET = "courtvision"
   ARCHIVE_NAMESPACE = "owner"
   ```

   Those strings are placeholders, not usable credentials. This implementation supports the HTTPS `*.supabase.co` project URL and JWT-style legacy service-role key. Do not put these values into GitHub, a CSV, the provider key input, or chat. The service-role key has elevated project permissions and stays server-side behind the app password.
4. Reboot/reopen the Streamlit app after saving Secrets. It restores the namespace's existing checkpoints, and after each fetch/import/build action uploads an immutable incremental checkpoint containing new archive rows and runs. Repeated raw responses remain separate observations, while their already-saved bytes are not uploaded again in later deltas.
5. Verify a real fetch and a saved run, reboot the app, and confirm that **Archive & saved runs** restores both. Cloud credentials and an actual Supabase project were not available during development, so this live restart test must be performed after configuration. Storage HTTP/protocol behavior and incremental restoration were tested with a mocked Supabase service.

Use a dedicated namespace for this app and leave its checkpoint objects intact. Each checkpoint contains references to earlier content. Deleting arbitrary objects can make later restoration incomplete. All holders of the same app password share this owner archive; this is not a hosted service with individual user accounts. In separate deployments use different namespaces.

Cloud checkpoint failures are shown explicitly. Local changes remain in the current session, and **Retry cloud checkpoint** retries pending rows. A failed upload is not a successful durable save. Download a manual archive if a cloud error persists. Restore merges immutable identities and refuses conflicts rather than overwriting records.

Each portable checkpoint has a 50 MB expanded-size limit. Long histories can exceed the free tier's storage, bandwidth, or compute limits. This initial free app is suitable for interactive research; large historical pipelines need a separate storage design. No automatic retention deletion or external write-once guarantee is implemented.

## Hosting behavior

- No local installation on the work computer: all interaction is through the deployed browser URL.
- Without `APP_PASSWORD`, demo/CSV workflows and each visitor's own provider key are available, but shared server API keys and shared Supabase storage are disabled.
- Session provider keys are isolated with request-local contexts. They do not mutate process-wide environment values or leak into another browser's session. Password inputs are cleared after submission.
- Restarts do not preserve a sidebar key. A server key in Streamlit Secrets persists independently and is available only after the password gate.
- Free hosting may sleep. Fetches occur on request; there is no continuous injury/news collector or promise to capture updates during downtime.
- Supabase checkpoints preserve observed source data, normalized records, receipt times, failures, saved inputs, and results. They are backups of the observation archive, not a claim that every upstream revision was observed.
- Selection and simulation retain the baseline limitations documented in README.md. Positive synthetic EV is not proof of an edge.

## Development verification

The tested runtime is Python 3.12 with `requirements.lock`. Root `requirements.txt` contains the pinned primary dependencies used by Community Cloud. Developers can run:

```bash
.venv/bin/python -m streamlit run streamlit_app.py --server.headless true
.venv/bin/python -m unittest discover -s tests -v
```

Tests include Streamlit's AppTest: actual demo generation, password-gated navigation, session key entry, and independent browser-session databases. Separate tests cover parallel credential contexts, incremental archive backup/restore, conflict rejection, and Supabase upload/list/download behavior. They do not replace a live Community Cloud deployment or live provider authentication check.

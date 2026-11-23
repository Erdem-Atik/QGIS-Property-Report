# Deploy it yourself

This guide sets up the whole system on your own computer: you add a row to a Google Sheet,
and a PDF location report arrives by email a minute or two later.

```
Google Sheet  ──►  n8n (checks the sheet every minute)  ──►  Report API (QGIS, Docker)
     ▲                         │
     └──── status ◄────────────┴──►  Gmail (PDF attached)
```

**What you need**

- Docker Desktop (Windows, macOS) or Docker Engine with Compose (Linux)
- About 5 GB of free disk space
- A Google account (for the Sheet and for sending email) and access to
  [Google Cloud Console](https://console.cloud.google.com/) (free)
- Optional: an API key for an OpenAI-compatible LLM (e.g. [Groq](https://console.groq.com/))
  for the AI summary; without it the PDF is still produced

**Time:** about 30–45 minutes, most of it the one-time Google setup and downloads.

**Steps**

1. [Start the report API](#1-start-the-report-api)
2. [Start n8n](#2-start-n8n)
3. [Create the Google Sheet](#3-create-the-google-sheet)
4. [Create Google OAuth credentials](#4-create-google-oauth-credentials)
5. [Import and configure the workflow](#5-import-and-configure-the-workflow)
6. [Test with one row](#6-test-with-one-row)

Commands are for PowerShell; on macOS/Linux use the same `docker` commands in your shell.

---

## 1. Start the report API

```powershell
git clone https://github.com/Erdem-Atik/QGIS-Property-Report.git
cd QGIS-Property-Report

# OpenStreetMap data for Türkiye (~620 MB, one time)
.\scripts\download_extract.ps1

# Settings: copy, then edit .env (Nominatim User-Agent, optional LLM key)
Copy-Item .env.example .env

# Build and start (first build downloads the QGIS image, ~3.7 GB)
docker compose up -d --build
```

Check it:

```powershell
Invoke-RestMethod http://localhost:8000/health
# status: ok  ·  osm_extract: ok = True
```

If `osm_extract` is not ok, see [OSM data (Geofabrik extract)](../README.md#osm-data-geofabrik-extract).

## 2. Start n8n

**If you do not run n8n yet**, start the one included in this project:

```powershell
docker compose --profile n8n up -d
```

Open <http://localhost:5678> and create the owner account (local login, stays on your machine).

- Port 5678 is taken? Set another port in `.env`, e.g. `N8N_PORT=5679`, and run the command again.
  Use that port everywhere below.
- Time zone for the schedule and the `processed_at` column: `TZ` in `.env`
  (default `Europe/Istanbul`).
- Workflows and credentials are stored in the Docker volume `n8n_data`; they survive restarts
  and `docker compose down` (not `docker compose down -v`).

**If you already run n8n** (version 2.x) in Docker, use it. The workflow calls the report API at
`http://host.docker.internal:8000`. Docker Desktop provides that name; on Linux, add
`--add-host=host.docker.internal:host-gateway` to your n8n container.

Check that n8n can reach the API (should print `{"status":"ok",...}`):

```powershell
# n8n from this project
docker compose exec n8n node -e "fetch('http://host.docker.internal:8000/health').then(r=>r.text()).then(console.log)"
# your own n8n container: replace my-n8n with its name (docker ps)
docker exec my-n8n node -e "fetch('http://host.docker.internal:8000/health').then(r=>r.text()).then(console.log)"
```

## 3. Create the Google Sheet

1. Create a new Google Sheet in the account that will run the workflow.
2. *File → Import → Upload* [`samples/sheet_template.csv`](../samples/sheet_template.csv),
   choose **Replace current sheet**. You get the header row and two example rows:

   `address | lat | lon | name | email | status | error_code | report_id | message | processed_at`

   Keep the header names exactly as they are; the workflow matches columns by name.
3. Rename the tab (bottom left) to **`Requests`**.
4. **Delete the two example rows** (or put your own email in them). Every row with an empty
   `status` is processed as soon as the workflow is active.
5. Copy the sheet's URL from the browser.

## 4. Create Google OAuth credentials

n8n needs permission to read and write the sheet and to send email from your Gmail account.
You create one OAuth client in Google Cloud and use it for two n8n credentials.

**4.1 Find the redirect URL in n8n.** In n8n: *Credentials → Create credential →
Google Sheets OAuth2 API*. Copy the **OAuth Redirect URL** shown there; with the default port
it is `http://localhost:5678/rest/oauth2-credential/callback`. Leave the dialog open.

**4.2 Google Cloud project.** In [Google Cloud Console](https://console.cloud.google.com/):

1. Create a project (top bar → project picker → *New project*), e.g. `property-report`.
2. *APIs & Services → Library*: enable **Google Sheets API**, **Google Drive API** and **Gmail API**.
3. *APIs & Services → OAuth consent screen* (in newer consoles: *Google Auth Platform*):
   - User type / audience: **External** (or **Internal** if you use Google Workspace)
   - App name and support email: anything you like
   - Under *Test users*, add the Google account that owns the sheet and sends the email
4. *Credentials → Create credentials → OAuth client ID*:
   - Application type: **Web application**
   - Authorised redirect URIs: paste the URL from step 4.1
   - Create, then copy the **Client ID** and **Client secret**

**4.3 Connect the credentials in n8n.**

1. Back in the Google Sheets dialog: paste Client ID and Client secret → *Sign in with Google* →
   choose your account and allow access. Google may show *"Google hasn't verified this app"*:
   this is your own app, so choose *Continue*. Save.
2. Create a second credential, **Gmail OAuth2 API**, with the same Client ID and secret and sign in
   the same way.

> **Important: tokens in "Testing" mode expire after 7 days.** While the consent screen is in
> *Testing*, Google stops the refresh token after a week and the workflow fails with an
> authentication error. Either reconnect both credentials when that happens, or set the app to
> *In production* on the consent screen (for your own account you just click through the
> "unverified app" warning). Workspace accounts with an **Internal** app are not affected.

## 5. Import and configure the workflow

1. In n8n: *Workflows → Create → ⋯ menu → Import from File…* → choose
   [`n8n/workflow.json`](../n8n/workflow.json).
2. Open the **Config** node: paste your sheet URL into `sheet_url`; `sheet_name` is `Requests`
   (change it if your tab has another name).
3. Select your **Google Sheets** credential in the nodes **Read Rows**, **Mark Processing** and
   **Write Result**, and your **Gmail** credential in **Send Email**.
4. Save, then **Publish** / activate the workflow (toggle in the top bar).

## 6. Test with one row

Add a row to the sheet, with your own email:

| address | lat | lon | name | email |
|---|---|---|---|---|
| | 36.2018 | 29.6377 | Kas Harbour Villa | you@example.com |

Within 1–2 minutes:

- the row's `status` changes to `processing`, then `sent`
- an email arrives with `Location Report - Kas Harbour Villa.pdf` attached

Then try an error, e.g. `Acropolis, Athens` in `address`: the row should end with
`status = error` and `error_code = outside_coverage`, and no email is sent.
The full list of error codes is in the [README](../README.md#report-api).

---

## Troubleshooting

Check one layer at a time, in this order; do not move on until the current one works.

| Symptom | Layer | What to check |
|---|---|---|
| Rows stay empty, nothing happens | n8n | Is the workflow published/active? *Executions* tab shows a run every minute? |
| `error_code = service_unreachable` | Connection | `docker compose ps`: is `qgis` running? Run the `fetch` check from step 2. |
| Execution fails in **Read Rows** | Authentication | Reconnect the Google Sheets credential (7-day token expiry, see step 4). |
| Execution fails in **Read Rows** with "not found" | Sheet | `sheet_url` and `sheet_name` in **Config**; the tab name must match exactly. |
| Rows are not picked up | Data | Header names match the template? `email` filled in and `status` empty? |
| `error_code = delivery_failed` | Authentication | Report was generated, email failed: reconnect the Gmail credential. |
| `error_code = extract_unavailable` | Deployment | OSM extract missing; `Invoke-RestMethod http://localhost:8000/health`. |
| `error_code = address_not_found` | Data | Not a deployment problem; use `lat` + `lon` (see README, *Google Sheet input*). |
| Redirect error when signing in with Google | Authentication | The redirect URI in Google Cloud must equal the one n8n shows, including the port. |

Logs:

```powershell
docker compose logs qgis --tail 50     # report API
docker compose logs n8n --tail 50      # n8n (if started from this project)
```

## Security notes

This setup is meant for one person's computer.

- The report API (port 8000) has no login: anyone who can reach the port can create reports and
  download them. Do not expose it to the internet.
- n8n stores your Google credentials. Keep its owner password strong, and back up the
  `n8n_data` volume if you rely on it.
- `.env` contains your LLM key; it is git-ignored, keep it that way.

## Updating and stopping

```powershell
git pull; docker compose up -d --build      # new code
.\scripts\download_extract.ps1 -Force        # newer OSM data (no restart needed)
docker compose --profile n8n down            # stop everything (data is kept)
```

## Running on a server

Not covered in detail; it has only been run locally. On a VPS the same `docker compose` setup
works in principle, but you additionally need HTTPS and a domain for n8n (the Google redirect URL
must use it: set `N8N_EDITOR_BASE_URL` and `WEBHOOK_URL`), and you must not publish port 8000.

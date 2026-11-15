# Property Location Report Generator

Turns an address or coordinate into a map-based PDF location report (nearby amenities,
straight-line distances, 500 m / 1 km radius analysis) using headless QGIS (PyQGIS)
and OpenStreetMap data.

**Coverage: Türkiye only.** Locations outside Türkiye are rejected with a clear error.

> Work in progress. Full documentation follows in a later phase.

## Report API

`docker compose up -d --build` starts the report service on port 8000 (FastAPI, `qgis/server.py`).
The n8n workflow (`n8n/workflow.json`) exposes the same contract on its webhook
(`POST /webhook/property-report`) and emails the PDF as an attachment (Gmail). It needs one
extra field, the recipient:

```json
{"address": "Kaleici, Antalya", "name": "Old Town Apartment", "email": "client@example.com"}
```

The email is checked before any work is done. The success response adds `email_sent_to`
and `email_message_id`. After importing the workflow, select a Gmail OAuth2 credential in
the **Send Email** node; the report is sent from that account.

```powershell
# By coordinates
Invoke-RestMethod -Method Post -Uri http://localhost:8000/generate -ContentType "application/json" `
  -Body '{"lat": 36.2018, "lon": 29.6377, "name": "Kas Harbour Villa"}'

# By address (geocoded with Nominatim; name is optional)
Invoke-RestMethod -Method Post -Uri http://localhost:8000/generate -ContentType "application/json" `
  -Body '{"address": "Kaleici, Antalya", "name": "Old Town Apartment"}'
```

Success (`200`) returns `job`, `pdf_url`, `summary_url` and the list of output files.
`GET /files/{job}/{name}` downloads a file; `GET /health` also checks that the OSM extract is present.

Every error has the same shape, so callers can branch on `error_code`:

```json
{"status": "error", "job": "20261115-...", "error_code": "outside_coverage",
 "error": "Location 37.98, 23.73 is outside Türkiye. Coverage: Türkiye only."}
```

| HTTP | `error_code` | Meaning |
|---|---|---|
| 422 | `invalid_input` | Missing/invalid fields, malformed JSON, both `address` and `lat`/`lon` given; n8n: missing or invalid `email` |
| 422 | `outside_coverage` | Location or address is outside Türkiye |
| 422 | `address_not_found` | Nominatim found no match for the address |
| 422 | `no_osm_data` | No OSM data around the point (e.g. at sea); never reported as "0 POIs" |
| 502 | `geocoding_unavailable` | Nominatim unreachable or returned an error (retry later) |
| 503 | `extract_unavailable` | OSM extract missing or unreadable (deployment problem) |
| 503 | `service_unreachable` | n8n only: the report service is not running |
| 502 | `delivery_failed` | n8n only: the report was generated but the email could not be sent; `pdf_url` is included |
| 504 | `timeout` | Report generation exceeded `REPORT_TIMEOUT_S` (default 540 s) |
| 500 | `processing_error` | Unexpected failure; the response includes `stderr_tail` for debugging |

4xx errors are input problems (fix the request); 5xx errors are service problems (retry or check the deployment).

## LLM settings (AI location summary)

The report includes a 3–4 sentence location summary written by an LLM. The model only
receives the analysis table (category counts, nearest straight-line distances and POI names
from `summary.json`) and is instructed not to add any information that is not in it.

Any OpenAI-compatible chat-completions API can be used. Configure it in `.env`
(copy `.env.example`):

| Variable | Example | Notes |
|---|---|---|
| `LLM_PROVIDER` | `groq` | Label; `groq` also provides defaults for the two settings below |
| `LLM_MODEL` | `openai/gpt-oss-120b` | Model id at the provider |
| `LLM_BASE_URL` | `https://api.groq.com/openai/v1` | OpenAI-compatible endpoint |
| `LLM_API_KEY` | – | Required; never commit it (`.env` is git-ignored) |

Only Groq has been tested so far. Behaviour:

- Temperature 0.2. HTTP 429 is retried up to 3 times with backoff (honours `Retry-After`).
- If the key is missing or the call fails, the PDF is still produced and the summary reads
  "AI summary unavailable."; the reason is written to `ai_summary.json`.
- `--no-summary` skips the LLM call entirely.

## Data sources and limitations

- Map data and basemap © OpenStreetMap contributors (ODbL).
- POIs are read from a local copy of the [Geofabrik Türkiye extract](https://download.geofabrik.de/europe/turkey.html)
  (`turkey-latest.osm.pbf`, ~620 MB). No third-party POI API is called, so POI lookup works offline.
  The extract is not part of the repository; download it once:

  ```powershell
  .\scripts\download_extract.ps1          # -> data\turkey-latest.osm.pbf (MD5-verified)
  ```

  `docker-compose.yml` mounts `./data` read-only into the container (`OSM_PBF_PATH`).
- Both OSM nodes and areas (closed ways / multipolygons, e.g. beaches, supermarket buildings)
  are used. Distances to areas are measured to their nearest edge.
- The OSM data date of the extract is shown in every report's source note.
  Bundled samples (`samples/poi/`, `samples/output/`) use OSM data of 2026-11-12.
- One POI lookup scans the whole extract: about 8–10 s per report.
- Results depend on OpenStreetMap tagging quality (e.g. a place tagged as a cafe is counted as a cafe).
- A location with no OSM data around it (outside the extract, or at sea) is reported as an error,
  never as "0 POIs".
- Distances are straight-line, not walking routes.

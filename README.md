# Property Location Report Generator

Send an address or a coordinate in Türkiye, get back a one-page PDF location report
by email: a map with 500 m / 1 km radius rings, nearby amenities by category,
straight-line distances to the nearest of each, and a short AI-written summary that sticks
to the numbers.

Built with **headless QGIS (PyQGIS)**, **OpenStreetMap** data, **FastAPI** and **n8n**,
all running locally in Docker.

**Coverage: Türkiye only.** Locations outside Türkiye are rejected with a clear error.

![Sample report: Kas Harbour Villa](docs/images/kas_report.png)

Sample PDFs: [Kaş (coastal town)](samples/output/kas.pdf) ·
[Kaleiçi, Antalya (city centre)](samples/output/kaleici.pdf) ·
[Elmalı (rural)](samples/output/elmali.pdf)

## Who it is for

Short-term rental hosts, property managers, real-estate agencies and investors who need a
quick, consistent answer to "what is around this property?" for a listing, a client or an
investment memo, without opening a GIS application.

## What the report contains

- **Map:** OpenStreetMap basemap, the property, 500 m and 1 km radius rings, and amenities
  coloured by category, with legend, scale bar and north arrow.
- **Distance table:** for beach, supermarket, restaurant, cafe, pharmacy and bus stop, the
  number within 500 m and within 1 km, and the straight-line distance to the nearest one.
- **Location summary:** 3–4 sentences written by an LLM **from the table only**. The model gets
  nothing else, so it cannot invent facts, and the PDF labels the text as AI-generated.
- **Source note:** "© OpenStreetMap contributors" and the date of the OSM data used.

Each run also writes `analysis.gpkg` (all layers, for QGIS), `summary.json` (the table as data)
and `pois.geojson`.

## How it works

```mermaid
flowchart LR
    A["Client<br/>POST location + email"] --> B["n8n webhook"]
    B -->|"POST /generate"| C["Report API<br/>(FastAPI, Docker)"]
    C --> D["Nominatim<br/>(address → coordinate)"]
    C --> E["Geofabrik Türkiye extract<br/>(local .osm.pbf, GDAL)"]
    C --> F["PyQGIS<br/>buffers · distances · layout → PDF"]
    F --> G["LLM API<br/>summary from table"]
    C -->|"PDF + JSON"| B
    B --> H["Gmail<br/>PDF as attachment"]
```

1. **Input:** an address (geocoded with Nominatim) or a latitude/longitude, plus a recipient email.
2. **Coverage check:** anything outside Türkiye stops here with a clear error.
3. **POIs:** read from a local copy of the Geofabrik Türkiye extract with GDAL's OSM driver.
   Both points and areas are used; an area (a beach, a supermarket building) stays a polygon
   and its distance is measured to the nearest edge.
4. **Analysis (PyQGIS):** the property is projected to its local **UTM zone** (chosen
   automatically from the coordinate) so that buffers and distances are in metres, never
   in degrees. Amenities are counted per ring and the nearest one per category is found.
5. **Layout:** layer styles (`.qml`) and a print layout template (`.qpt`) produce an A4 PDF.
6. **Summary:** the table is sent to an OpenAI-compatible LLM; if that fails the PDF is still produced.
7. **Delivery:** n8n emails the PDF as an attachment and returns a JSON result to the caller.

## Tech stack

| Layer | Choice |
|---|---|
| GIS engine | QGIS 3.44 / PyQGIS, headless (`QT_QPA_PLATFORM=offscreen`) |
| Container | Based on the official `qgis/qgis` Docker image (includes GDAL with the OSM driver) |
| POI data | Geofabrik Türkiye extract (`turkey-latest.osm.pbf`), read locally |
| Geocoding | Nominatim (max 1 request/s, cached, identifying User-Agent) |
| Report API | FastAPI + uvicorn |
| Orchestration | n8n (webhook, HTTP request, Gmail) |
| AI summary | Any OpenAI-compatible chat API (tested with Groq) |
| Output | PDF, GeoPackage, JSON |

## Quick start

Requirements: Windows/macOS/Linux with Docker Desktop, about 5 GB of free disk space
(Docker image ~3.7 GB, OSM extract ~0.65 GB).
Commands below are for PowerShell.

```powershell
# 1. Download the OSM extract once (~620 MB, MD5-verified) -> data\turkey-latest.osm.pbf
.\scripts\download_extract.ps1

# 2. Settings: Nominatim User-Agent and LLM key (optional; without a key the PDF says
#    "AI summary unavailable.")
Copy-Item .env.example .env

# 3. Build and start the report API on http://localhost:8000
docker compose up -d --build
Invoke-RestMethod http://localhost:8000/health

# 4. Generate a report
Invoke-RestMethod -Method Post -Uri http://localhost:8000/generate -ContentType "application/json" `
  -Body '{"lat": 36.2018, "lon": 29.6377, "name": "Kas Harbour Villa"}'
```

Files are written to `out\<job>\`. The same pipeline runs from the command line:

```powershell
docker compose run --rm qgis python3 generate_report.py --address "Kaleici, Antalya" --name "Old Town Apartment" --out /out/kaleici
```

**n8n (email delivery):** import `n8n/workflow.json`, select a Gmail OAuth2 credential in the
**Send Email** node, and publish. n8n reaches the API at `http://host.docker.internal:8000`.

## Report API

`POST /generate` takes either an address or a coordinate; `name` is optional.

```powershell
# By coordinates
Invoke-RestMethod -Method Post -Uri http://localhost:8000/generate -ContentType "application/json" `
  -Body '{"lat": 36.2018, "lon": 29.6377, "name": "Kas Harbour Villa"}'

# By address (geocoded with Nominatim)
Invoke-RestMethod -Method Post -Uri http://localhost:8000/generate -ContentType "application/json" `
  -Body '{"address": "Kaleici, Antalya", "name": "Old Town Apartment"}'
```

Success (`200`) returns `job`, `pdf_url`, `summary_url` and the list of output files.
`GET /files/{job}/{name}` downloads a file; `GET /health` also checks that the OSM extract is present.

The n8n webhook (`POST /webhook/property-report`) takes the same fields plus the recipient:

```json
{"address": "Kaleici, Antalya", "name": "Old Town Apartment", "email": "client@example.com"}
```

The email is checked before any work is done. The success response adds `email_sent_to`
and `email_message_id`. The report is sent from the Gmail account of the selected credential.

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
| 502 | `delivery_failed` | n8n only: the report was generated but the email could not be sent; `pdf_url` is included |
| 503 | `extract_unavailable` | OSM extract missing or unreadable (deployment problem) |
| 503 | `service_unreachable` | n8n only: the report API is not running |
| 504 | `timeout` | Report generation exceeded `REPORT_TIMEOUT_S` (default 540 s) |
| 500 | `processing_error` | Unexpected failure; the response includes `stderr_tail` for debugging |

4xx errors are input problems (fix the request); 5xx errors are service problems (retry or check the deployment).

## LLM settings (AI location summary)

The model only receives the analysis table (category counts, nearest straight-line distances
and POI names from `summary.json`) and is instructed not to add any information that is not in it.

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
- POIs are read from a local copy of the [Geofabrik Türkiye extract](https://download.geofabrik.de/europe/turkey.html).
  No third-party POI API is called, so POI lookup works offline. The extract is not part of the
  repository; `scripts/download_extract.ps1` downloads it and `docker-compose.yml` mounts `./data`
  read-only into the container (`OSM_PBF_PATH`).
- The OSM data date of the extract is shown in every report's source note.
  Bundled samples (`samples/poi/`, `samples/output/`) use OSM data of 2026-11-12.
  Re-run the download script to refresh the data.
- One POI lookup scans the whole extract: about 8–10 s; a full report takes about 15–20 s
  including the LLM call.
- Results depend on OpenStreetMap tagging quality: a place is counted in the category it is
  tagged with in OSM, and missing places are missing from the report.
- A location with no OSM data around it (outside the extract, or at sea) is reported as an error,
  never as "0 POIs".
- Distances are straight-line, not walking routes.

## Project structure

```
qgis/
  generate_report.py     # CLI: location -> PDF + GeoPackage + JSON
  server.py              # Report API (FastAPI)
  report/
    poi_provider.py      # POIs from the local OSM extract (GDAL OSM driver)
    osm.py               # Nominatim geocoding (cache, rate limit, coverage check)
    crs.py               # UTM zone selection, Türkiye check
    analysis.py          # buffers, counts, nearest distances (metric CRS)
    render.py            # QGIS project, layout, PDF export
    llm.py               # AI summary from summary.json
  styles/  templates/    # .qml layer styles, .qpt print layout
  tests/                 # unit tests (small OSM fixture; LLM client mocked)
n8n/workflow.json        # webhook -> report API -> Gmail
scripts/download_extract.ps1
samples/                 # sample POI files and PDFs (no extract needed to view them)
```

## Tests

```powershell
docker compose run --rm qgis python3 -m unittest discover -s tests -v
```

POI tests read a small hand-made OSM file through the real GDAL driver; tests against the
real extract are skipped when it is not present. LLM tests mock the API client (no network).

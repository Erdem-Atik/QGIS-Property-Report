# Property Location Report Generator

Turns an address or coordinate into a map-based PDF location report (nearby amenities,
straight-line distances, 500 m / 1 km radius analysis) using headless QGIS (PyQGIS)
and OpenStreetMap data.

**Coverage: Türkiye only.** Locations outside Türkiye are rejected with a clear error.

> Work in progress. Full documentation follows in a later phase.

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

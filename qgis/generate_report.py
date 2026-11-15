"""Generate a property location report (PDF + GeoPackage + summary JSON).

Coverage: Türkiye only. POIs are read from a local Geofabrik extract ($OSM_PBF_PATH).

Examples (inside the container):
    # Coordinates -> POIs from the local extract -> PDF
    python3 generate_report.py --lat 36.887 --lon 30.704 --name "Villa Demo" --out /out/demo

    # Address -> Nominatim geocode -> POIs from the local extract -> PDF
    python3 generate_report.py --address "Kaleici, Antalya, Turkey" --name "Old Town Apartment" --out /out/kaleici

    # Coordinates + a prepared POI file (no extract needed)
    python3 generate_report.py --lat 36.2018 --lon 29.6377 --name "Kas Harbour Villa" \
        --poi /samples/poi/kas.geojson --out /out/kas

Exit codes: 0 ok, 1 processing error (incl. missing/unreadable extract),
2 invalid input (incl. outside Türkiye, address not found, no OSM data at the location),
3 external service error (geocoding).

On failure the error is also written to <out>/error.json as
{"error_code": "...", "error": "...", "exit_code": n}; see ERROR_EXIT_CODES.
"""
import argparse
import json
import logging
import os
import sys
import time


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Property location report generator (PyQGIS).")
    loc = p.add_argument_group("location (use --address OR --lat/--lon)")
    loc.add_argument("--address", help="Free-text address, geocoded with Nominatim")
    loc.add_argument("--lat", type=float, help="Latitude (WGS84)")
    loc.add_argument("--lon", type=float, help="Longitude (WGS84)")
    p.add_argument("--name", help="Report title (default: address or coordinates)")
    p.add_argument("--out", required=True, help="Output directory")
    p.add_argument("--poi", help="Prepared POI GeoJSON ('name' and 'category' properties). "
                                 "If omitted, POIs are read from the local OSM extract "
                                 "($OSM_PBF_PATH).")
    p.add_argument("--no-basemap", action="store_true", help="Skip the OSM basemap (offline)")
    p.add_argument("--no-summary", action="store_true",
                   help="Skip the AI location summary (no LLM call)")
    p.add_argument("--dpi", type=int, default=200, help="PDF raster resolution (default 200)")
    args = p.parse_args(argv)
    has_coords = args.lat is not None or args.lon is not None
    if args.address and has_coords:
        p.error("use either --address or --lat/--lon, not both")
    if not args.address and (args.lat is None or args.lon is None):
        p.error("--address or both --lat and --lon are required")
    return args


# Machine-readable error codes (used by server.py / n8n) and their process exit codes.
ERROR_EXIT_CODES = {
    "invalid_input": 2,           # bad coordinates, missing POI file
    "outside_coverage": 2,        # location or address outside Türkiye
    "address_not_found": 2,       # Nominatim found nothing
    "no_osm_data": 2,             # no OSM data around the point (outside extract / at sea)
    "geocoding_unavailable": 3,   # Nominatim unreachable or returned an error
    "extract_unavailable": 1,     # OSM extract missing or unreadable
    "processing_error": 1,        # anything else during analysis / rendering
}


def fail(out_dir: str, error_code: str, msg: str) -> int:
    """Print the error, write <out_dir>/error.json and return the exit code."""
    exit_code = ERROR_EXIT_CODES[error_code]
    print(f"ERROR: {msg}", file=sys.stderr)
    try:
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "error.json"), "w", encoding="utf-8") as fh:
            json.dump({"error_code": error_code, "error": msg, "exit_code": exit_code},
                      fh, indent=2, ensure_ascii=False)
    except OSError as exc:  # the exit code still tells the caller what happened
        print(f"WARNING: could not write error.json: {exc}", file=sys.stderr)
    return exit_code


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(levelname)s %(name)s: %(message)s")
    error_json = os.path.join(args.out, "error.json")
    if os.path.exists(error_json):
        os.remove(error_json)  # never leave a stale error from an earlier run

    from report import llm, osm, poi_provider

    # 1. Location
    lat, lon, address = args.lat, args.lon, None
    if args.address:
        try:
            hit = osm.geocode(args.address)
        except osm.OutsideCoverageError as exc:
            return fail(args.out, "outside_coverage", str(exc))
        except osm.AddressNotFoundError as exc:
            return fail(args.out, "address_not_found", str(exc))
        except osm.OsmError as exc:
            return fail(args.out, "geocoding_unavailable", str(exc))
        lat, lon, address = hit["lat"], hit["lon"], hit["display_name"]
        print(f"Geocoded: {address} -> {lat:.5f}, {lon:.5f}")
    name = args.name or args.address or f"{lat:.5f}, {lon:.5f}"

    # Import PyQGIS lazily so --help and input errors are fast.
    from qgis.core import QgsProject
    from report.app import qgis_app
    from report.analysis import run_analysis, write_geopackage
    from report.crs import validate_lat_lon, validate_turkey
    from report.render import build_layout, build_project, export_pdf

    try:
        validate_lat_lon(lat, lon)
    except ValueError as exc:
        return fail(args.out, "invalid_input", str(exc))
    try:
        validate_turkey(lat, lon)
    except ValueError as exc:
        return fail(args.out, "outside_coverage", str(exc))

    os.makedirs(args.out, exist_ok=True)
    gpkg = os.path.join(args.out, "analysis.gpkg")
    pdf = os.path.join(args.out, "report.pdf")
    summary_path = os.path.join(args.out, "summary.json")
    ai_path = os.path.join(args.out, "ai_summary.json")
    if os.path.exists(ai_path):
        os.remove(ai_path)  # never leave a stale summary from an earlier run

    # 2. POIs: prepared file or the local OSM extract
    poi_meta, poi_source = {}, None
    if args.poi:
        if not os.path.isfile(args.poi):
            return fail(args.out, "invalid_input", f"POI file not found: {args.poi}")
        poi_path = args.poi
        try:  # bundled samples carry the extract metadata; keep the date in the source note
            with open(poi_path, encoding="utf-8") as fh:
                poi_meta = json.load(fh).get("metadata") or {}
        except (ValueError, AttributeError):
            poi_meta = {}
        if poi_meta.get("extract_date"):
            poi_source = f"OSM Geofabrik extract ({poi_meta['extract_date'][:10]})"
    else:
        try:
            fc = poi_provider.fetch_pois(lat, lon)
        except poi_provider.NoOsmDataError as exc:
            return fail(args.out, "no_osm_data", str(exc))
        except poi_provider.ExtractUnavailableError as exc:
            return fail(args.out, "extract_unavailable", str(exc))
        except poi_provider.PoiProviderError as exc:
            return fail(args.out, "processing_error", str(exc))
        poi_meta = fc.pop("metadata")
        date = (poi_meta["extract_date"] or "date unknown")[:10]
        poi_source = f"OSM Geofabrik extract ({date})"
        poi_path = os.path.join(args.out, "pois.geojson")
        with open(poi_path, "w", encoding="utf-8") as fh:
            json.dump(fc, fh, ensure_ascii=False)
        print(f"Loaded {len(fc['features'])} POIs from the local extract in "
              f"{poi_meta['seconds']} s (radius {poi_meta['radius_m']} m, "
              f"OSM data {poi_meta['extract_date']})")

    def run() -> dict:
        # Kept in its own scope so every QGIS object is released before exitQgis().
        result = run_analysis(lat, lon, name, poi_path, poi_source=poi_source,
                              poi_meta=poi_meta, address=address)
        uris = write_geopackage(result, gpkg)
        summary = result.summary()
        with open(summary_path, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=2, ensure_ascii=False)

        # AI summary: based only on summary.json; a failure never blocks the PDF.
        summary_text = None
        if not args.no_summary:
            ai = llm.try_summarize(summary)
            with open(ai_path, "w", encoding="utf-8") as fh:
                json.dump(ai, fh, indent=2, ensure_ascii=False)
            summary_text = ai["text"]
            summary["_ai_status"] = ai["status"]

        layers = build_project(result, uris, basemap=not args.no_basemap)
        layout = build_layout(result, layers, summary_text=summary_text)
        export_pdf(layout, pdf, dpi=args.dpi)
        return summary

    # 3. Analysis + map
    t0 = time.time()
    with qgis_app():
        try:
            summary = run()
        except Exception as exc:  # report and exit non-zero for automation (n8n)
            return fail(args.out, "processing_error", str(exc))
        finally:
            QgsProject.instance().clear()

    print(f"UTM CRS:  EPSG:{summary['utm_epsg']}")
    for s in summary["categories"].values():
        print(f"  {s['label']:<12} 500m={s['count_500']:<3} 1km={s['count_1000']:<3} "
              f"nearest={s['nearest_m']} m ({s['nearest_name']})")
    if "_ai_status" in summary:
        print(f"AI summary: {summary['_ai_status']} (see {ai_path})")
    print(f"Wrote {pdf}\n      {gpkg}\n      {summary_path}  ({time.time() - t0:.1f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

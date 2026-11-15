"""Local POI source: read points of interest from a Geofabrik .osm.pbf extract.

Per report, the extract is scanned once with GDAL's OSM driver (the same engine as
`ogr2ogr ... points multipolygons -spat xmin ymin xmax ymax`):

- `points` layer: tagged nodes (restaurants, bus stops, ...).
- `multipolygons` layer: closed ways / multipolygon relations (beaches, many shops).
  Polygons are kept as polygons; analysis.py measures the distance to their edge.

A degree bbox is only used as a fast prefilter. The real "within radius" test is
metric, in the local UTM zone. Tags live partly in dedicated columns and partly in
the hstore-like `other_tags` column; both are merged before classification.

Plain GDAL/OGR (no PyQGIS), so it also runs outside a QGIS session:
    python3 -m report.poi_provider --lat 36.887 --lon 30.704 --out /out/pois.geojson
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import struct
import sys
import time
import zlib
from datetime import datetime, timezone

from osgeo import gdal, ogr, osr

gdal.UseExceptions()
ogr.UseExceptions()
osr.UseExceptions()

OSM_PBF_PATH = os.environ.get("OSM_PBF_PATH", "/data/turkey-latest.osm.pbf")
SEARCH_RADIUS_M = 1500  # a bit beyond the 1 km buffer so "nearest" is meaningful
LAYERS = ("points", "multipolygons")
# Columns of the OSM driver that are not OSM tags.
_NON_TAG_FIELDS = {"osm_id", "osm_way_id", "other_tags"}


class PoiProviderError(RuntimeError):
    """Reading POIs from the extract failed."""


class ExtractUnavailableError(PoiProviderError):
    """The extract file is missing or cannot be opened (a deployment problem)."""


class NoOsmDataError(PoiProviderError):
    """No OSM data at all around the location (outside the extract, or at sea)."""


# ---------------------------------------------------------------- tags/categories
_OTHER_TAGS = re.compile(r'"((?:[^"\\]|\\.)*)"=>"((?:[^"\\]|\\.)*)"')


def parse_other_tags(text: str | None) -> dict:
    """Parse GDAL's other_tags string: '"k"=>"v","k2"=>"v2"' (quotes escaped as \\")."""
    if not text:
        return {}
    unescape = lambda s: s.replace('\\"', '"').replace("\\\\", "\\")  # noqa: E731
    return {unescape(k): unescape(v) for k, v in _OTHER_TAGS.findall(text)}


def feature_tags(feat: ogr.Feature) -> dict:
    """All OSM tags of a feature: dedicated columns + other_tags."""
    tags = parse_other_tags(feat["other_tags"]) if feat.GetFieldIndex("other_tags") >= 0 else {}
    defn = feat.GetDefnRef()
    for i in range(defn.GetFieldCount()):
        name = defn.GetFieldDefn(i).GetName()
        if name not in _NON_TAG_FIELDS and feat.IsFieldSetAndNotNull(i):
            tags[name] = feat.GetFieldAsString(i)
    return tags


def categorize(tags: dict) -> str | None:
    """Map OSM tags to a report category (None = not relevant for the report)."""
    if tags.get("natural") == "beach":
        return "beach"
    if tags.get("shop") == "supermarket":
        return "supermarket"
    if tags.get("amenity") in ("restaurant", "cafe", "pharmacy"):
        return tags["amenity"]
    if tags.get("highway") == "bus_stop" or (
            tags.get("public_transport") == "platform" and tags.get("bus") == "yes"):
        return "bus_stop"
    return None


def _osm_id(layer: str, feat: ogr.Feature) -> str:
    if layer == "points":
        return f"node/{feat['osm_id']}"
    way_id = feat["osm_way_id"]  # closed way -> osm_way_id; relation -> osm_id
    return f"way/{way_id}" if way_id else f"relation/{feat['osm_id']}"


# ----------------------------------------------------------------- extract date
def _varint(buf: bytes, i: int) -> tuple[int, int]:
    shift = result = 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7


def _fields(buf: bytes):
    """Yield (field_number, value) of a protobuf message (varint and bytes fields)."""
    i = 0
    while i < len(buf):
        key, i = _varint(buf, i)
        num, wire = key >> 3, key & 7
        if wire == 0:
            val, i = _varint(buf, i)
        elif wire == 2:
            size, i = _varint(buf, i)
            val, i = buf[i:i + size], i + size
        elif wire == 1:
            val, i = buf[i:i + 8], i + 8
        elif wire == 5:
            val, i = buf[i:i + 4], i + 4
        else:
            raise ValueError(f"unsupported protobuf wire type {wire}")
        yield num, val


def pbf_replication_timestamp(pbf: str) -> str | None:
    """Read osmosis_replication_timestamp from the PBF header (set by Geofabrik)."""
    with open(pbf, "rb") as fh:
        (hdr_len,) = struct.unpack(">I", fh.read(4))
        header = dict(_fields(fh.read(hdr_len)))
        if header.get(1) != b"OSMHeader":
            return None
        blob = dict(_fields(fh.read(header[3])))
    if 3 in blob:
        data = zlib.decompress(blob[3])
    elif 1 in blob:
        data = blob[1]
    else:
        return None  # other compressions (lzma, zstd) are not used by Geofabrik
    ts = dict(_fields(data)).get(32)
    if not ts:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def extract_date(pbf: str) -> str | None:
    """OSM data date of the extract: PBF header, else the <pbf>.timestamp sidecar.

    The header holds the OSM replication time of the data; the sidecar (written by
    scripts/download_extract.ps1) holds the file's publication time on Geofabrik.
    Returns None if unknown: the date is never guessed.
    """
    try:
        ts = pbf_replication_timestamp(pbf)
    except (OSError, ValueError, KeyError, IndexError, struct.error, zlib.error):
        ts = None
    if ts:
        return ts
    sidecar = pbf + ".timestamp"
    if os.path.isfile(sidecar):
        with open(sidecar, encoding="utf-8") as fh:
            return fh.read().strip() or None
    return None


# ------------------------------------------------------------------------ query
def _utm(lat: float, lon: float) -> osr.SpatialReference:
    zone = min(int(math.floor((lon + 180.0) / 6.0)) + 1, 60)
    srs = osr.SpatialReference()
    srs.ImportFromEPSG((32600 if lat >= 0 else 32700) + zone)
    srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return srs


def bbox(lat: float, lon: float, radius_m: float) -> tuple[float, float, float, float]:
    """Degree bbox (lon_min, lat_min, lon_max, lat_max) that safely contains the circle.

    Only a prefilter for the OSM driver; distances are measured in UTM.
    """
    dlat = radius_m / 110_574.0 * 1.1
    dlon = radius_m / (111_320.0 * max(math.cos(math.radians(lat)), 0.01)) * 1.1
    return lon - dlon, lat - dlat, lon + dlon, lat + dlat


def fetch_pois(lat: float, lon: float, radius_m: int = SEARCH_RADIUS_M,
               pbf: str | None = None) -> dict:
    """POIs within radius_m of (lat, lon) as a GeoJSON FeatureCollection + 'metadata'.

    Feature properties: name, category, osm_id (the format generate_report.py expects).
    Raises NoOsmDataError if there is no OSM data at all around the point, so that an
    empty result is never presented as "0 POIs".
    """
    pbf = pbf or OSM_PBF_PATH
    if not os.path.isfile(pbf):
        raise ExtractUnavailableError(
            f"OSM extract not found: {pbf}. Download it with scripts/download_extract.ps1 "
            "and set OSM_PBF_PATH.")
    t0 = time.time()
    try:
        ds = gdal.OpenEx(pbf, gdal.OF_VECTOR, allowed_drivers=["OSM"])
    except RuntimeError as exc:
        raise ExtractUnavailableError(f"Cannot read OSM extract {pbf}: {exc}") from exc
    # Only parse the layers we need (same mechanism ogr2ogr uses).
    ds.ExecuteSQL(f"SET interest_layers = {','.join(LAYERS)}")
    box = bbox(lat, lon, radius_m)
    for name in LAYERS:
        ds.GetLayerByName(name).SetSpatialFilterRect(*box)

    wgs84 = osr.SpatialReference()
    wgs84.ImportFromEPSG(4326)
    wgs84.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    to_utm = osr.CoordinateTransformation(wgs84, _utm(lat, lon))
    origin = ogr.Geometry(ogr.wkbPoint)
    origin.AddPoint_2D(lon, lat)
    origin.Transform(to_utm)

    features, seen, raw, skipped = [], set(), 0, 0
    gdal.PushErrorHandler("CPLQuietErrorHandler")  # e.g. "Non closed ring" warnings
    try:
        while True:  # the OSM driver must be read interleaved across layers
            feat, lyr = ds.GetNextFeature()
            if feat is None:
                break
            geom = feat.GetGeometryRef()
            if geom is None or geom.IsEmpty():
                continue
            tags = feature_tags(feat)
            try:
                gu = geom.Clone()
                gu.Transform(to_utm)
                dist = gu.Distance(origin)
            except RuntimeError:
                skipped += 1  # broken OSM geometry, e.g. a ring with 2 points
                continue
            if dist > radius_m:
                continue
            if "boundary" not in tags:  # admin/maritime areas also cover the sea
                raw += 1
            cat = categorize(tags)
            if cat is None:
                continue
            osm_id = _osm_id(lyr.GetName(), feat)
            if osm_id in seen:
                continue
            seen.add(osm_id)
            features.append({
                "type": "Feature",
                "properties": {"name": tags.get("name:en") or tags.get("name") or "",
                               "category": cat, "osm_id": osm_id},
                "geometry": json.loads(geom.ExportToJson()),
            })
    except RuntimeError as exc:
        raise PoiProviderError(f"Error while reading {pbf}: {exc}") from exc
    finally:
        gdal.PopErrorHandler()
        ds = None

    date = extract_date(pbf)
    if raw == 0:
        raise NoOsmDataError(
            f"No OpenStreetMap data within {radius_m} m of {lat:.5f}, {lon:.5f} "
            f"(outside the Türkiye extract, or at sea). Coverage: Türkiye only.")
    return {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {
            "source": "OpenStreetMap - Geofabrik Türkiye extract",
            "extract_file": os.path.basename(pbf),
            "extract_date": date,
            "radius_m": radius_m,
            "osm_features_in_radius": raw,
            "invalid_geometries_skipped": skipped,
            "seconds": round(time.time() - t0, 1),
        },
    }


# -------------------------------------------------------------------------- CLI
def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Extract report POIs from a local .osm.pbf.")
    p.add_argument("--lat", type=float, required=True)
    p.add_argument("--lon", type=float, required=True)
    p.add_argument("--radius", type=int, default=SEARCH_RADIUS_M, help="metres")
    p.add_argument("--pbf", default=None, help=f"default: $OSM_PBF_PATH ({OSM_PBF_PATH})")
    p.add_argument("--out", required=True, help="Output GeoJSON")
    args = p.parse_args(argv)
    try:
        fc = fetch_pois(args.lat, args.lon, args.radius, args.pbf)
    except NoOsmDataError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except PoiProviderError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(fc, fh, ensure_ascii=False, indent=1)
    meta = fc["metadata"]
    counts = {}
    for f in fc["features"]:
        counts[f["properties"]["category"]] = counts.get(f["properties"]["category"], 0) + 1
    print(f"{len(fc['features'])} POIs {counts} in {meta['seconds']} s "
          f"(extract {meta['extract_date']}) -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

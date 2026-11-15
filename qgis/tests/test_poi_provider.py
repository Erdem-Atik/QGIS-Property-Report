"""Tests for the local POI provider (no mocks).

Unit tests use a tiny hand-made OSM file (tests/fixtures/mini.osm); GDAL's OSM driver
reads .osm XML and .osm.pbf through the same code path. Live tests run against the real
extract and are skipped when $OSM_PBF_PATH does not exist.

Run: docker compose run --rm qgis python3 -m unittest discover -s tests -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report import poi_provider  # noqa: E402
from report.crs import validate_turkey  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "mini.osm")
LAT, LON = 36.9000, 30.7000


class ParseTagsTest(unittest.TestCase):
    def test_other_tags(self):
        tags = poi_provider.parse_other_tags('"amenity"=>"cafe","name:en"=>"Blue Cafe"')
        self.assertEqual(tags, {"amenity": "cafe", "name:en": "Blue Cafe"})

    def test_escaped_quotes_and_separators(self):
        tags = poi_provider.parse_other_tags(r'"name"=>"Cafe \"Best\", Kas","shop"=>"x=>y"')
        self.assertEqual(tags, {"name": 'Cafe "Best", Kas', "shop": "x=>y"})

    def test_empty(self):
        self.assertEqual(poi_provider.parse_other_tags(None), {})
        self.assertEqual(poi_provider.parse_other_tags(""), {})

    def test_categorize(self):
        c = poi_provider.categorize
        self.assertEqual(c({"natural": "beach"}), "beach")
        self.assertEqual(c({"shop": "supermarket"}), "supermarket")
        self.assertEqual(c({"amenity": "pharmacy"}), "pharmacy")
        self.assertEqual(c({"highway": "bus_stop"}), "bus_stop")
        self.assertEqual(c({"public_transport": "platform", "bus": "yes"}), "bus_stop")
        self.assertIsNone(c({"public_transport": "platform"}))
        self.assertIsNone(c({"amenity": "bench"}))


class FixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fc = poi_provider.fetch_pois(LAT, LON, 1500, pbf=FIXTURE)
        cls.by_id = {f["properties"]["osm_id"]: f for f in cls.fc["features"]}

    def test_categories_and_ids(self):
        got = {i: f["properties"]["category"] for i, f in self.by_id.items()}
        self.assertEqual(got, {
            "node/1": "restaurant",
            "node/2": "bus_stop",
            "node/5": "bus_stop",
            "way/100": "beach",
            "way/101": "supermarket",
        })

    def test_metric_radius_filter(self):
        # node/4 is inside the degree bbox but ~2 km away
        self.assertNotIn("node/4", self.by_id)

    def test_polygons_are_kept(self):
        self.assertIn(self.by_id["way/100"]["geometry"]["type"], ("Polygon", "MultiPolygon"))
        self.assertEqual(self.by_id["node/1"]["geometry"]["type"], "Point")

    def test_name_prefers_english(self):
        self.assertEqual(self.by_id["node/1"]["properties"]["name"], "Test Restaurant")
        self.assertEqual(self.by_id["way/101"]["properties"]["name"], "Test Market")

    def test_metadata(self):
        meta = self.fc["metadata"]
        self.assertEqual(meta["radius_m"], 1500)
        self.assertGreaterEqual(meta["osm_features_in_radius"], 6)  # incl. the bench
        self.assertIsNone(meta["extract_date"])  # XML fixture: no header, no sidecar

    def test_small_radius(self):
        fc = poi_provider.fetch_pois(LAT, LON, 120, pbf=FIXTURE)
        ids = {f["properties"]["osm_id"] for f in fc["features"]}
        self.assertEqual(ids, {"node/1"})

    def test_no_data_is_an_error_not_zero_pois(self):
        with self.assertRaises(poi_provider.NoOsmDataError):
            poi_provider.fetch_pois(36.5, 31.5, 1500, pbf=FIXTURE)

    def test_missing_file(self):
        with self.assertRaises(poi_provider.PoiProviderError):
            poi_provider.fetch_pois(LAT, LON, 1500, pbf="/nonexistent/turkey.osm.pbf")


class TurkeyBoundsTest(unittest.TestCase):
    def test_inside(self):
        validate_turkey(36.887, 30.704)   # Antalya
        validate_turkey(41.0082, 28.9784)  # Istanbul

    def test_outside(self):
        for lat, lon in ((37.98, 23.73), (52.52, 13.40), (33.89, 35.50)):  # Athens, Berlin, Beirut
            with self.assertRaises(ValueError):
                validate_turkey(lat, lon)


@unittest.skipUnless(os.path.isfile(poi_provider.OSM_PBF_PATH),
                     f"live extract not found: {poi_provider.OSM_PBF_PATH}")
class LiveExtractTest(unittest.TestCase):
    def test_antalya_centre(self):
        fc = poi_provider.fetch_pois(36.887, 30.704)
        cats = {f["properties"]["category"] for f in fc["features"]}
        self.assertGreater(len(fc["features"]), 0)
        self.assertGreaterEqual(len(cats), 4)
        self.assertRegex(fc["metadata"]["extract_date"] or "", r"^\d{4}-\d{2}-\d{2}")

    def test_sea_has_no_data(self):
        with self.assertRaises(poi_provider.NoOsmDataError):
            poi_provider.fetch_pois(36.8, 30.7)


if __name__ == "__main__":
    unittest.main()

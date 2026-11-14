"""Tests for the error contract between generate_report.py and server.py (no mocks).

Run: docker compose run --rm qgis python3 -m unittest discover -s tests -v
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import generate_report  # noqa: E402
import server  # noqa: E402
from pydantic import ValidationError  # noqa: E402


class ErrorContractTest(unittest.TestCase):
    def test_every_generator_error_has_an_http_status(self):
        self.assertTrue(set(generate_report.ERROR_EXIT_CODES) <= set(server.HTTP_STATUS))

    def test_input_errors_are_4xx_and_system_errors_5xx(self):
        for code, exit_code in generate_report.ERROR_EXIT_CODES.items():
            status = server.HTTP_STATUS[code]
            if exit_code == 2:
                self.assertEqual(status, 422, code)
            else:
                self.assertGreaterEqual(status, 500, code)

    def test_fail_writes_error_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "job")
            rc = generate_report.fail(out, "address_not_found", "Address not found: 'x'")
            self.assertEqual(rc, 2)
            with open(os.path.join(out, "error.json"), encoding="utf-8") as fh:
                info = json.load(fh)
            self.assertEqual(server.classify(rc, info),
                             ("address_not_found", "Address not found: 'x'"))


class ClassifyTest(unittest.TestCase):
    def test_uses_error_json(self):
        self.assertEqual(server.classify(1, {"error_code": "extract_unavailable", "error": "m"}),
                         ("extract_unavailable", "m"))

    def test_falls_back_to_exit_code(self):
        self.assertEqual(server.classify(2, None)[0], "invalid_input")
        self.assertEqual(server.classify(3, None)[0], "geocoding_unavailable")
        self.assertEqual(server.classify(1, None)[0], "processing_error")
        self.assertEqual(server.classify(-11, None)[0], "processing_error")  # crash

    def test_unknown_code_is_not_trusted(self):
        self.assertEqual(server.classify(2, {"error_code": "bogus"})[0], "invalid_input")


class RequestValidationTest(unittest.TestCase):
    def test_coordinates(self):
        req = server.GenerateRequest(lat=36.9, lon=30.7, name="X")
        self.assertIsNone(req.address)

    def test_address_without_name(self):
        req = server.GenerateRequest(address="Kaleici, Antalya")
        self.assertIsNone(req.name)

    def test_blank_name_becomes_none(self):
        self.assertIsNone(server.GenerateRequest(lat=36.9, lon=30.7, name="  ").name)

    def test_rejects_missing_location(self):
        for kwargs in ({}, {"lat": 36.9}, {"name": "X"}):
            with self.assertRaises(ValidationError, msg=kwargs):
                server.GenerateRequest(**kwargs)

    def test_rejects_both_address_and_coordinates(self):
        with self.assertRaises(ValidationError):
            server.GenerateRequest(address="Kas", lat=36.2, lon=29.6)

    def test_rejects_out_of_range(self):
        with self.assertRaises(ValidationError):
            server.GenerateRequest(lat=95, lon=30)


if __name__ == "__main__":
    unittest.main()

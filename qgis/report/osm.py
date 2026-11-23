"""Nominatim geocoding (POIs come from the local extract, see poi_provider.py).

Nominatim usage policy: max 1 request/second, identifying User-Agent, results cached.
Coverage is Türkiye only: geocode results in other countries are rejected.
No PyQGIS imports: this module is plain Python.
"""
from __future__ import annotations

import hashlib
import json
import os
import time

import requests

CACHE_DIR = os.environ.get("REPORT_CACHE_DIR", "/cache")
USER_AGENT = os.environ.get(
    "OSM_USER_AGENT",
    "qgis-property-report/0.2 (PyQGIS location report demo)",
)
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
COUNTRY_CODE = "tr"  # coverage: Türkiye only


class OsmError(RuntimeError):
    """Nominatim could not be reached or returned an error."""


class AddressNotFoundError(OsmError):
    """Nominatim answered, but found nothing for the address (an input problem)."""


class OutsideCoverageError(OsmError):
    """The address was found, but not in Türkiye."""


# ---------------------------------------------------------------- cache helpers
def _cache_path(kind: str, key: str) -> str:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return os.path.join(CACHE_DIR, kind, f"{digest}.json")


def _cache_get(kind: str, key: str, max_age_s: float | None):
    path = _cache_path(kind, key)
    if not os.path.exists(path):
        return None
    if max_age_s is not None and time.time() - os.path.getmtime(path) > max_age_s:
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _cache_put(kind: str, key: str, value) -> None:
    path = _cache_path(kind, key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(value, fh, ensure_ascii=False)


def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


# ------------------------------------------------------------------- Nominatim
_last_nominatim_call = 0.0


def _nominatim_throttle() -> None:
    """Enforce >= 1 s between Nominatim requests (also across processes via a stamp file)."""
    global _last_nominatim_call
    stamp = os.path.join(CACHE_DIR, "nominatim.last")
    last = _last_nominatim_call
    try:
        last = max(last, os.path.getmtime(stamp))
    except OSError:
        pass
    wait = 1.1 - (time.time() - last)
    if wait > 0:
        time.sleep(wait)
    _last_nominatim_call = time.time()
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(stamp, "w"):
        pass


def geocode(address: str) -> dict:
    """Return {'lat', 'lon', 'display_name', 'country_code'} for an address in Türkiye.

    Cached indefinitely. Raises OsmError if not found, OutsideCoverageError if the
    address is outside Türkiye.
    """
    key = " ".join(address.lower().split())
    result = _cache_get("nominatim", key, max_age_s=None)
    if result is None or "country_code" not in result:  # old entries lack the country
        _nominatim_throttle()
        try:
            r = _session().get(
                NOMINATIM_URL,
                params={"q": address, "format": "jsonv2", "limit": 1, "addressdetails": 1},
                timeout=20,
            )
            r.raise_for_status()
            hits = r.json()
        except requests.RequestException as exc:
            raise OsmError(f"Nominatim request failed: {exc}") from exc
        if not hits:
            raise AddressNotFoundError(f"Address not found: {address!r}")
        hit = hits[0]
        result = {
            "lat": float(hit["lat"]),
            "lon": float(hit["lon"]),
            "display_name": hit.get("display_name", address),
            "country_code": (hit.get("address") or {}).get("country_code", ""),
        }
        _cache_put("nominatim", key, result)
    if result["country_code"] != COUNTRY_CODE:
        # Report the input and the country code, not display_name: Nominatim returns
        # display_name in the local language (e.g. Greek), which is unreadable here.
        country = (result["country_code"] or "unknown").upper()
        raise OutsideCoverageError(f"Address is not in Türkiye: {address!r} was found in "
                                   f"country {country}. Coverage: Türkiye only.")
    return result


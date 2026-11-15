"""HTTP wrapper around generate_report.py, called by n8n.

    POST /generate              {"lat": 36.2018, "lon": 29.6377, "name": "Kas Harbour Villa"}
                                {"address": "Kaleici, Antalya", "name": "Old Town Apartment"}
    GET  /files/{job}/{name}    download a generated file (report.pdf, summary.json, ...)
    GET  /health                liveness + OSM extract check (503 if the extract is missing)

Each request runs generate_report.py in a separate process, so QGIS is initialised
and torn down cleanly per report and a crash never takes the API down.
Output goes to $OUT_DIR/<job>/ (default /out).

Every error response has the same shape:
    {"status": "error", "job": "...", "error_code": "...", "error": "human-readable message"}
error_code -> HTTP status: see HTTP_STATUS.
"""
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, model_validator

APP_DIR = Path(__file__).resolve().parent
SCRIPT = APP_DIR / "generate_report.py"
OUT_DIR = Path(os.environ.get("OUT_DIR", "/out")).resolve()
OSM_PBF_PATH = Path(os.environ.get("OSM_PBF_PATH", "/data/turkey-latest.osm.pbf"))
# Below n8n's HTTP Request timeout (630 s), so the client gets our 504, not a socket error.
TIMEOUT_S = int(os.environ.get("REPORT_TIMEOUT_S", "540"))
STDERR_TAIL_CHARS = 2000

# error_code (written by generate_report.py to error.json, or set here) -> HTTP status
HTTP_STATUS = {
    "invalid_input": 422,
    "outside_coverage": 422,
    "address_not_found": 422,
    "no_osm_data": 422,
    "geocoding_unavailable": 502,
    "extract_unavailable": 503,
    "timeout": 504,
    "processing_error": 500,
}
# Fallback when error.json is missing (e.g. the process crashed): exit code -> error_code
EXIT_CODE_ERRORS = {2: "invalid_input", 3: "geocoding_unavailable"}
# Only these include the stderr tail; for input errors the message says it all.
DEBUG_ERRORS = {"processing_error", "timeout", "extract_unavailable"}

app = FastAPI(title="Property Location Report API", version="0.2.0")


class GenerateRequest(BaseModel):
    lat: float | None = Field(None, ge=-90, le=90, description="Latitude (WGS84)")
    lon: float | None = Field(None, ge=-180, le=180, description="Longitude (WGS84)")
    address: str | None = Field(None, min_length=3, max_length=300,
                                description="Free-text address in Türkiye (geocoded)")
    name: str | None = Field(None, max_length=200,
                             description="Report title (default: address or coordinates)")

    @model_validator(mode="after")
    def _location(self):
        if self.name is not None and not self.name.strip():
            self.name = None
        has_coords = self.lat is not None or self.lon is not None
        if self.address and has_coords:
            raise ValueError("use either 'address' or 'lat'/'lon', not both")
        if not self.address and (self.lat is None or self.lon is None):
            raise ValueError("'address' or both 'lat' and 'lon' are required")
        return self


def classify(exit_code: int, error_info: dict | None) -> tuple[str, str]:
    """(error_code, message) for a failed generator run."""
    info = error_info or {}
    code = info.get("error_code")
    if code not in HTTP_STATUS:
        code = EXIT_CODE_ERRORS.get(exit_code, "processing_error")
    return code, info.get("error") or "report generation failed"


def _tail(text: str, limit: int = STDERR_TAIL_CHARS) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else "..." + text[-limit:]


def _new_job_id(label: str) -> str:
    """Readable and unique: 20261111-153012-kas-harbour-villa-1a2b3c."""
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:40] or "report"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{slug}-{uuid.uuid4().hex[:6]}"


def _error(error_code: str, message: str, job: str | None = None, **extra) -> JSONResponse:
    return JSONResponse(status_code=HTTP_STATUS[error_code],
                        content={"status": "error", "job": job, "error_code": error_code,
                                 "error": message, **extra})


def _read_error_json(job_dir: Path) -> dict | None:
    try:
        return json.loads((job_dir / "error.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


@app.exception_handler(RequestValidationError)
def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    parts = []
    for err in exc.errors():
        if err.get("type") == "json_invalid":
            return _error("invalid_input", "request body is not valid JSON")
        field =".".join(str(p) for p in err.get("loc", ()) if p != "body")
        msg = err.get("msg", "invalid value").removeprefix("Value error, ")
        parts.append(f"{field}: {msg}" if field else msg)
    return _error("invalid_input", "; ".join(parts) or "invalid request body")


def _extract_problem() -> str | None:
    if not OSM_PBF_PATH.is_file():
        return (f"OSM extract not found: {OSM_PBF_PATH}. Download it with "
                "scripts/download_extract.ps1 and set OSM_PBF_PATH.")
    if not os.access(OSM_PBF_PATH, os.R_OK):
        return f"OSM extract is not readable: {OSM_PBF_PATH}"
    return None


@app.get("/health")
def health():
    problem = _extract_problem()
    extract = {"path": str(OSM_PBF_PATH), "ok": problem is None}
    if problem:
        return JSONResponse(status_code=503, content={
            "status": "error", "error_code": "extract_unavailable", "error": problem,
            "osm_extract": extract})
    extract["size_mb"] = round(OSM_PBF_PATH.stat().st_size / 1e6)
    return {"status": "ok", "osm_extract": extract}


# Plain `def` (not async): FastAPI runs it in a worker thread, so a long report
# does not block other requests such as /health or file downloads.
@app.post("/generate")
def generate(req: GenerateRequest, request: Request):
    label = req.name or req.address or f"{req.lat}-{req.lon}"
    job = _new_job_id(label)
    job_dir = OUT_DIR / job

    # Fail fast: without the extract every report would fail after QGIS start-up.
    problem = _extract_problem()
    if problem:
        return _error("extract_unavailable", problem, job)

    cmd = [sys.executable, str(SCRIPT), "--out", str(job_dir)]
    if req.address:
        cmd += ["--address", req.address]
    else:
        cmd += ["--lat", repr(req.lat), "--lon", repr(req.lon)]
    if req.name:
        cmd += ["--name", req.name]

    try:
        proc = subprocess.run(cmd, cwd=APP_DIR, capture_output=True, text=True,
                              timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr
        return _error("timeout", f"report generation timed out after {TIMEOUT_S}s", job,
                      stderr_tail=_tail(stderr))

    if proc.returncode != 0:
        code, message = classify(proc.returncode, _read_error_json(job_dir))
        extra = {"exit_code": proc.returncode}
        if code in DEBUG_ERRORS:
            extra["stderr_tail"] = _tail(proc.stderr)
        return _error(code, message, job, **extra)

    pdf, summary = job_dir / "report.pdf", job_dir / "summary.json"
    missing = [p.name for p in (pdf, summary) if not p.is_file()]
    if missing:
        return _error("processing_error",
                      f"generator exited 0 but did not write: {', '.join(missing)}", job,
                      stderr_tail=_tail(proc.stderr))

    files = sorted(p.name for p in job_dir.iterdir() if p.is_file())
    base = str(request.base_url).rstrip("/")
    return {
        "status": "ok",
        "job": job,
        "pdf_path": str(pdf),
        "summary_path": str(summary),
        "pdf_url": f"{base}/files/{job}/report.pdf",
        "summary_url": f"{base}/files/{job}/summary.json",
        "files": files,
    }


@app.get("/files/{job}/{name}")
def get_file(job: str, name: str):
    path = (OUT_DIR / job / name).resolve()
    # Only files directly inside a job folder; blocks "..", absolute paths, etc.
    if path.parent.parent != OUT_DIR or not path.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(path, filename=name)

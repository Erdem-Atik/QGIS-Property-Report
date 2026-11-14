"""HTTP wrapper around generate_report.py, called by n8n.

    POST /generate              {"lat": 36.2018, "lon": 29.6377, "name": "Kas Harbour Villa"}
    GET  /files/{job}/{name}    download a generated file (report.pdf, summary.json, ...)
    GET  /health                liveness check

Each request runs generate_report.py in a separate process, so QGIS is initialised
and torn down cleanly per report and a crash never takes the API down.
Output goes to $OUT_DIR/<job>/ (default /out).
"""
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

APP_DIR = Path(__file__).resolve().parent
SCRIPT = APP_DIR / "generate_report.py"
OUT_DIR = Path(os.environ.get("OUT_DIR", "/out")).resolve()
TIMEOUT_S = int(os.environ.get("REPORT_TIMEOUT_S", "600"))
STDERR_TAIL_CHARS = 2000

app = FastAPI(title="Property Location Report API", version="0.1.0")


class GenerateRequest(BaseModel):
    lat: float = Field(..., ge=-90, le=90, description="Latitude (WGS84)")
    lon: float = Field(..., ge=-180, le=180, description="Longitude (WGS84)")
    name: str = Field(..., min_length=1, max_length=200, description="Report title")


def _tail(text: str, limit: int = STDERR_TAIL_CHARS) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else "..." + text[-limit:]


def _new_job_id(name: str) -> str:
    """Readable and unique: 20261111-153012-kas-harbour-villa-1a2b3c."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "report"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{slug}-{uuid.uuid4().hex[:6]}"


def _error(status: int, message: str, job: str, **extra) -> JSONResponse:
    return JSONResponse(status_code=status,
                        content={"status": "error", "job": job, "error": message, **extra})


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# Plain `def` (not async): FastAPI runs it in a worker thread, so a long report
# does not block other requests such as /health or file downloads.
@app.post("/generate")
def generate(req: GenerateRequest, request: Request):
    job = _new_job_id(req.name)
    job_dir = OUT_DIR / job
    cmd = [sys.executable, str(SCRIPT),
           "--lat", repr(req.lat), "--lon", repr(req.lon),
           "--name", req.name, "--out", str(job_dir)]

    try:
        proc = subprocess.run(cmd, cwd=APP_DIR, capture_output=True, text=True,
                              timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr
        return _error(500, f"report generation timed out after {TIMEOUT_S}s", job,
                      stderr_tail=_tail(stderr))

    if proc.returncode != 0:
        return _error(500, "report generation failed", job,
                      exit_code=proc.returncode, stderr_tail=_tail(proc.stderr))

    pdf, summary = job_dir / "report.pdf", job_dir / "summary.json"
    missing = [p.name for p in (pdf, summary) if not p.is_file()]
    if missing:
        return _error(500, f"generator exited 0 but did not write: {', '.join(missing)}", job,
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

# -*- coding: utf-8 -*-
"""Extension router for threshold service: adds /health and /meta endpoints.

Run with: uvicorn service_ext:app --host 0.0.0.0 --port 8000
(Internally imports threshold_service_fastapi.app and mounts extra routes.)
"""
from fastapi import APIRouter
from fastapi.responses import JSONResponse
import os, json, time, socket
from pathlib import Path

# Import the existing FastAPI app
from threshold_service_fastapi import app

router = APIRouter()

@router.get("/health")
def health():
    now = int(time.time())
    meta_path = os.getenv("META_PATH", "./qr_multihead_outputs/meta.json")
    meta_ok = Path(meta_path).exists()
    return {
        "status": "ok" if meta_ok else "degraded",
        "time": now,
        "host": socket.gethostname(),
        "meta_path": meta_path,
        "meta_exists": meta_ok,
    }

@router.get("/meta")
def meta():
    meta_path = os.getenv("META_PATH", "./qr_multihead_outputs/meta.json")
    p = Path(meta_path)
    if not p.exists():
        return JSONResponse(status_code=404, content={"error": f"meta not found at {meta_path}"})
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"failed to read meta: {e}"})
    return data

# Mount router
app.include_router(router)

# Expose app for uvicorn
__all__ = ["app"]

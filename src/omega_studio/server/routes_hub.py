"""Hugging Face download jobs — poll ``…/download/{job_id}/status`` (see README)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from omega_studio.downloads.hf_download import download_hf_file
from omega_studio.paths import app_data_dir, ensure_app_dirs

log = logging.getLogger("omega_studio.server.hub")

router = APIRouter(prefix="/v1/models/hub", tags=["hub"])


class HubDownloadStart(BaseModel):
    repo_id: str
    filename: str
    dest_subdir: str = ""


@router.post("/download")
async def hub_download_start(request: Request, body: HubDownloadStart):
    store = getattr(request.app.state, "hub_jobs", None)
    if store is None:
        raise HTTPException(503, "hub job store unavailable")
    _, models_root = ensure_app_dirs()
    sub = body.dest_subdir.strip().replace("\\", "/").strip("/")
    if ".." in sub or sub.startswith("/") or ":" in sub:
        raise HTTPException(400, "invalid dest_subdir")
    dest = Path(models_root) / sub if sub else Path(models_root)
    dest.mkdir(parents=True, exist_ok=True)

    job = store.create()

    async def _run() -> None:
        try:
            await store.run_hf_download_async(
                job,
                repo_id=body.repo_id,
                filename=body.filename,
                dest_dir=dest,
                download_fn=download_hf_file,
            )
        except Exception as exc:
            log.exception("hub download task failed")
            job.status = "error"
            job.error = str(exc)

    asyncio.create_task(_run())
    poll_url = f"/v1/models/hub/download/{job.job_id}/status"
    return {"job_id": job.job_id, "dest_dir": str(dest), "poll": poll_url}


@router.get("/download/{job_id}/status")
def hub_download_status(request: Request, job_id: str):
    store = getattr(request.app.state, "hub_jobs", None)
    if store is None:
        raise HTTPException(503, "hub job store unavailable")
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "unknown job_id")
    return job.to_public()


@router.get("/app-data")
def hub_app_data_debug():
    """Operator aid: resolved per-user models directory."""
    root, models = ensure_app_dirs()
    return {"app_data": str(root), "models": str(models), "localappdata_hint": str(app_data_dir())}

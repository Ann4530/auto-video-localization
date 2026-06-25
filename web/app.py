"""FastAPI app: API JSON (cho web UI + n8n) và các trang HTML.

Chạy:  uvicorn web.app:app --host 127.0.0.1 --port 8000

Process này NHẸ: chỉ ghi/đọc job vào hàng đợi SQLite, KHÔNG nạp model. Worker
(python -m worker.run) mới là nơi xử lý nặng.
"""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from fastapi import (Depends, FastAPI, File, Form, HTTPException, Request,
                     UploadFile)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.config import ROOT
from src.jobs import JobOptions
from src.upload.dispatch import upload_result
from worker.db import STATE_DONE

from .deps import get_config, list_voices, queue, require_api_key
from .schemas import (ChannelScanIn, JobCreated, JobStatus, UploadIn,
                      UrlJobIn)

HERE = Path(__file__).resolve().parent
UPLOAD_DIR = ROOT / "data" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Video Localization Studio")

templates = Jinja2Templates(directory=str(HERE / "templates"))
app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
# Phục vụ file kết quả để preview/tải
_cfg = get_config()
app.mount("/outputs", StaticFiles(directory=str(_cfg.output_dir)), name="outputs")


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def _opts_from_in(data) -> JobOptions:
    """JobOptionsIn (hoặc subclass) -> JobOptions."""
    return JobOptions.from_request(
        data.choice,
        ocr_overlay=data.ocr_overlay,
        target_language=data.target_language,
        voice=data.voice,
        rate=data.rate,
        keep_original_volume=data.keep_original_volume,
        provider=data.provider,
        model=data.model,
        style=data.style,
        upload_targets=data.upload_targets,
        caption=data.caption,
    )


def _row_to_status(row: dict) -> JobStatus:
    output_url = None
    if row.get("output_path"):
        output_url = f"/outputs/{Path(row['output_path']).name}"
    return JobStatus(
        id=row["id"],
        source_type=row["source_type"],
        title=row.get("title"),
        state=row["state"],
        stage=row.get("stage"),
        percent=row.get("percent") or 0,
        output_url=output_url,
        error=row.get("error"),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


def _ui_key() -> str:
    """Key để nhúng vào UI cho HTMX gửi kèm (local single-user)."""
    return get_config().env("API_KEY")


# ----------------------------------------------------------------------------
# Trang HTML
# ----------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def page_index(request: Request):
    voices = await list_voices()
    return templates.TemplateResponse(
        request, "index.html",
        {"voices": voices, "api_key": _ui_key()},
    )


@app.get("/jobs", response_class=HTMLResponse)
async def page_jobs(request: Request):
    rows = queue().list(limit=100)
    jobs = [_row_to_status(r) for r in rows]
    return templates.TemplateResponse(
        request, "jobs.html", {"jobs": jobs, "api_key": _ui_key()}
    )


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
async def page_job_detail(request: Request, job_id: str):
    row = queue().get(job_id)
    if not row:
        raise HTTPException(404, "Không thấy job")
    return templates.TemplateResponse(
        request, "job_detail.html",
        {"job": _row_to_status(row), "api_key": _ui_key()},
    )


# ----------------------------------------------------------------------------
# API: tạo job
# ----------------------------------------------------------------------------
@app.post("/api/jobs/upload", response_model=JobCreated,
          dependencies=[Depends(require_api_key)])
async def api_job_upload(
    file: UploadFile = File(...),
    choice: str = Form("both"),
    ocr_overlay: bool = Form(False),
    target_language: str = Form("Tiếng Việt"),
    voice: str = Form("vi-VN-HoaiMyNeural"),
    rate: str = Form("+0%"),
    keep_original_volume: float | None = Form(None),
    provider: str | None = Form(None),
    model: str | None = Form(None),
    style: str | None = Form(None),
):
    # Lưu file upload
    safe_name = Path(file.filename or "video.mp4").name
    dest = UPLOAD_DIR / f"{uuid.uuid4().hex}_{safe_name}"
    with open(dest, "wb") as out:
        shutil.copyfileobj(file.file, out)

    opts = JobOptions.from_request(
        choice, ocr_overlay=ocr_overlay, target_language=target_language,
        voice=voice, rate=rate, keep_original_volume=keep_original_volume,
        provider=provider, model=model, style=style,
    )
    job_id = queue().enqueue("file", str(dest), opts, title=Path(safe_name).stem)
    return JobCreated(id=job_id)


@app.post("/api/jobs/url", response_model=JobCreated,
          dependencies=[Depends(require_api_key)])
async def api_job_url(data: UrlJobIn):
    opts = _opts_from_in(data)
    job_id = queue().enqueue("url", data.url, opts)
    return JobCreated(id=job_id)


# ----------------------------------------------------------------------------
# API: trạng thái job
# ----------------------------------------------------------------------------
@app.get("/api/jobs", dependencies=[Depends(require_api_key)])
async def api_jobs(limit: int = 50, offset: int = 0, state: str | None = None):
    rows = queue().list(limit=limit, offset=offset, state=state)
    return [_row_to_status(r) for r in rows]


@app.get("/api/jobs/{job_id}", response_model=JobStatus,
         dependencies=[Depends(require_api_key)])
async def api_job_status(job_id: str):
    row = queue().get(job_id)
    if not row:
        raise HTTPException(404, "Không thấy job")
    return _row_to_status(row)


@app.get("/api/jobs/{job_id}/result", dependencies=[Depends(require_api_key)])
async def api_job_result(job_id: str):
    row = queue().get(job_id)
    if not row or not row.get("output_path"):
        raise HTTPException(404, "Chưa có kết quả")
    path = Path(row["output_path"])
    if not path.exists():
        raise HTTPException(404, "File kết quả không tồn tại")
    return FileResponse(str(path), media_type="video/mp4", filename=path.name)


# ----------------------------------------------------------------------------
# API: giọng đọc
# ----------------------------------------------------------------------------
@app.get("/api/voices")
async def api_voices(locale: str | None = None):
    return await list_voices(locale)


# ----------------------------------------------------------------------------
# API: kênh (cho n8n automation)
# ----------------------------------------------------------------------------
def _build_downloader():
    from src.download.downloader import Downloader
    cfg = get_config()
    return Downloader(
        dest_dir=cfg.downloads_dir,
        fmt=cfg.download.get("format", "b"),
        cookies_file=cfg.env("COOKIES_FILE") or None,
        cookies_from_browser=cfg.download.get("cookies_from_browser"),
    )


@app.get("/api/channels/scan", dependencies=[Depends(require_api_key)])
async def api_channel_scan(channel: str | None = None, limit: int = 5):
    from src.utils.state import State
    cfg = get_config()
    ch = channel or (cfg.sources[0] if cfg.sources else None)
    if not ch:
        raise HTTPException(400, "Thiếu channel và config.sources rỗng")
    dl = _build_downloader()
    state = State(cfg.state_file)
    try:
        entries = dl.list_channel_videos(ch, limit)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Không quét được kênh: {e}")
    out = []
    for e in entries:
        vid = str(e.get("id", ""))
        out.append({
            "id": vid,
            "url": e.get("url") or e.get("webpage_url") or ch,
            "title": e.get("title") or e.get("description") or vid,
            "is_processed": bool(vid and state.is_processed(vid)),
        })
    return out


@app.post("/api/channels/scan", dependencies=[Depends(require_api_key)])
async def api_channel_scan_enqueue(data: ChannelScanIn):
    from src.utils.state import State
    cfg = get_config()
    ch = data.channel or (cfg.sources[0] if cfg.sources else None)
    if not ch:
        raise HTTPException(400, "Thiếu channel và config.sources rỗng")
    dl = _build_downloader()
    state = State(cfg.state_file)
    try:
        entries = dl.list_channel_videos(ch, data.limit)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Không quét được kênh: {e}")
    opts = _opts_from_in(data)
    created, skipped = [], []
    for e in entries:
        vid = str(e.get("id", ""))
        if vid and state.is_processed(vid):
            skipped.append(vid)
            continue
        url = e.get("url") or e.get("webpage_url") or ch
        jid = queue().enqueue("url", url, opts, title=e.get("title"))
        created.append({"id": jid, "video_id": vid, "url": url})
    return {"created": created, "skipped": skipped}


# ----------------------------------------------------------------------------
# API: đăng kết quả lên nền tảng
# ----------------------------------------------------------------------------
@app.post("/api/jobs/{job_id}/upload", dependencies=[Depends(require_api_key)])
async def api_job_upload_result(job_id: str, data: UploadIn):
    row = queue().get(job_id)
    if not row or row["state"] != STATE_DONE or not row.get("output_path"):
        raise HTTPException(400, "Job chưa xong hoặc không có kết quả")
    path = Path(row["output_path"])
    if not path.exists():
        raise HTTPException(404, "File kết quả không tồn tại")
    caption = (data.caption or "{title}").format(title=row.get("title") or "")
    result = upload_result(get_config(), path, data.targets, caption)
    return result


# ----------------------------------------------------------------------------
# Healthcheck
# ----------------------------------------------------------------------------
@app.get("/healthz")
async def healthz():
    c = queue().counts()
    return JSONResponse({
        "ok": True,
        "queued": c.get("queued", 0),
        "running": c.get("running", 0),
        "done": c.get("done", 0),
        "failed": c.get("failed", 0),
    })

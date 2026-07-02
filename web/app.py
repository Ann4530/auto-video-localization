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
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               RedirectResponse)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.config import ROOT
from src.jobs import JobOptions
from src.languages import SOURCE_LANGUAGES, TARGET_LANGUAGES
from src.upload.dispatch import upload_result
from worker.db import STATE_DONE

from .auth import COOKIE, AuthMiddleware, make_token
from .deps import get_config, list_voices, queue, require_api_key
from .schemas import (ChannelScanIn, JobCreated, JobStatus, ProjectIn,
                      ProjectUpdate, RerenderIn, UploadIn, UrlJobIn, VoiceJobIn)

HERE = Path(__file__).resolve().parent
UPLOAD_DIR = ROOT / "data" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
EDITS_DIR = ROOT / "data" / "edits"
EDITS_DIR.mkdir(parents=True, exist_ok=True)
PROJECTS_DIR = ROOT / "data" / "projects"
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)


def _project_dirs(pid: str):
    """Trả (source_dir, output_dir) của project, tạo nếu chưa có."""
    base = PROJECTS_DIR / pid
    src = base / "source"
    out = base / "output"
    src.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    return src, out

app = FastAPI(title="Video Localization Studio")

# Bảo vệ truy cập khi mở public (bật khi APP_PASSWORD/API_KEY được đặt)
app.add_middleware(
    AuthMiddleware,
    get_password=lambda: get_config().env("APP_PASSWORD"),
    get_api_key=lambda: get_config().env("API_KEY"),
)

templates = Jinja2Templates(directory=str(HERE / "templates"))
app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
# Phục vụ file kết quả để preview/tải
_cfg = get_config()
app.mount("/outputs", StaticFiles(directory=str(_cfg.output_dir)), name="outputs")
app.mount("/projectfiles", StaticFiles(directory=str(PROJECTS_DIR)), name="projectfiles")


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def _opts_from_in(data) -> JobOptions:
    """JobOptionsIn (hoặc subclass) -> JobOptions."""
    return JobOptions.from_request(
        data.choice,
        ocr_overlay=data.ocr_overlay,
        ocr_all_text=data.ocr_all_text,
        target_language=data.target_language,
        source_language=data.source_language,
        voice=data.voice,
        rate=data.rate,
        keep_original_volume=data.keep_original_volume,
        provider=data.provider,
        model=data.model,
        style=data.style,
        upload_targets=data.upload_targets,
        caption=data.caption,
    )


def _output_url(output_path: str | None) -> str | None:
    if not output_path:
        return None
    p = Path(output_path)
    try:
        rel = p.resolve().relative_to(PROJECTS_DIR.resolve())
        return "/projectfiles/" + str(rel).replace("\\", "/")
    except ValueError:
        return f"/outputs/{p.name}"


def _row_to_status(row: dict) -> JobStatus:
    return JobStatus(
        id=row["id"],
        source_type=row["source_type"],
        title=row.get("title"),
        state=row["state"],
        stage=row.get("stage"),
        percent=row.get("percent") or 0,
        output_url=_output_url(row.get("output_path")),
        error=row.get("error"),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


def _ui_key() -> str:
    """Key để nhúng vào UI cho HTMX gửi kèm (local single-user)."""
    return get_config().env("API_KEY")


def _segments_path_for(row: dict) -> Path | None:
    """Đường dẫn JSON bản dịch của job (suy ra từ output_path)."""
    if not row.get("output_path"):
        return None
    return Path(row["output_path"]).with_suffix(".segments.json")


def _read_segments(row: dict) -> list[dict]:
    import json
    p = _segments_path_for(row)
    if not p or not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []


# ----------------------------------------------------------------------------
# Đăng nhập
# ----------------------------------------------------------------------------
@app.get("/login", response_class=HTMLResponse)
async def page_login(request: Request, next: str = "/", error: str = ""):
    return templates.TemplateResponse(
        request, "login.html", {"next": next, "error": error, "api_key": ""}
    )


@app.post("/login")
async def do_login(request: Request, password: str = Form(...),
                   next: str = Form("/")):
    pw = get_config().env("APP_PASSWORD")
    if not pw or password != pw:
        return RedirectResponse(f"/login?error=1&next={next}", status_code=302)
    resp = RedirectResponse(next or "/", status_code=302)
    resp.set_cookie(
        COOKIE, make_token(pw), httponly=True, samesite="lax",
        max_age=60 * 60 * 24 * 30,  # 30 ngày
    )
    return resp


@app.get("/logout")
async def do_logout():
    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie(COOKIE)
    return resp


# ----------------------------------------------------------------------------
# Trang HTML
# ----------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def page_index(request: Request):
    voices = await list_voices()
    projects = queue().list_projects()
    return templates.TemplateResponse(
        request, "index.html",
        {"voices": voices, "api_key": _ui_key(),
         "target_languages": TARGET_LANGUAGES,
         "source_languages": SOURCE_LANGUAGES,
         "projects": projects,
         "sel_project": request.query_params.get("project", ""),
         "sel_mode": request.query_params.get("mode", "translate")},
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
          dependencies=[])
async def api_job_upload(
    file: UploadFile = File(...),
    choice: str = Form("both"),
    ocr_overlay: bool = Form(False),
    ocr_all_text: bool = Form(False),
    target_language: str = Form("Tiếng Việt"),
    source_language: str | None = Form(None),
    voice: str = Form("vi-VN-HoaiMyNeural"),
    rate: str = Form("+0%"),
    keep_original_volume: float | None = Form(None),
    provider: str | None = Form(None),
    model: str | None = Form(None),
    style: str | None = Form(None),
    project_id: str | None = Form(None),
):
    pid = project_id or queue().ensure_default_project()
    src_dir, out_dir = _project_dirs(pid)
    # Lưu file upload vào thư mục source của project
    safe_name = Path(file.filename or "video.mp4").name
    dest = src_dir / f"{uuid.uuid4().hex}_{safe_name}"
    with open(dest, "wb") as out:
        shutil.copyfileobj(file.file, out)

    opts = JobOptions.from_request(
        choice, ocr_overlay=ocr_overlay, ocr_all_text=ocr_all_text,
        target_language=target_language, source_language=source_language,
        voice=voice, rate=rate, keep_original_volume=keep_original_volume,
        provider=provider, model=model, style=style, output_dir=str(out_dir),
    )
    job_id = queue().enqueue("file", str(dest), opts, title=Path(safe_name).stem,
                             project_id=pid)
    return JobCreated(id=job_id)


@app.post("/api/jobs/create", response_model=JobCreated, dependencies=[])
async def api_job_create_video(
    file: UploadFile = File(...),
    terms: str = Form(""),
    lang: str = Form("vi"),
    model: str = Form("base"),
    project_id: str | None = Form(None),
):
    """TẠO video: nhận footage tự quay -> gắn phụ đề karaoke động (mode=create)."""
    pid = project_id or queue().ensure_default_project()
    src_dir, out_dir = _project_dirs(pid)
    safe_name = Path(file.filename or "video.mp4").name
    dest = src_dir / f"{uuid.uuid4().hex}_{safe_name}"
    with open(dest, "wb") as out:
        shutil.copyfileobj(file.file, out)

    term_list = [t.strip() for t in terms.split(",") if t.strip()]
    opts = JobOptions.from_request(
        "none", mode="create", create_terms=term_list,
        create_lang=lang, create_model=model, output_dir=str(out_dir),
    )
    job_id = queue().enqueue("file", str(dest), opts,
                             title=Path(safe_name).stem, project_id=pid)
    return JobCreated(id=job_id)


@app.post("/api/jobs/create-voice", response_model=JobCreated, dependencies=[])
async def api_job_create_voice(data: VoiceJobIn):
    """TẠO video bằng LỒNG TIẾNG AI từ kịch bản (mode=create, source=voice_ai)."""
    script = (data.script or "").strip()
    if not script:
        raise HTTPException(400, "Cần nhập kịch bản")
    pid = data.project_id or queue().ensure_default_project()
    _, out_dir = _project_dirs(pid)
    term_list = [t.strip() for t in (data.terms or "").split(",") if t.strip()]
    opts = JobOptions.from_request(
        "none", mode="create", create_source="voice_ai", create_script=script,
        create_terms=term_list, voice=data.voice, rate=data.rate,
        output_dir=str(out_dir),
    )
    title = (data.title or script[:40]).strip()
    job_id = queue().enqueue("script", script, opts, title=title, project_id=pid)
    return JobCreated(id=job_id)


@app.post("/api/jobs/url", response_model=JobCreated,
          dependencies=[])
async def api_job_url(data: UrlJobIn):
    pid = data.project_id or queue().ensure_default_project()
    _, out_dir = _project_dirs(pid)
    opts = _opts_from_in(data)
    opts.output_dir = str(out_dir)
    job_id = queue().enqueue("url", data.url, opts, project_id=pid)
    return JobCreated(id=job_id)


# ----------------------------------------------------------------------------
# API: trạng thái job
# ----------------------------------------------------------------------------
@app.get("/api/jobs", dependencies=[])
async def api_jobs(limit: int = 50, offset: int = 0, state: str | None = None):
    rows = queue().list(limit=limit, offset=offset, state=state)
    return [_row_to_status(r) for r in rows]


@app.get("/api/jobs/{job_id}", response_model=JobStatus,
         dependencies=[])
async def api_job_status(job_id: str):
    row = queue().get(job_id)
    if not row:
        raise HTTPException(404, "Không thấy job")
    return _row_to_status(row)


@app.get("/api/jobs/{job_id}/result", dependencies=[])
async def api_job_result(job_id: str):
    row = queue().get(job_id)
    if not row or not row.get("output_path"):
        raise HTTPException(404, "Chưa có kết quả")
    path = Path(row["output_path"])
    if not path.exists():
        raise HTTPException(404, "File kết quả không tồn tại")
    return FileResponse(str(path), media_type="video/mp4", filename=path.name)


# ----------------------------------------------------------------------------
# API: bản dịch (sửa & render lại)
# ----------------------------------------------------------------------------
@app.get("/api/jobs/{job_id}/segments", dependencies=[])
async def api_job_segments(job_id: str):
    row = queue().get(job_id)
    if not row:
        raise HTTPException(404, "Không thấy job")
    return _read_segments(row)


@app.post("/api/jobs/{job_id}/rerender", response_model=JobCreated,
          dependencies=[])
async def api_job_rerender(job_id: str, data: RerenderIn):
    import json
    row = queue().get(job_id)
    if not row:
        raise HTTPException(404, "Không thấy job")
    if not data.segments:
        raise HTTPException(400, "Cần ít nhất 1 đoạn dịch")

    # Lưu bản dịch đã sửa
    new_id = uuid.uuid4().hex
    seg_file = EDITS_DIR / f"{new_id}.json"
    seg_file.write_text(
        json.dumps([s.model_dump() for s in data.segments], ensure_ascii=False),
        encoding="utf-8",
    )

    # Render lại từ CÙNG nguồn, bỏ qua bóc lời + dịch (segments_path đã đặt).
    pid = row.get("project_id") or queue().ensure_default_project()
    _, out_dir = _project_dirs(pid)
    opts = JobOptions.from_request(
        data.choice,
        segments_path=str(seg_file),
        voice=data.voice, rate=data.rate,
        keep_original_volume=data.keep_original_volume,
        output_dir=str(out_dir),
    )
    jid = queue().enqueue(
        row["source_type"], row["source_ref"], opts,
        title=(row.get("title") or "") + " (đã sửa)", project_id=pid,
    )
    return JobCreated(id=jid)


@app.get("/jobs/{job_id}/edit", response_class=HTMLResponse)
async def page_job_edit(request: Request, job_id: str):
    row = queue().get(job_id)
    if not row:
        raise HTTPException(404, "Không thấy job")
    segments = _read_segments(row)
    voices = await list_voices()
    return templates.TemplateResponse(
        request, "edit.html",
        {"job": _row_to_status(row), "segments": segments,
         "voices": voices, "api_key": _ui_key()},
    )


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


@app.get("/api/channels/scan", dependencies=[])
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


@app.post("/api/channels/scan", dependencies=[])
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
    pid = data.project_id or queue().ensure_default_project()
    _, out_dir = _project_dirs(pid)
    opts = _opts_from_in(data)
    opts.output_dir = str(out_dir)
    created, skipped = [], []
    for e in entries:
        vid = str(e.get("id", ""))
        if vid and state.is_processed(vid):
            skipped.append(vid)
            continue
        url = e.get("url") or e.get("webpage_url") or ch
        jid = queue().enqueue("url", url, opts, title=e.get("title"), project_id=pid)
        created.append({"id": jid, "video_id": vid, "url": url})
    return {"created": created, "skipped": skipped}


# ----------------------------------------------------------------------------
# API: đăng kết quả lên nền tảng
# ----------------------------------------------------------------------------
@app.post("/api/jobs/{job_id}/upload", dependencies=[])
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
# API: Projects
# ----------------------------------------------------------------------------
@app.get("/api/projects", dependencies=[])
async def api_projects():
    return queue().list_projects()


@app.post("/api/projects", response_model=dict, dependencies=[])
async def api_project_create(data: ProjectIn):
    pid = queue().create_project(data.name, data.settings)
    _project_dirs(pid)
    return {"id": pid}


@app.get("/api/projects/{pid}", dependencies=[])
async def api_project_get(pid: str):
    p = queue().get_project(pid)
    if not p:
        raise HTTPException(404, "Không thấy project")
    return p


@app.patch("/api/projects/{pid}", dependencies=[])
async def api_project_update(pid: str, data: ProjectUpdate):
    if not queue().get_project(pid):
        raise HTTPException(404, "Không thấy project")
    queue().update_project(pid, name=data.name, settings=data.settings)
    return {"ok": True}


@app.delete("/api/projects/{pid}", dependencies=[])
async def api_project_delete(pid: str):
    queue().delete_project(pid)
    return {"ok": True}


@app.get("/api/projects/{pid}/jobs", dependencies=[])
async def api_project_jobs(pid: str, limit: int = 100):
    rows = queue().list(limit=limit, project_id=pid)
    return [_row_to_status(r) for r in rows]


# ----------------------------------------------------------------------------
# Trang HTML: Projects
# ----------------------------------------------------------------------------
@app.get("/projects", response_class=HTMLResponse)
async def page_projects(request: Request):
    import json as _json
    projects = queue().list_projects()
    for p in projects:
        try:
            p["settings"] = _json.loads(p.get("settings_json") or "{}")
        except Exception:  # noqa: BLE001
            p["settings"] = {}
    return templates.TemplateResponse(
        request, "projects.html", {"projects": projects, "api_key": _ui_key()}
    )


@app.get("/projects/{pid}", response_class=HTMLResponse)
async def page_project_detail(request: Request, pid: str):
    import json as _json
    p = queue().get_project(pid)
    if not p:
        raise HTTPException(404, "Không thấy project")
    try:
        settings = _json.loads(p.get("settings_json") or "{}")
    except Exception:  # noqa: BLE001
        settings = {}
    rows = queue().list(limit=200, project_id=pid)
    jobs = [_row_to_status(r) for r in rows]
    return templates.TemplateResponse(
        request, "project_detail.html",
        {"project": p, "settings": settings, "jobs": jobs,
         "target_languages": TARGET_LANGUAGES, "api_key": _ui_key()},
    )


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

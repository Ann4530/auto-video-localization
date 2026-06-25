"""Pydantic models cho request/response của API."""
from __future__ import annotations

from pydantic import BaseModel, Field

from src.jobs import CHOICES


class JobOptionsIn(BaseModel):
    """Tuỳ chọn dịch gửi từ client (web/n8n)."""
    choice: str = Field(default="both", description=f"Một trong {CHOICES}")
    ocr_overlay: bool = False
    ocr_all_text: bool = False
    target_language: str = "Tiếng Việt"
    source_language: str | None = None
    voice: str = "vi-VN-HoaiMyNeural"
    rate: str = "+0%"
    keep_original_volume: float | None = None
    provider: str | None = None
    model: str | None = None
    style: str | None = None
    upload_targets: list[str] = Field(default_factory=list)
    caption: str | None = None


class UrlJobIn(JobOptionsIn):
    url: str


class JobCreated(BaseModel):
    id: str
    state: str = "queued"


class JobStatus(BaseModel):
    id: str
    source_type: str
    title: str | None = None
    state: str
    stage: str | None = None
    percent: int = 0
    output_url: str | None = None
    error: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class ChannelScanIn(JobOptionsIn):
    channel: str | None = None
    limit: int = 5


class UploadIn(BaseModel):
    targets: list[str] = Field(default_factory=lambda: ["tiktok"])
    caption: str | None = None


class SegmentIn(BaseModel):
    start: float
    end: float
    text: str


class RerenderIn(BaseModel):
    """Render lại video với bản dịch đã chỉnh sửa."""
    segments: list[SegmentIn]
    choice: str = "both"        # text | voice | both
    voice: str = "vi-VN-HoaiMyNeural"
    rate: str = "+0%"
    keep_original_volume: float | None = None

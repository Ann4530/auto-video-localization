"""Điều phối toàn bộ quy trình cho 1 video:
   tải -> bóc lời -> dịch -> phụ đề -> lồng tiếng -> ghép -> đăng.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .compose.compositor import Compositor
from .compose.subtitles import write_srt
from .config import Config
from .download.downloader import Downloader, VideoItem
from .transcribe.transcriber import Transcriber
from .translate.translator import make_translator
from .tts.synthesizer import Synthesizer
from .upload.facebook import FacebookUploader
from .upload.instagram import InstagramUploader
from .upload.tiktok import TikTokUploader
from .utils.logging import get_logger
from .utils.state import State

log = get_logger("pipeline")


class Pipeline:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.state = State(cfg.state_file)

        self.downloader = Downloader(
            dest_dir=cfg.downloads_dir,
            fmt=cfg.download.get("format", "b"),
            cookies_file=cfg.env("COOKIES_FILE") or None,
            cookies_from_browser=cfg.download.get("cookies_from_browser"),
        )
        # Transcriber nạp model nặng -> tạo lười (lazy) khi cần
        self._transcriber: Transcriber | None = None

        self.translator = make_translator(
            provider=cfg.translate.get("provider", "claude"),
            anthropic_key=cfg.env("ANTHROPIC_API_KEY"),
            gemini_key=cfg.env("GEMINI_API_KEY"),
            model=cfg.translate.get("model", ""),
            target_language=cfg.translate.get("target_language", "Tiếng Việt"),
            style=cfg.translate.get("style", "tự nhiên"),
        )
        self.compositor = Compositor(
            cfg.compose, keep_original_volume=cfg.tts.get("keep_original_volume", 0.12)
        )
        self.mode = cfg.raw.get("mode", "voice_transcript")

    @property
    def transcriber(self) -> Transcriber:
        if self._transcriber is None:
            t = self.cfg.transcribe
            self._transcriber = Transcriber(
                model=t.get("model", "small"),
                language=t.get("language"),
                device=t.get("device", "auto"),
                compute_type=t.get("compute_type", "int8"),
                cpu_threads=t.get("cpu_threads", 4),
            )
        return self._transcriber

    # ---------------------------------------------------------------

    def process_url(self, url: str, mode: str | None = None) -> Path | None:
        """Xử lý 1 URL video đơn lẻ, trả về đường dẫn video kết quả."""
        if mode:
            self.mode = mode
        # Lấy id sớm để check trùng (tải nhẹ metadata)
        item = self.downloader.download(url)
        if self.state.is_processed(item.id):
            log.info("Bỏ qua (đã xử lý): %s", item.id)
            return self.state.get(item.id).get("output")  # type: ignore
        return self._run(item)

    def process_file(self, file_path: str, title: str | None = None,
                     mode: str | None = None) -> Path | None:
        """Xử lý 1 file video CÓ SẴN trên máy (bỏ qua bước tải).

        Dùng cho nguồn khó tải tự động như Douyin: bạn tự tải file về rồi
        đưa đường dẫn vào đây.
        """
        if mode:
            self.mode = mode
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Không thấy file: {path}")
        # ID an toàn cho đường dẫn (tránh ký tự non-ASCII / đặc biệt làm
        # hỏng bộ lọc subtitles của ffmpeg trên Windows).
        safe_id = re.sub(r"[^A-Za-z0-9_-]", "", path.stem.encode("ascii", "ignore").decode())
        if not safe_id:
            safe_id = "video_" + hashlib.md5(str(path).encode()).hexdigest()[:8]
        item = VideoItem(
            id=safe_id,
            url=str(path),
            title=title or path.stem,
            path=path,
            info={},
        )
        if self.state.is_processed(item.id):
            log.info("Bỏ qua (đã xử lý): %s", item.id)
            return self.state.get(item.id).get("output")  # type: ignore
        return self._run(item)

    def _run(self, item: VideoItem) -> Path:
        cfg = self.cfg
        work = cfg.work_dir / item.id
        work.mkdir(parents=True, exist_ok=True)

        mode = self.mode
        # voice_transcript: lồng tiếng + phụ đề
        # voice_only:       lồng tiếng, không phụ đề
        # screen_only:      chỉ đè chữ màn hình, giữ audio gốc
        do_voice = mode in ("voice_transcript", "voice_only")
        do_subtitle = mode == "voice_transcript"
        do_ocr = cfg.ocr.get("enabled", False) or mode == "screen_only"
        log.info("Chế độ: %s (lồng tiếng=%s, phụ đề=%s, OCR=%s)",
                 mode, do_voice, do_subtitle, do_ocr)

        src_lang = ""
        srt_path = None
        vi_voice = None

        if do_voice:
            # 1) Bóc lời  2) Dịch  3) (tuỳ) phụ đề  4) Lồng tiếng
            segments, src_lang = self.transcriber.transcribe(item.path)
            vi_segments = self.translator.translate_segments(segments)
            if do_subtitle:
                srt_path = write_srt(vi_segments, work / "vi.srt")
                # xuất kèm transcript ra thư mục output cho tiện
                write_srt(vi_segments, cfg.output_dir / f"{item.id}_vi.srt")
            total = self.compositor.video_duration(item.path)
            synth = Synthesizer(
                voice=cfg.tts.get("voice", "vi-VN-HoaiMyNeural"),
                rate=cfg.tts.get("rate", "+0%"),
                work_dir=work,
            )
            vi_voice = synth.synthesize(vi_segments, total)

        # 4b) OCR chữ trên màn hình -> đè tiếng Việt
        overlays = self._ocr_overlays(item.path, work) if do_ocr else []

        # 5) Ghép video cuối
        out_path = cfg.output_dir / f"{item.id}_vi.mp4"
        self.compositor.compose(
            video=item.path,
            vi_voice=vi_voice,
            srt=srt_path,
            out_path=out_path,
            overlays=overlays,
        )

        # 6) Đăng
        uploaded = self._upload(item, out_path)

        self.state.mark(
            item.id,
            {
                "title": item.title,
                "url": item.url,
                "mode": mode,
                "source_lang": src_lang,
                "output": str(out_path),
                "uploaded": uploaded,
            },
        )
        log.info("HOÀN TẤT: %s -> %s", item.title, out_path.name)
        return out_path

    def _ocr_overlays(self, video: Path, work: Path) -> list:
        """Chạy OCR + dịch chữ trên màn hình, trả danh sách Overlay."""
        from .ocr.screen_text import ScreenTextTranslator

        o = self.cfg.ocr
        stt = ScreenTextTranslator(
            translator=self.translator,
            sample_fps=o.get("sample_fps", 2.0),
            min_score=o.get("min_score", 0.6),
            only_cjk=o.get("only_cjk", True),
            font_path=o.get("font", "C:/Windows/Fonts/arial.ttf"),
            ocr_max_width=o.get("ocr_max_width", 960),
            scene_diff=o.get("scene_diff", 2.5),
        )
        try:
            return stt.process(video, work)
        except Exception as e:  # noqa: BLE001
            log.error("OCR lỗi (bỏ qua overlay): %s", e)
            return []

    def _upload(self, item: VideoItem, video: Path) -> dict:
        cfg = self.cfg
        up = cfg.upload
        caption = up.get("caption_template", "{title}").format(title=item.title)
        result: dict = {}

        if up.get("tiktok"):
            try:
                tt = TikTokUploader(cfg.env("TIKTOK_ACCESS_TOKEN"))
                result["tiktok"] = tt.upload(video, caption)
            except Exception as e:  # noqa: BLE001
                log.error("Đăng TikTok lỗi: %s", e)
                result["tiktok"] = {"error": str(e)}

        if up.get("facebook"):
            try:
                fb = FacebookUploader(
                    cfg.env("FB_PAGE_ID"), cfg.env("FB_PAGE_ACCESS_TOKEN")
                )
                result["facebook"] = fb.upload(video, caption)
            except Exception as e:  # noqa: BLE001
                log.error("Đăng Facebook lỗi: %s", e)
                result["facebook"] = {"error": str(e)}

        if up.get("instagram"):
            log.warning(
                "Instagram cần URL công khai của video. Hãy host file rồi "
                "gọi InstagramUploader.upload(video_url, caption)."
            )
            result["instagram"] = {"skipped": "cần public URL"}

        return result

    # ---------------------------------------------------------------

    def process_sources(self) -> list[Path]:
        """Quét tất cả nguồn trong config, xử lý video mới."""
        outputs: list[Path] = []
        limit = self.cfg.download.get("max_per_channel", 5)
        for src in self.cfg.sources:
            log.info("Quét nguồn: %s", src)
            try:
                entries = self.downloader.list_channel_videos(src, limit)
            except Exception as e:  # noqa: BLE001
                log.error("Không quét được %s: %s", src, e)
                continue
            for entry in entries:
                vid = str(entry.get("id", ""))
                if vid and self.state.is_processed(vid):
                    continue
                url = entry.get("url") or entry.get("webpage_url") or src
                try:
                    out = self.process_url(url)
                    if out:
                        outputs.append(Path(out))
                except Exception as e:  # noqa: BLE001
                    log.error("Lỗi xử lý %s: %s", url, e)
        return outputs

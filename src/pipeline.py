"""Điều phối toàn bộ quy trình cho 1 video:
   tải -> bóc lời -> dịch -> phụ đề -> lồng tiếng -> ghép -> đăng.
"""
from __future__ import annotations

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

    @property
    def transcriber(self) -> Transcriber:
        if self._transcriber is None:
            t = self.cfg.transcribe
            self._transcriber = Transcriber(
                model=t.get("model", "small"),
                language=t.get("language"),
                device=t.get("device", "auto"),
                compute_type=t.get("compute_type", "int8"),
            )
        return self._transcriber

    # ---------------------------------------------------------------

    def process_url(self, url: str) -> Path | None:
        """Xử lý 1 URL video đơn lẻ, trả về đường dẫn video kết quả."""
        # Lấy id sớm để check trùng (tải nhẹ metadata)
        item = self.downloader.download(url)
        if self.state.is_processed(item.id):
            log.info("Bỏ qua (đã xử lý): %s", item.id)
            return self.state.get(item.id).get("output")  # type: ignore
        return self._run(item)

    def _run(self, item: VideoItem) -> Path:
        cfg = self.cfg
        work = cfg.work_dir / item.id
        work.mkdir(parents=True, exist_ok=True)

        # 1) Bóc lời
        segments, src_lang = self.transcriber.transcribe(item.path)

        # 2) Dịch sang tiếng Việt
        vi_segments = self.translator.translate_segments(segments)

        # 3) Tạo phụ đề .srt
        srt_path = write_srt(vi_segments, work / "vi.srt")

        # 4) Lồng tiếng
        total = self.compositor.video_duration(item.path)
        synth = Synthesizer(
            voice=cfg.tts.get("voice", "vi-VN-HoaiMyNeural"),
            rate=cfg.tts.get("rate", "+0%"),
            work_dir=work,
        )
        vi_voice = synth.synthesize(vi_segments, total)

        # 5) Ghép video cuối
        out_path = cfg.output_dir / f"{item.id}_vi.mp4"
        self.compositor.compose(
            video=item.path,
            vi_voice=vi_voice,
            srt=srt_path if cfg.compose.get("burn_subtitles") else None,
            out_path=out_path,
        )

        # 6) Đăng
        uploaded = self._upload(item, out_path)

        self.state.mark(
            item.id,
            {
                "title": item.title,
                "url": item.url,
                "source_lang": src_lang,
                "output": str(out_path),
                "uploaded": uploaded,
            },
        )
        log.info("HOÀN TẤT: %s -> %s", item.title, out_path.name)
        return out_path

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

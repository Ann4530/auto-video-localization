"""Điều phối toàn bộ quy trình cho 1 video:
   tải -> bóc lời -> dịch -> phụ đề -> lồng tiếng -> ghép -> đăng.

Mỗi job truyền vào một `JobOptions` (xem src/jobs.py) để tuỳ biến: dịch text/
tiếng/cả 2, chọn giọng, ngôn ngữ đích... Field nào để None sẽ rơi về mặc định
trong config.yaml.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Callable

from .compose.compositor import Compositor
from .compose.subtitles import write_srt
from .config import Config
from .download.downloader import Downloader, VideoItem
from .jobs import JobOptions
from .transcribe.transcriber import BaseTranscriber, make_transcriber
from .translate.translator import BaseTranslator, make_translator
from .tts.synthesizer import Synthesizer
from .upload.dispatch import upload_result
from .utils.logging import get_logger
from .utils.state import State

log = get_logger("pipeline")

# progress(stage_name, percent 0..100)
ProgressCb = Callable[[str, int], None]


def _noop(stage: str, percent: int) -> None:  # callback mặc định
    pass


# Nhãn cấu trúc kịch bản (không phải lời thoại) -> bỏ hoặc cắt phần nhãn.
_SCRIPT_LABEL = re.compile(
    r"^\s*(title|narration|on[-\s]?screen text|on[-\s]?screen|script|scene|hook|"
    r"cta|visual|visuals|sound|music|sfx|note|notes|caption|captions|"
    r"b[-\s]?roll|voiceover|vo|text)\s*\d*\s*[:\-–]\s*",
    re.IGNORECASE,
)


def _clean_script_lines(script: str) -> list[str]:
    """Tách kịch bản thành các câu ĐỌC ĐƯỢC, bỏ rác cấu trúc.

    Bỏ: dòng phân cách (--- *** ===), dòng chỉ có ký hiệu/không có chữ, dòng nhãn
    (TITLE:/Narration:/On-screen text:...). Với dòng 'Nhãn: nội dung' thì cắt nhãn,
    giữ nội dung. Sau đó tách tiếp theo câu để phụ đề ngắn gọn.
    """
    out: list[str] = []
    for raw in script.splitlines():
        s = raw.strip().lstrip("•·-*#> ").strip()
        if not s:
            continue
        # dòng phân cách / chỉ ký hiệu
        if re.fullmatch(r"[-=_*~#•·.\s]+", s):
            continue
        # cắt nhãn đầu dòng nếu có (giữ nội dung phía sau)
        s = _SCRIPT_LABEL.sub("", s).strip()
        if not s or not re.search(r"[A-Za-zÀ-ỹ0-9]", s):
            continue
        # tách theo câu để mỗi phụ đề gọn
        for sent in re.split(r"(?<=[.!?…])\s+", s):
            sent = sent.strip()
            if sent and re.search(r"[A-Za-zÀ-ỹ0-9]", sent):
                out.append(sent)
    return out


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
        self._transcriber: BaseTranscriber | None = None
        # Cache translator theo (provider, model, target_language, style) để
        # job lặp cùng cấu hình dịch dùng lại 1 client.
        self._translators: dict[tuple, BaseTranslator] = {}

    @property
    def transcriber(self) -> BaseTranscriber:
        if self._transcriber is None:
            t = self.cfg.transcribe
            self._transcriber = make_transcriber(
                provider=t.get("provider", "whisper"),
                gemini_key=self.cfg.env("GEMINI_API_KEY"),
                model=t.get("gemini_model", "gemini-2.5-flash"),
                language=t.get("language"),
                whisper_model=t.get("model", "base"),
                device=t.get("device", "auto"),
                compute_type=t.get("compute_type", "int8"),
                cpu_threads=t.get("cpu_threads", 4),
            )
        return self._transcriber

    def _get_translator(self, opts: JobOptions) -> BaseTranslator:
        cfg = self.cfg
        key = (
            opts.provider or cfg.translate.get("provider", "gemini"),
            opts.model or cfg.translate.get("model", ""),
            opts.target_language or cfg.translate.get("target_language", "Tiếng Việt"),
            opts.style or cfg.translate.get("style", "tự nhiên"),
        )
        if key not in self._translators:
            self._translators[key] = make_translator(
                provider=key[0],
                anthropic_key=cfg.env("ANTHROPIC_API_KEY"),
                gemini_key=cfg.env("GEMINI_API_KEY"),
                model=key[1],
                target_language=key[2],
                style=key[3],
            )
        return self._translators[key]

    # ---------------------------------------------------------------

    def process_url(
        self,
        url: str,
        opts: JobOptions,
        progress: ProgressCb | None = None,
        skip_state: bool = False,
    ) -> Path | None:
        """Xử lý 1 URL video đơn lẻ, trả về đường dẫn video kết quả."""
        progress = progress or _noop
        progress("download", 2)
        item = self.downloader.download(url)
        if not skip_state and self.state.is_processed(item.id):
            log.info("Bỏ qua (đã xử lý): %s", item.id)
            progress("done", 100)
            return self.state.get(item.id).get("output")  # type: ignore
        return self._run(item, opts, progress)

    def process_file(
        self,
        file_path: str,
        opts: JobOptions,
        title: str | None = None,
        progress: ProgressCb | None = None,
        skip_state: bool = False,
    ) -> Path | None:
        """Xử lý 1 file video CÓ SẴN trên máy (bỏ qua bước tải).

        Dùng cho nguồn khó tải tự động như Douyin, và cho upload thủ công từ
        web (skip_state=True để cho phép xử lý lại cùng 1 file).
        """
        progress = progress or _noop
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
        if not skip_state and self.state.is_processed(item.id):
            log.info("Bỏ qua (đã xử lý): %s", item.id)
            progress("done", 100)
            return self.state.get(item.id).get("output")  # type: ignore
        return self._run(item, opts, progress)

    def process_script(
        self,
        script: str,
        opts: JobOptions,
        title: str | None = None,
        progress: ProgressCb | None = None,
        skip_state: bool = True,
    ) -> Path | None:
        """Mode LỒNG TIẾNG AI: từ KỊCH BẢN (không có video nguồn).

        Sinh giọng edge-tts -> suy timing -> caption karaoke -> nền AI/màu.
        Không cần whisper nên KHÔNG tốn RAM.
        """
        progress = progress or _noop
        item_id = "voice_" + hashlib.md5(script.encode("utf-8")).hexdigest()[:10]
        return self._create_from_script(item_id, title or "Video lồng tiếng",
                                        script, opts, progress)

    def _run(self, item: VideoItem, opts: JobOptions, progress: ProgressCb) -> Path:
        if opts.mode == "create":
            return self._create_video(item, opts, progress)
        cfg = self.cfg
        work = cfg.work_dir / item.id
        work.mkdir(parents=True, exist_ok=True)

        do_voice = opts.dub
        do_subtitle = opts.subtitles
        do_ocr = opts.ocr_overlay
        out_dir = Path(opts.output_dir) if opts.output_dir else cfg.output_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        log.info("Job: lồng tiếng=%s, phụ đề=%s, OCR=%s, ngôn ngữ=%s",
                 do_voice, do_subtitle, do_ocr, opts.target_language)

        translator = self._get_translator(opts)
        src_lang = ""
        srt_path = None
        vi_voice = None

        if opts.needs_speech:
            if opts.segments_path:
                # Render lại từ bản dịch đã chỉnh sửa -> bỏ qua bóc lời + dịch
                progress("load-edits", 40)
                vi_segments = self._load_segments(Path(opts.segments_path))
                src_lang = "edited"
            else:
                # 1) Bóc lời  2) Dịch
                progress("transcribe", 10)
                segments, src_lang = self.transcriber.transcribe(
                    item.path, language=opts.source_language or None
                )
                progress("translate", 45)
                vi_segments = translator.translate_segments(segments)
            # Luôn lưu bản dịch (để user sửa/render lại sau)
            self._save_segments(item, vi_segments, out_dir)
            if do_subtitle:
                progress("subtitles", 55)
                srt_path = write_srt(vi_segments, work / "vi.srt")
                # xuất kèm transcript ra thư mục output cho tiện
                write_srt(vi_segments, out_dir / f"{item.id}_vi.srt")
            if do_voice:
                progress("dubbing", 65)
                total = self.compositor_duration(item.path)
                synth = Synthesizer(
                    voice=opts.voice or cfg.tts.get("voice", "vi-VN-HoaiMyNeural"),
                    rate=opts.rate or cfg.tts.get("rate", "+0%"),
                    work_dir=work,
                )
                vi_voice = synth.synthesize(vi_segments, total)

        # 4b) OCR chữ trên màn hình -> đè tiếng Việt
        overlays = []
        if do_ocr:
            progress("ocr", 80)
            overlays = self._ocr_overlays(item.path, work, translator, opts)

        # 5) Ghép video cuối
        progress("compose", 92)
        kov = (
            opts.keep_original_volume
            if opts.keep_original_volume is not None
            else cfg.tts.get("keep_original_volume", 0.12)
        )
        compositor = Compositor(cfg.compose, keep_original_volume=kov)
        out_path = out_dir / f"{item.id}_vi.mp4"
        compositor.compose(
            video=item.path,
            vi_voice=vi_voice,
            srt=srt_path,
            out_path=out_path,
            overlays=overlays,
        )

        # 6) Đăng (nếu job yêu cầu, hoặc theo config mặc định)
        progress("upload", 98)
        uploaded = self._upload(item, out_path, opts)

        self.state.mark(
            item.id,
            {
                "title": item.title,
                "url": item.url,
                "target_language": opts.target_language,
                "source_lang": src_lang,
                "output": str(out_path),
                "uploaded": uploaded,
            },
        )
        progress("done", 100)
        log.info("HOÀN TẤT: %s -> %s", item.title, out_path.name)
        return out_path

    def _create_video(self, item: VideoItem, opts: JobOptions,
                      progress: ProgressCb) -> Path:
        """Pipeline TẠO video: footage tự quay -> phụ đề KARAOKE ĐỘNG.

        Không dịch, không lồng tiếng: bóc lời mức TỪ bằng whisper (bắt buộc, vì
        Gemini ASR không cấp word-timestamp) rồi sinh .ass karaoke và burn vào.
        """
        from .compose.captions import write_karaoke_ass
        from .transcribe.transcriber import WhisperTranscriber

        cfg = self.cfg
        work = cfg.work_dir / item.id
        work.mkdir(parents=True, exist_ok=True)
        out_dir = Path(opts.output_dir) if opts.output_dir else cfg.output_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        log.info("Job TẠO video: lang=%s, model=%s, terms=%s",
                 opts.create_lang, opts.create_model, opts.create_terms)

        progress("transcribe", 15)
        segments, lang = self._transcribe_words(
            item.path, opts.create_model or "tiny", opts.create_lang or "vi"
        )
        if not segments:
            raise RuntimeError("Không bóc được lời thoại nào từ footage")
        self._save_segments(item, segments, out_dir)

        progress("captions", 60)
        compositor = Compositor(cfg.compose)
        vw, vh = compositor.video_size(item.path)
        terms = {w for w in (opts.create_terms or []) if w}
        ass = write_karaoke_ass(
            segments, work / "captions.ass",
            video_w=vw, video_h=vh, terms=terms or None,
        )

        progress("compose", 85)
        out_path = out_dir / f"{item.id}_caption.mp4"
        compositor.compose(video=item.path, vi_voice=None, srt=None, ass=ass,
                           out_path=out_path, overlays=[])

        self.state.mark(item.id, {
            "title": item.title, "url": item.url, "mode": "create",
            "source_lang": lang, "output": str(out_path), "uploaded": {},
        })
        progress("done", 100)
        log.info("HOÀN TẤT (tạo): %s -> %s", item.title, out_path.name)
        return out_path

    def _create_from_script(self, item_id: str, title: str, script: str,
                            opts: JobOptions, progress: ProgressCb) -> Path:
        """Lồng tiếng AI: kịch bản -> giọng + caption karaoke trên nền."""
        import json
        import re

        from .compose.captions import write_karaoke_ass

        cfg = self.cfg
        work = cfg.work_dir / item_id
        work.mkdir(parents=True, exist_ok=True)
        out_dir = Path(opts.output_dir) if opts.output_dir else cfg.output_dir
        out_dir.mkdir(parents=True, exist_ok=True)

        # Tách + LÀM SẠCH kịch bản: bỏ dòng phân cách (---), nhãn cấu trúc
        # (TITLE:/Narration:/On-screen text:...) và dòng không có chữ -> edge-tts
        # không đọc được những dòng này, mỗi dòng rác làm chậm ~24s vì retry.
        lines = _clean_script_lines(script)
        if not lines:
            raise RuntimeError("Kịch bản không có câu thoại nào đọc được")
        log.info("Job LỒNG TIẾNG AI: %d dòng đọc được, voice=%s", len(lines), opts.voice)

        progress("tts", 25)
        synth = Synthesizer(
            voice=opts.voice or cfg.tts.get("voice", "vi-VN-HoaiMyNeural"),
            rate=opts.rate or cfg.tts.get("rate", "+0%"),
            work_dir=work,
        )
        # Báo tiến trình mịn trong lúc đọc (25% -> 60%) để không trông như đứng hình.
        def _tts_prog(done: int, n: int) -> None:
            progress("tts", 25 + int(35 * done / max(n, 1)))

        voice_audio, segments, total = synth.synthesize_sequential(
            lines, on_progress=_tts_prog
        )
        if not segments:
            raise RuntimeError("Không tạo được giọng đọc (edge-tts lỗi mạng?)")

        # Lưu segments (để sửa/render lại sau nếu cần).
        (out_dir / f"{item_id}_vi.segments.json").write_text(
            json.dumps([{"start": s.start, "end": s.end, "text": s.text}
                        for s in segments], ensure_ascii=False, indent=1),
            encoding="utf-8",
        )

        progress("captions", 65)
        W, H = 720, 1280   # khung dọc HD nhẹ RAM (1080p hay 'malloc failed' máy yếu)
        terms = {w for w in (opts.create_terms or []) if w}
        ass = write_karaoke_ass(segments, work / "captions.ass",
                                video_w=W, video_h=H, terms=terms or None)

        progress("compose", 85)
        import gc
        gc.collect()   # nhường RAM cho x264 (máy yếu)
        compositor = Compositor(cfg.compose)
        out_path = out_dir / f"{item_id}_voice.mp4"
        compositor.compose_voiceover(voice_audio, ass, out_path,
                                     width=W, height=H, duration=total)

        self.state.mark(item_id, {
            "title": title, "url": "", "mode": "create_voice",
            "output": str(out_path), "uploaded": {},
        })
        progress("done", 100)
        log.info("HOÀN TẤT (lồng tiếng AI): %s -> %s", title, out_path.name)
        return out_path

    def _transcribe_words(self, media: Path, model: str, language: str):
        """Bóc lời MỨC TỪ bằng whisper, có fallback cho máy RAM yếu.

        word_timestamps ngốn RAM gấp ~2x. Nếu hết RAM (mkl_malloc / alloc),
        tự lùi dần model -> 'tiny' với 1 luồng + greedy để chạy được.
        """
        from .transcribe.transcriber import WhisperTranscriber

        t = self.cfg.transcribe
        # Bậc thử: model người dùng chọn -> tiny (nhẹ nhất). Loại trùng, giữ thứ tự.
        chain = list(dict.fromkeys([model, "tiny"]))
        last_err: Exception | None = None
        for i, m in enumerate(chain):
            try:
                tr = WhisperTranscriber(
                    model=m,
                    language=language,
                    device=t.get("device", "auto"),
                    compute_type=t.get("compute_type", "int8"),
                    cpu_threads=2 if i == 0 else 1,
                    word_timestamps=True,
                    beam_size=1,            # greedy -> ít RAM
                )
                segments, lang = tr.transcribe(media)
                if i > 0:
                    log.warning("Đã lùi model whisper xuống '%s' do thiếu RAM", m)
                return segments, lang
            except Exception as e:  # noqa: BLE001
                last_err = e
                msg = str(e).lower()
                if "malloc" in msg or "alloc" in msg or "memory" in msg:
                    log.warning("Whisper '%s' thiếu RAM (%s), thử model nhẹ hơn", m, e)
                    import gc
                    gc.collect()
                    continue
                raise
        raise RuntimeError(f"Bóc lời thất bại (hết RAM kể cả tiny): {last_err}")

    def compositor_duration(self, video: Path) -> float:
        """Lấy thời lượng video (Compositor nhẹ, dựng tạm)."""
        return Compositor(self.cfg.compose).video_duration(video)

    def _save_segments(self, item: VideoItem, segments, out_dir: Path | None = None) -> None:
        import json
        out_dir = out_dir or self.cfg.output_dir
        data = [{"start": s.start, "end": s.end, "text": s.text,
                 **({"rate": s.rate} if getattr(s, "rate", None) else {})}
                for s in segments]
        (out_dir / f"{item.id}_vi.segments.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
        )

    @staticmethod
    def _load_segments(path: Path):
        import json

        from .transcribe.transcriber import Segment
        data = json.loads(path.read_text(encoding="utf-8"))
        return [
            Segment(start=float(d["start"]), end=float(d["end"]),
                    text=str(d.get("text", "")).strip(), rate=d.get("rate"))
            for d in data
            if str(d.get("text", "")).strip()
        ]

    def _ocr_overlays(self, video: Path, work: Path, translator: BaseTranslator,
                      opts: JobOptions) -> list:
        """Chạy OCR + dịch chữ trên màn hình, trả danh sách Overlay."""
        from .ocr.screen_text import ScreenTextTranslator

        o = self.cfg.ocr
        # opts.ocr_all_text=True -> dịch MỌI chữ (kể cả Latin); ngược lại theo config
        only_cjk = o.get("only_cjk", True) and not opts.ocr_all_text
        stt = ScreenTextTranslator(
            translator=translator,
            sample_fps=o.get("sample_fps", 2.0),
            min_score=o.get("min_score", 0.6),
            only_cjk=only_cjk,
            font_path=o.get("font", "C:/Windows/Fonts/arial.ttf"),
            ocr_max_width=o.get("ocr_max_width", 960),
            scene_diff=o.get("scene_diff", 2.5),
        )
        try:
            return stt.process(video, work)
        except Exception as e:  # noqa: BLE001
            log.error("OCR lỗi (bỏ qua overlay): %s", e)
            return []

    def _upload(self, item: VideoItem, video: Path, opts: JobOptions) -> dict:
        """Đăng video. Ưu tiên opts.upload_targets; nếu rỗng thì theo config."""
        cfg = self.cfg
        up = cfg.upload
        # caption: ưu tiên opts.caption -> template config
        if opts.caption:
            caption = opts.caption.format(title=item.title)
        else:
            caption = up.get("caption_template", "{title}").format(title=item.title)

        # targets: ưu tiên opts, fallback các nền tảng bật trong config
        if opts.upload_targets:
            targets = list(opts.upload_targets)
        else:
            targets = [p for p in ("tiktok", "facebook", "instagram") if up.get(p)]

        if not targets:
            return {}
        return upload_result(cfg, video, targets, caption)

    # ---------------------------------------------------------------

    def process_sources(self, opts: JobOptions) -> list[Path]:
        """Quét tất cả nguồn trong config, xử lý video mới với `opts`."""
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
                    out = self.process_url(url, opts)
                    if out:
                        outputs.append(Path(out))
                except Exception as e:  # noqa: BLE001
                    log.error("Lỗi xử lý %s: %s", url, e)
        return outputs

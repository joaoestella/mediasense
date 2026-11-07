"""Speech-to-text with word-level timestamps.

Backends (all optional installs, imported lazily):

* ``faster-whisper`` (default) – local, CPU or GPU, ~99 languages.
* ``whisperx``                 – local, adds forced alignment (tighter word
                                  times) and speaker diarization.
* ``openai``                   – OpenAI-compatible HTTP API (OpenAI, Groq, …).
                                  No local model, needs an API key.
"""

from __future__ import annotations

import math
import os
import platform
import re
import tempfile
from collections.abc import Callable
from pathlib import Path

from .media import extract_audio, probe
from .models import Segment, Transcript, Word, fill_missing_word_times, join_words

BACKENDS = ("faster-whisper", "whisperx", "openai")

# Windows without Developer Mode can't create symlinks; the HF cache still works,
# the warning just confuses people.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

# Whisper tends to "clean up" speech and drop hesitations. Priming it with a
# disfluent prompt in the right language makes it keep them, which is what you
# want when the goal is to cut them out.
VERBATIM_PROMPTS = {
    "pt": "Então, é... tipo, hum, eu acho que, ahn, sabe, né?",
    "en": "Umm, so, uh, I mean, like, hmm, you know, uh...",
    "es": "Eh, pues, este... o sea, mmm, ¿sabes?",
    "fr": "Euh, alors, bah... enfin, hum, tu vois?",
    "de": "Äh, also, ähm... naja, hm, weißt du?",
    "it": "Ehm, allora, cioè... boh, mmm, sai?",
}


class TranscriptionError(RuntimeError):
    pass


def _missing(pkg: str, extra: str) -> TranscriptionError:
    return TranscriptionError(
        f"backend needs '{pkg}'. Install with:  pip install \"mediasense[{extra}]\""
    )


def transcribe(
    path: str | Path,
    backend: str = "faster-whisper",
    model: str | None = None,
    language: str | None = None,
    device: str = "auto",
    verbatim: bool = False,
    diarize: bool = False,
    hf_token: str | None = None,
    progress: Callable[[str], None] | None = None,
    vocabulary: list[str] | str | None = None,
) -> Transcript:
    """Transcribe an audio or video file. ``language=None`` auto-detects.

    ``vocabulary``: names and terms the speaker uses ("Cloudflare, Claude, Next.js").
    They bias the model towards the right spelling of brands and jargon.
    """
    if backend not in BACKENDS:
        raise TranscriptionError(f"unknown backend '{backend}', choose one of {BACKENDS}")
    info = probe(path)
    if not info.has_audio:
        raise TranscriptionError("file has no audio stream")
    say = progress or (lambda _m: None)
    prompt = VERBATIM_PROMPTS.get((language or "")[:2]) if verbatim else None
    if verbatim and prompt is None:
        prompt = VERBATIM_PROMPTS["en"]
    hotwords = _vocab(vocabulary)
    if hotwords:
        # a short "glossary" in the prompt helps every backend; faster-whisper also gets hotwords
        prompt = f"{prompt} {hotwords}." if prompt else f"{hotwords}."

    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "audio.wav"
        say("extracting audio…")
        extract_audio(path, wav)
        peak = _peak_level(wav)
        if peak is not None and peak < SILENT_PEAK:
            say(f"warning: the audio is silent (peak {20 * math.log10(max(peak, 1e-9)):.0f} dBFS). "
                "Check that the right microphone was selected and not muted.")
        if backend == "faster-whisper":
            tr = _faster_whisper(wav, model or "small", language, device, prompt, say, hotwords)
        elif backend == "whisperx":
            tr = _whisperx(wav, model or "small", language, device, prompt, diarize, hf_token, say, hotwords)
        else:
            tr = _openai(wav, model, language, prompt, say)

    credits = drop_subtitle_credits(tr)
    if credits:
        say("removed a subtitle-credit line the model imagined (e.g. 'Amara.org')")
    removed = drop_repetition_loops(tr)
    if removed:
        say(f"warning: dropped {removed} zero-length repeated word(s) — the model got stuck in a loop. "
            "This usually means music/noise instead of speech; try without --verbatim or a smaller model.")
    tr.duration = info.duration
    tr.source = str(path)
    tr.backend = backend
    return tr


# Whisper was trained on subtitles from the web and sometimes "hears" their credit lines in silence or
# music, most often at the very end. Nobody says these, so whole segments made of them are dropped.
_SUBTITLE_CREDITS = re.compile(
    r"(amara\.?\s*org|legendas? (pela|por|da) comunidade|legendado por|legendas? por|tradu[cç][aã]o e legendas"
    r"|subtitles? (by|from)|subtitled by|captions? by|transcri(ption|bed) by|sous-titr(es|age) (r[ée]alis[ée]s? )?par"
    r"|untertitel (im auftrag|von|der)|subt[ií]tulos? (realizados? )?por|sottotitoli (creati )?da"
    r"|字幕|zdf (für|fur) funk)",
    re.IGNORECASE,
)


def drop_subtitle_credits(tr: Transcript) -> int:
    """Remove hallucinated subtitle credits ("Legendas pela comunidade de Amara.org"). Returns words removed."""
    removed = 0
    kept = []
    for seg in tr.segments:
        if _SUBTITLE_CREDITS.search(seg.text) and len(seg.text) < 140:
            removed += len(seg.words) or 1
            continue
        kept.append(seg)
    tr.segments = kept
    return removed


def drop_repetition_loops(tr: Transcript, min_duration: float = 0.02) -> int:
    """Remove Whisper "looping" artifacts: the same word repeated with ~0 s duration.

    Real repeated words ("ah, ah, ah") each take time; a decoding loop stacks
    dozens of copies on one timestamp. Returns how many words were dropped.
    """
    from .edit import normalize

    removed = 0
    kept_segments = []
    for seg in tr.segments:
        kept: list[Word] = []
        for w in seg.words:
            if (kept and w.end - w.start < min_duration
                    and normalize(w.text) == normalize(kept[-1].text)):
                removed += 1
                continue
            kept.append(w)
        if not kept:
            continue
        if len(kept) != len(seg.words):
            seg.words = kept
            seg.text = join_words([w.text for w in kept])
            seg.end = max(kept[-1].end, seg.start)
        kept_segments.append(seg)
    tr.segments = kept_segments
    return removed


# ---------------------------------------------------------------- backends
# CTranslate2 (faster-whisper) 4.x needs CUDA 12 cuBLAS and cuDNN 9 at run time.
_CUDA_LIBS = {
    "Windows": ("cublas64_12.dll", "cudnn_ops64_9.dll"),
    "Linux": ("libcublas.so.12", "libcudnn_ops.so.9"),
}


def _suppress_windows_error_dialogs() -> None:
    """A missing DLL can make Windows show a modal 'not found' box; when mediasense runs inside
    Claude Desktop nobody sees it and the transcription hangs. Fail with an error instead."""
    if platform.system() == "Windows":
        try:
            import ctypes

            SEM_FAILCRITICALERRORS, SEM_NOOPENFILEERRORBOX = 0x0001, 0x8000
            ctypes.windll.kernel32.SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOOPENFILEERRORBOX)
        except Exception:
            pass


def missing_cuda_libs() -> list[str]:
    """CUDA libraries CTranslate2 needs that can't be loaded on this machine ([] = all fine)."""
    names = _CUDA_LIBS.get(platform.system(), ())
    if not names:
        return []
    import ctypes

    _suppress_windows_error_dialogs()
    missing = []
    for name in names:
        try:
            (ctypes.WinDLL if platform.system() == "Windows" else ctypes.CDLL)(name)
        except OSError:
            missing.append(name)
    return missing


def _resolve_device(device: str, say=None) -> tuple[str, str]:
    """-> (device, compute_type) for CTranslate2-based models.

    "auto" only picks the GPU when its CUDA libraries really load; trying and failing later can
    hang (Windows) or waste minutes.
    """
    if device == "auto":
        try:
            import ctranslate2  # type: ignore

            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            device = "cpu"
        if device == "cuda":
            missing = missing_cuda_libs()
            if missing:
                if say:
                    say(f"NVIDIA GPU found but {', '.join(missing)} missing – using the CPU")
                device = "cpu"
    return device, ("float16" if device == "cuda" else "int8")


def load_wav_array(wav: str | Path):
    """16 kHz mono PCM WAV (written by our ffmpeg step) -> float32 numpy array.

    Passing an array to faster-whisper skips its own PyAV-based decoder, which
    breaks whenever PyAV changes its API (newer releases dropped
    ``metadata_errors``). FFmpeg already did the decoding.
    """
    import wave

    import numpy as np

    with wave.open(str(wav), "rb") as fh:
        if fh.getsampwidth() != 2:
            raise TranscriptionError("expected 16-bit PCM audio")
        frames = fh.readframes(fh.getnframes())
        channels = fh.getnchannels()
    audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    return audio


SILENT_PEAK = 10 ** (-60 / 20)  # below -60 dBFS there is nothing a model could hear


def _peak_level(wav: str | Path) -> float | None:
    try:
        audio = load_wav_array(wav)
    except Exception:  # numpy missing (API backend) or odd file: skip the check
        return None
    return float(abs(audio).max()) if len(audio) else 0.0


_GPU_ERROR_HINTS = ("cuda", "cudnn", "cublas", "out of memory", "no kernel image", "libcu")


def _vocab(vocabulary) -> str | None:
    if not vocabulary:
        return None
    items = vocabulary.split(",") if isinstance(vocabulary, str) else list(vocabulary)
    items = [i.strip() for i in items if i and i.strip()]
    return ", ".join(dict.fromkeys(items)) or None


def _faster_whisper(wav, model_name, language, device, prompt, say, hotwords=None) -> Transcript:
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError as e:
        raise _missing("faster-whisper", "whisper") from e
    audio = load_wav_array(wav)
    dev, compute = _resolve_device(device, say)
    try:
        return _faster_whisper_run(WhisperModel, audio, model_name, dev, compute, language, prompt, say, hotwords)
    except Exception as e:  # GPU present but CUDA/cuDNN libraries missing or broken
        if device != "auto" or dev != "cuda" or not any(h in str(e).lower() for h in _GPU_ERROR_HINTS):
            raise
        say(f"GPU failed ({str(e).splitlines()[0][:120]}); retrying on CPU…")
        return _faster_whisper_run(WhisperModel, audio, model_name, "cpu", "int8", language, prompt, say, hotwords)


def _faster_whisper_run(WhisperModel, audio, model_name, dev, compute, language, prompt, say,
                        hotwords=None) -> Transcript:
    say(f"loading model '{model_name}' on {dev} (first run downloads it)…")
    m = WhisperModel(model_name, device=dev, compute_type=compute)
    say(f"transcribing {len(audio) / 16000:.1f}s of audio…")
    kwargs = dict(language=language, word_timestamps=True, vad_filter=True,
                  initial_prompt=prompt, condition_on_previous_text=False)
    if hotwords:
        kwargs["hotwords"] = hotwords
    try:
        segs_iter, info = m.transcribe(audio, **kwargs)
    except TypeError:  # faster-whisper < 1.0.2 has no ``hotwords``; the prompt still carries them
        kwargs.pop("hotwords", None)
        segs_iter, info = m.transcribe(audio, **kwargs)
    segments: list[Segment] = []
    for s in segs_iter:
        words = [
            Word(text=w.word, start=float(w.start), end=float(w.end),
                 confidence=float(getattr(w, "probability", 0.0) or 0.0))
            for w in (s.words or [])
        ]
        words = _clean_words(words)
        if not words:
            continue
        segments.append(Segment(start=float(s.start), end=float(s.end),
                                text=join_words([w.text for w in words]), words=words))
        say(f"  {s.end:7.1f}s  {s.text.strip()[:70]}")
    return Transcript(segments=segments, language=getattr(info, "language", language), model=model_name)


def _whisperx(wav, model_name, language, device, prompt, diarize, hf_token, say, hotwords=None) -> Transcript:
    try:
        import whisperx  # type: ignore
    except ImportError as e:
        raise _missing("whisperx", "whisperx") from e
    if device == "auto":
        try:
            import torch  # type: ignore

            device = "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            device = "cpu"
    compute = "float16" if device == "cuda" else "int8"
    say(f"loading WhisperX '{model_name}' on {device}…")
    asr_options = {k: v for k, v in (("initial_prompt", prompt), ("hotwords", hotwords)) if v} or None
    kwargs = {"compute_type": compute, "language": language}
    if asr_options:
        kwargs["asr_options"] = asr_options
    m = whisperx.load_model(model_name, device, **kwargs)
    audio = whisperx.load_audio(str(wav))
    say(f"transcribing {len(audio) / 16000:.1f}s of audio…")
    result = m.transcribe(audio, batch_size=16 if device == "cuda" else 4)
    lang = result.get("language", language)
    say(f"aligning words ({lang})…")
    align_model, meta = whisperx.load_align_model(language_code=lang, device=device)
    result = whisperx.align(result["segments"], align_model, meta, audio, device,
                            return_char_alignments=False)
    if diarize:
        token = hf_token or os.environ.get("HF_TOKEN")
        if not token:
            raise TranscriptionError("diarization needs a Hugging Face token (--hf-token or HF_TOKEN)")
        say("identifying speakers…")
        try:
            from whisperx.diarize import DiarizationPipeline  # type: ignore
        except ImportError:
            DiarizationPipeline = whisperx.DiarizationPipeline  # type: ignore[attr-defined]
        try:  # whisperx >= 3.4 uses ``token``; older releases ``use_auth_token``
            diar = DiarizationPipeline(token=token, device=device)
        except TypeError:
            diar = DiarizationPipeline(use_auth_token=token, device=device)
        result = whisperx.assign_word_speakers(diar(audio), result)
    return _from_whisperx_result(result, lang, model_name)


def _from_whisperx_result(result: dict, lang: str | None, model_name: str | None) -> Transcript:
    segments: list[Segment] = []
    for s in result.get("segments", []):
        raw = [dict(w) for w in s.get("words", []) if str(w.get("word", "")).strip()]
        if not raw:
            continue
        fill_missing_word_times(raw, float(s.get("start", 0.0)), float(s.get("end", 0.0)))
        words = [
            Word(text=str(w["word"]), start=float(w["start"]), end=float(w["end"]),
                 confidence=w.get("score"), speaker=w.get("speaker") or s.get("speaker"))
            for w in raw
        ]
        words = _clean_words(words)
        segments.append(Segment(start=words[0].start, end=words[-1].end,
                                text=join_words([w.text for w in words]),
                                words=words, speaker=s.get("speaker")))
    return Transcript(segments=segments, language=lang, model=model_name)


def _openai(wav, model_name, language, prompt, say) -> Transcript:
    try:
        from openai import OpenAI  # type: ignore
    except ImportError as e:
        raise _missing("openai", "api") from e
    client = OpenAI()  # honours OPENAI_API_KEY and OPENAI_BASE_URL (Groq etc.)
    model_name = model_name or os.environ.get("MEDIASENSE_API_MODEL", "whisper-1")
    size_mb = os.path.getsize(wav) / 1e6
    if size_mb > 24:
        raise TranscriptionError(
            f"audio is {size_mb:.0f} MB; most APIs cap uploads at 25 MB. "
            "Use a local backend or split the file."
        )
    say(f"uploading to API ({model_name})…")
    with open(wav, "rb") as fh:
        kwargs = dict(model=model_name, file=fh, response_format="verbose_json",
                      timestamp_granularities=["word", "segment"])
        if language:
            kwargs["language"] = language
        if prompt:
            kwargs["prompt"] = prompt
        resp = client.audio.transcriptions.create(**kwargs)
    data = resp.model_dump() if hasattr(resp, "model_dump") else dict(resp)
    return _from_openai_response(data, model_name)


def _from_openai_response(data: dict, model_name: str | None) -> Transcript:
    words = [Word(text=w["word"], start=float(w["start"]), end=float(w["end"]))
             for w in (data.get("words") or [])]
    words = _clean_words(words)
    segments: list[Segment] = []
    api_segments = data.get("segments") or []
    if api_segments:
        wi = 0
        for s in api_segments:
            s_start, s_end = float(s["start"]), float(s["end"])
            chunk = []
            while wi < len(words) and words[wi].start < s_end - 1e-3:
                chunk.append(words[wi])
                wi += 1
            if chunk:
                segments.append(Segment(start=s_start, end=s_end,
                                        text=str(s.get("text", "")).strip() or join_words([w.text for w in chunk]),
                                        words=chunk))
        if wi < len(words) and segments:
            segments[-1].words.extend(words[wi:])
            segments[-1].end = max(segments[-1].end, words[-1].end)
    elif words:
        from .models import regroup

        segments = regroup(words)
    lang = data.get("language")
    return Transcript(segments=segments, language=_lang_code(lang), model=model_name)


_LANG_NAMES = {"english": "en", "portuguese": "pt", "spanish": "es", "french": "fr", "german": "de",
               "italian": "it", "japanese": "ja", "chinese": "zh", "korean": "ko", "russian": "ru",
               "dutch": "nl", "arabic": "ar", "hindi": "hi", "turkish": "tr", "polish": "pl"}


def _lang_code(lang: str | None) -> str | None:
    if not lang:
        return None
    return _LANG_NAMES.get(lang.lower(), lang)


def _clean_words(words: list[Word]) -> list[Word]:
    """Drop empty tokens, enforce start <= end and monotonic times."""
    out: list[Word] = []
    last = 0.0
    for w in words:
        if not w.text.strip():
            continue
        start = max(w.start, last) if out else w.start
        end = max(w.end, start)
        out.append(Word(text=w.text, start=start, end=end, confidence=w.confidence, speaker=w.speaker))
        last = start
    return out

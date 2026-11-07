"""Speech-to-text with word-level timestamps.

Backends (all optional installs, imported lazily):

* ``faster-whisper`` (default) – local, CPU or GPU, ~99 languages.
* ``whisperx``                 – local, adds forced alignment (tighter word
                                  times) and speaker diarization.
* ``openai``                   – OpenAI-compatible HTTP API (OpenAI, Groq, …).
                                  No local model, needs an API key.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from pathlib import Path

from .media import extract_audio, probe
from .models import Segment, Transcript, Word, fill_missing_word_times, join_words

BACKENDS = ("faster-whisper", "whisperx", "openai")

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
        f"backend needs '{pkg}'. Install with:  pip install \"wordcut[{extra}]\""
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
) -> Transcript:
    """Transcribe an audio or video file. ``language=None`` auto-detects."""
    if backend not in BACKENDS:
        raise TranscriptionError(f"unknown backend '{backend}', choose one of {BACKENDS}")
    info = probe(path)
    if not info.has_audio:
        raise TranscriptionError("file has no audio stream")
    say = progress or (lambda _m: None)
    prompt = VERBATIM_PROMPTS.get((language or "")[:2]) if verbatim else None
    if verbatim and prompt is None:
        prompt = VERBATIM_PROMPTS["en"]

    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "audio.wav"
        say("extracting audio…")
        extract_audio(path, wav)
        if backend == "faster-whisper":
            tr = _faster_whisper(wav, model or "small", language, device, prompt, say)
        elif backend == "whisperx":
            tr = _whisperx(wav, model or "small", language, device, prompt, diarize, hf_token, say)
        else:
            tr = _openai(wav, model, language, prompt, say)

    tr.duration = info.duration
    tr.source = str(path)
    tr.backend = backend
    return tr


# ---------------------------------------------------------------- backends
def _resolve_device(device: str) -> tuple[str, str]:
    """-> (device, compute_type) for CTranslate2-based models."""
    if device == "auto":
        try:
            import ctranslate2  # type: ignore

            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            device = "cpu"
    return device, ("float16" if device == "cuda" else "int8")


def _faster_whisper(wav, model_name, language, device, prompt, say) -> Transcript:
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError as e:
        raise _missing("faster-whisper", "whisper") from e
    dev, compute = _resolve_device(device)
    say(f"loading model '{model_name}' on {dev} (first run downloads it)…")
    m = WhisperModel(model_name, device=dev, compute_type=compute)
    say("transcribing…")
    segs_iter, info = m.transcribe(
        str(wav),
        language=language,
        word_timestamps=True,
        vad_filter=True,
        initial_prompt=prompt,
        condition_on_previous_text=False,
    )
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


def _whisperx(wav, model_name, language, device, prompt, diarize, hf_token, say) -> Transcript:
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
    asr_options = {"initial_prompt": prompt} if prompt else None
    kwargs = {"compute_type": compute, "language": language}
    if asr_options:
        kwargs["asr_options"] = asr_options
    m = whisperx.load_model(model_name, device, **kwargs)
    audio = whisperx.load_audio(str(wav))
    say("transcribing…")
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
    model_name = model_name or os.environ.get("WORDCUT_API_MODEL", "whisper-1")
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

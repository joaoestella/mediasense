"""Backend adapters tested against fake model libraries (no downloads)."""

import sys
import types
from types import SimpleNamespace as NS

import pytest

from mediasense import transcribe as tr_mod
from mediasense.transcribe import TranscriptionError, _from_openai_response, _from_whisperx_result, transcribe


@pytest.fixture
def fake_faster_whisper(monkeypatch):
    calls = {}

    class WhisperModel:
        def __init__(self, name, device, compute_type):
            calls["init"] = (name, device, compute_type)

        def transcribe(self, audio, **kw):
            calls["kw"] = kw
            calls["audio"] = audio
            if calls.get("fail_on") == calls["init"][1]:
                raise RuntimeError("Library cudnn_ops64_9.dll is not found or cannot be loaded")
            segs = [
                NS(start=0.0, end=1.0, text=" Olá pessoal",
                   words=[NS(word=" Olá", start=0.05, end=0.45, probability=0.98),
                          NS(word=" pessoal", start=0.5, end=0.95, probability=0.91)]),
                NS(start=2.5, end=4.0, text=" um",
                   words=[NS(word=" um", start=2.55, end=2.5, probability=0.4)]),  # end < start
                NS(start=4.0, end=4.1, text="", words=[]),
            ]
            return iter(segs), NS(language="pt", language_probability=0.99)

    mod = types.ModuleType("faster_whisper")
    mod.WhisperModel = WhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", mod)
    return calls


def test_faster_whisper_adapter(fake_faster_whisper, audio_file):
    t = transcribe(audio_file, language="pt", verbatim=True, device="cpu")
    assert t.language == "pt" and t.backend == "faster-whisper" and t.model == "small"
    assert [w.text for w in t.words()] == [" Olá", " pessoal", " um"]
    assert t.words()[2].end >= t.words()[2].start
    assert len(t.segments) == 2 and t.segments[0].text == "Olá pessoal"
    kw = fake_faster_whisper["kw"]
    assert kw["word_timestamps"] is True and kw["language"] == "pt"
    assert kw["initial_prompt"] == tr_mod.VERBATIM_PROMPTS["pt"]
    assert fake_faster_whisper["init"] == ("small", "cpu", "int8")
    assert abs(t.duration - 6.0) < 0.1
    # audio is handed over already decoded (no PyAV), 16 kHz mono float32
    audio = fake_faster_whisper["audio"]
    assert str(audio.dtype) == "float32" and abs(len(audio) / 16000 - 6.0) < 0.05
    assert 0.4 < float(abs(audio).max()) <= 1.0


def test_gpu_failure_falls_back_to_cpu(fake_faster_whisper, audio_file, monkeypatch):
    monkeypatch.setattr(tr_mod, "_resolve_device", lambda d: ("cuda", "float16"))
    fake_faster_whisper["fail_on"] = "cuda"
    messages = []
    t = transcribe(audio_file, device="auto", progress=messages.append)
    assert fake_faster_whisper["init"] == ("small", "cpu", "int8")
    assert any("retrying on CPU" in m for m in messages)
    assert len(t.words()) == 3


def test_explicit_gpu_failure_is_not_hidden(fake_faster_whisper, audio_file, monkeypatch):
    monkeypatch.setattr(tr_mod, "_resolve_device", lambda d: ("cuda", "float16"))
    fake_faster_whisper["fail_on"] = "cuda"
    with pytest.raises(RuntimeError, match="cudnn"):
        transcribe(audio_file, device="cuda")


def test_missing_backend_message(monkeypatch, audio_file):
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    with pytest.raises(TranscriptionError, match=r"mediasense\[whisper\]"):
        transcribe(audio_file)


def test_unknown_backend(audio_file):
    with pytest.raises(TranscriptionError):
        transcribe(audio_file, backend="nope")


def test_whisperx_result_conversion():
    result = {"segments": [{
        "start": 1.0, "end": 3.0, "speaker": "SPEAKER_00",
        "words": [{"word": "Em", "start": 1.0, "end": 1.2, "score": 0.9, "speaker": "SPEAKER_00"},
                  {"word": "2024"},  # whisperx leaves numbers untimed
                  {"word": "foi", "start": 2.0, "end": 2.3, "score": 0.8}],
    }]}
    t = _from_whisperx_result(result, "pt", "small")
    ws = t.words()
    assert [w.text for w in ws] == ["Em", "2024", "foi"]
    assert ws[1].start == 1.2 and ws[1].end == 2.0
    assert ws[0].speaker == "SPEAKER_00" and ws[2].speaker == "SPEAKER_00"
    assert t.segments[0].text == "Em 2024 foi"


def test_openai_response_conversion():
    data = {
        "language": "english",
        "words": [{"word": "Hello", "start": 0.0, "end": 0.4}, {"word": "there", "start": 0.5, "end": 0.8},
                  {"word": "General", "start": 1.5, "end": 1.9}],
        "segments": [{"start": 0.0, "end": 1.0, "text": " Hello there."},
                     {"start": 1.4, "end": 2.0, "text": " General."}],
    }
    t = _from_openai_response(data, "whisper-1")
    assert t.language == "en"
    assert [len(s.words) for s in t.segments] == [2, 1]
    assert t.segments[0].text == "Hello there."


def test_warns_when_audio_is_silent(fake_faster_whisper, tmp_path):
    import subprocess

    silent = tmp_path / "silent.m4a"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                    "-t", "3", "-c:a", "aac", str(silent)], check=True)
    messages = []
    transcribe(silent, progress=messages.append)
    assert any("audio is silent" in m for m in messages)


def test_no_silence_warning_for_real_audio(fake_faster_whisper, audio_file):
    messages = []
    transcribe(audio_file, progress=messages.append)
    assert not any("silent" in m for m in messages)


def test_drop_repetition_loops():
    from mediasense.models import Segment, Transcript, Word
    from mediasense.transcribe import drop_repetition_loops

    words = [Word(" ahn,", 1.84, 2.08), Word(" ahn,", 2.08, 2.32), Word(" ahn,", 2.5, 2.88)]
    words += [Word(" ahn,", 3.44, 3.44) for _ in range(60)] + [Word(" ah", 3.62, 3.62), Word(" fim", 3.7, 3.9)]
    tr = Transcript(segments=[Segment(1.84, 3.9, "x", words)])
    assert drop_repetition_loops(tr) == 60
    assert [w.text.strip() for w in tr.words()] == ["ahn,", "ahn,", "ahn,", "ah", "fim"]
    # genuine repetitions that take time are kept
    ahs = [Word(" ah", 0, 0.3), Word(" ah", 0.4, 0.7), Word(" ah", 0.8, 1.1)]
    real = Transcript(segments=[Segment(0, 2, "x", ahs)])
    assert drop_repetition_loops(real) == 0


def test_drop_subtitle_credits():
    from mediasense.models import Segment, Transcript, Word
    from mediasense.transcribe import drop_subtitle_credits

    def seg(text, start):
        return Segment(start, start + 1, text, [Word(" " + w, start, start + 0.2) for w in text.split()])

    tr = Transcript(segments=[seg("por isso que eu comprei só 100 ações agora", 57.0),
                              seg("Legendas pela comunidade de Amara.org", 59.4),
                              seg("Subtitles by the Amara.org community", 60.5),
                              seg("Sous-titres réalisés par la communauté d'Amara.org", 61.5)])
    assert drop_subtitle_credits(tr) > 0
    assert [s.text for s in tr.segments] == ["por isso que eu comprei só 100 ações agora"]
    # real speech that merely mentions subtitles stays
    keep = Transcript(segments=[seg("ativa as legendas aí embaixo e me segue", 1.0),
                                seg("obrigado por assistir", 5.0)])
    assert drop_subtitle_credits(keep) == 0 and len(keep.segments) == 2

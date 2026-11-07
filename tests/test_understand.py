"""Links, the one-call `understand`, vocabulary hints and adaptive storyboards."""

import json
import shutil
import subprocess
import sys
import types

import pytest

from mediasense import api, fetch
from mediasense.cli import main

TIKTOK_ERROR = "ERROR: [TikTok] 77: Unexpected response from webpage request; please report this issue"


# ------------------------------------------------------------------ links
def test_is_url():
    assert fetch.is_url("https://www.tiktok.com/@x/video/1")
    assert fetch.is_url("HTTP://youtu.be/abc")
    assert not fetch.is_url("C:\\Users\\me\\video.mp4")
    assert not fetch.is_url("~/v.mp4")


@pytest.fixture
def fake_ytdlp(monkeypatch, video_file):
    calls = {"downloads": 0}

    class YoutubeDL:
        def __init__(self, opts):
            calls["opts"] = opts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            info = {"id": "7350", "extractor": "TikTok", "title": "Comenta 123", "uploader": "creator",
                    "description": "#claude #cloudflare", "webpage_url": url}
            if download:
                calls["downloads"] += 1
                home = calls["opts"]["paths"]["home"]
                dst = f"{home}/TikTok_7350.mp4"
                shutil.copy(video_file, dst)
                info["requested_downloads"] = [{"filepath": dst}]
            return info

    mod = types.ModuleType("yt_dlp")
    mod.YoutubeDL = YoutubeDL
    monkeypatch.setitem(sys.modules, "yt_dlp", mod)
    return calls


def test_fetch_downloads_once_and_keeps_metadata(fake_ytdlp, tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIASENSE_HOME", str(tmp_path))
    p1 = fetch.fetch("https://www.tiktok.com/@x/video/7350")
    p2 = fetch.fetch("https://www.tiktok.com/@x/video/7350")
    assert p1 == p2 and p1.name == "TikTok_7350.mp4" and fake_ytdlp["downloads"] == 1
    assert fetch.read_info(p1)["title"] == "Comenta 123"
    assert fake_ytdlp["opts"]["merge_output_format"] == "mp4"


def test_fetch_retries_as_browser(fake_ytdlp, tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIASENSE_HOME", str(tmp_path))
    monkeypatch.setattr(fetch, "_attempts", lambda: [("direct", {}), ("as Chrome", {"impersonate": "chrome"})])
    real = sys.modules["yt_dlp"].YoutubeDL

    class Picky(real):
        def extract_info(self, url, download=False):
            if "impersonate" not in fake_ytdlp["opts"]:
                raise RuntimeError(TIKTOK_ERROR)
            return super().extract_info(url, download)

    sys.modules["yt_dlp"].YoutubeDL = Picky
    p = fetch.fetch("https://www.tiktok.com/@x/video/7350")
    assert p.exists() and fake_ytdlp["opts"]["impersonate"] == "chrome"


def test_fetch_error_is_readable(fake_ytdlp, tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIASENSE_HOME", str(tmp_path))
    monkeypatch.setattr(fetch, "_attempts", lambda: [("direct", {})])

    class Broken(sys.modules["yt_dlp"].YoutubeDL):
        def extract_info(self, url, download=False):
            raise RuntimeError(TIKTOK_ERROR)

    sys.modules["yt_dlp"].YoutubeDL = Broken
    with pytest.raises(fetch.FetchError) as e:
        fetch.fetch("https://www.tiktok.com/@x/video/77")
    msg = str(e.value)
    assert "Unexpected response from webpage request" in msg and "please report" not in msg
    assert "MEDIASENSE_COOKIES_FROM_BROWSER" in msg


def test_fetch_without_ytdlp(monkeypatch):
    monkeypatch.setitem(sys.modules, "yt_dlp", None)
    with pytest.raises(fetch.FetchError, match=r"mediasense\[url\]"):
        fetch.fetch("https://youtu.be/x")


def test_resolve_missing_file():
    with pytest.raises(FileNotFoundError):
        fetch.resolve("/nope/missing.mp4")


# ------------------------------------------------------------------ understand
@pytest.fixture
def fake_transcribe(monkeypatch, transcript):
    seen = {}

    def _fake(path, **kw):
        seen.update(kw)
        seen["calls"] = seen.get("calls", 0) + 1
        return transcript

    import mediasense.transcribe as tr_mod

    monkeypatch.setattr(tr_mod, "transcribe", _fake)
    return seen


def test_understand_link_end_to_end(fake_ytdlp, fake_transcribe, tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIASENSE_HOME", str(tmp_path))
    data = api.understand("https://www.tiktok.com/@x/video/7350", language="pt", vocabulary=["Cloudflare"])
    assert data["metadata"]["title"] == "Comenta 123"
    assert fake_transcribe["vocabulary"] == ["Cloudflare"] and fake_transcribe["language"] == "pt"
    assert len(data["storyboard"]["shots"]) >= 3
    report = api.understand_report(data)
    assert "title: Comenta 123" in report and "## Transcript" in report and "#1 00:02.000" in report
    assert "vamos testar isso." in report
    # second call reuses transcript + storyboard
    api.understand("https://www.tiktok.com/@x/video/7350")
    assert fake_transcribe["calls"] == 1 and fake_ytdlp["downloads"] == 1


def test_understand_audio_only(audio_file, fake_transcribe, tmp_path):
    src = tmp_path / "note.wav"
    shutil.copy(audio_file, src)
    data = api.understand(str(src))
    assert "storyboard" not in data and data["transcript"] is not None
    assert "audio ·" in api.understand_report(data)


def test_cli_understand(video_file, transcript_file, capsys):
    assert main(["understand", str(video_file)]) == 0
    out = capsys.readouterr().out
    assert "## Shots" in out and "Olá, pessoal!" in out


# ------------------------------------------------------------------ vocabulary
def test_vocabulary_reaches_model(monkeypatch, audio_file):
    from types import SimpleNamespace as NS

    from mediasense.transcribe import transcribe

    seen = {}

    class WhisperModel:
        def __init__(self, *a, **k):
            pass

        def transcribe(self, audio, **kw):
            seen.update(kw)
            return iter([NS(start=0, end=1, text=" Cloudflare", words=[NS(word=" Cloudflare", start=0, end=1,
                                                                            probability=0.9)])]), NS(language="pt")

    mod = types.ModuleType("faster_whisper")
    mod.WhisperModel = WhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", mod)
    transcribe(audio_file, device="cpu", vocabulary="Cloudflare, Claude, Cloudflare")
    assert seen["hotwords"] == "Cloudflare, Claude"
    assert "Cloudflare, Claude" in seen["initial_prompt"]


# ------------------------------------------------------------------ adaptive storyboard
def test_vertical_video_gets_denser_storyboard(tmp_path):
    from mediasense.video import storyboard

    v = tmp_path / "vertical.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=360x640:r=25:d=30",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(v)], check=True)
    data = storyboard(v, tmp_path / "sb")
    assert data["vertical"] is True
    gaps = [b["start"] - a["start"] for a, b in zip(data["shots"], data["shots"][1:])]
    assert max(gaps) <= 4.01  # 30 s video -> at least one frame every 4 s


# ------------------------------------------------------------------ MCP
def test_mcp_understand_and_prompts(video_file, transcript_file):
    pytest.importorskip("mcp")
    import asyncio

    from mediasense import mcp_server as s

    out = s.understand_media(str(video_file))
    assert "## Shots" in out[0] and len(out) >= 2  # text + contact sheet image
    prompts = {p.name for p in asyncio.run(s._server.list_prompts())}
    assert {"summarize", "explain_video", "clean_up", "subtitles"} <= prompts
    assert "understand" in s.summarize("https://youtu.be/x")
    assert json.dumps(s.clean_up("a.mp4"))  # plain string

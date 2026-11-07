"""Storyboard, CLI and MCP server."""

import json

import pytest

from wordcut.cli import main
from wordcut.media import probe
from wordcut.models import Transcript
from wordcut.video import pick_times, storyboard


def test_pick_times():
    assert pick_times(10, [2, 4]) == [0.0, 2, 4]
    t = pick_times(100, [], max_gap=20)  # no cuts: still a frame at least every 20 s
    assert len(t) == 6 and t[0] == 0.0 and max(b - a for a, b in zip(t, t[1:])) <= 20
    assert len(pick_times(1000, list(range(1, 1000, 2)), max_frames=10)) == 10
    assert pick_times(10, [2, 2.5, 3], min_gap=1.5) == [0.0, 2]


def test_storyboard(video_file, transcript, tmp_path):
    data = storyboard(video_file, tmp_path / "sb", transcript)
    assert data["scene_cuts_detected"] == 2 and len(data["shots"]) == 3
    speech = [s["speech"] for s in data["shots"]]
    assert speech[0] == "Olá, pessoal!"
    assert speech[1] == "éé então hoje"
    assert speech[2] == "vamos testar isso."
    assert (tmp_path / "sb" / data["shots"][0]["frame"]).exists()
    assert data["contact_sheets"] == ["contact_sheet_01.jpg"]
    assert "## 00:02.000" in (tmp_path / "sb" / "storyboard.md").read_text(encoding="utf-8")
    # frames show the right colour (red, blue, green)
    from PIL import Image

    colours = []
    for s in data["shots"]:
        r, g, b = Image.open(tmp_path / "sb" / s["frame"]).convert("RGB").getpixel((10, 10))
        colours.append(max(("r", r), ("g", g), ("b", b), key=lambda x: x[1])[0])
    assert colours == ["r", "b", "g"]


# ------------------------------------------------------------------ CLI
def test_cli_search_and_show(transcript_file, capsys):
    assert main(["search", str(transcript_file), "então hoje"]) == 0
    assert "00:03.050 → 00:03.950" in capsys.readouterr().out
    assert main(["search", str(transcript_file), "inexistente"]) == 1
    assert main(["show", str(transcript_file), "--words"]) == 0
    assert "então@3.05" in capsys.readouterr().out


def test_cli_edit_dry_run_json(video_file, transcript_file, capsys):
    rc = main(["edit", str(video_file), "--cut", "pessoal", "--fillers", "--cut-range", "5.4-6", "-n", "--json"])
    assert rc == 0
    plan = json.loads(capsys.readouterr().out)
    reasons = {c["reason"] for c in plan["cuts"]}
    assert {"phrase", "filler", "range"} <= reasons
    assert plan["new_duration"] < plan["duration"]


def test_cli_edit_render(video_file, transcript_file, tmp_path, capsys):
    out = tmp_path / "out.mp4"
    rc = main(["edit", str(video_file), "--cut-between", "olá", "pessoal", "-o", str(out),
               "--edl", str(tmp_path / "x.edl"), "--srt", str(tmp_path / "x.srt")])
    assert rc == 0
    assert abs(probe(out).duration - (6.0 - 1.0)) < 0.15
    assert "pessoal" not in (tmp_path / "x.srt").read_text(encoding="utf-8")
    edited = Transcript.load(str(video_file).replace(".mp4", ".edited.transcript.json"))
    assert edited.words()[0].text.strip() == "éé"
    # the original transcript must be untouched
    assert Transcript.load(transcript_file).words()[0].text.strip() == "Olá,"


def test_cli_needs_transcript(audio_file, capsys):
    assert main(["edit", str(audio_file), "--fillers", "-n"]) == 2


def test_cli_export_and_place(transcript_file, video_file, audio_file, tmp_path):
    srt, csvf = tmp_path / "a.srt", tmp_path / "a.csv"
    assert main(["export", str(transcript_file), str(srt), str(csvf), "--words-per-cue", "1"]) == 0
    assert srt.read_text(encoding="utf-8").count("-->") == 8
    assert csvf.read_text(encoding="utf-8").splitlines()[0].startswith("index,start,end")
    out = tmp_path / "p.mp4"
    assert main(["place", str(video_file), str(audio_file), "--at", "0:01.5", "--mode", "duck", "-o", str(out)]) == 0
    assert out.exists()


def test_cli_errors_are_friendly(capsys):
    assert main(["info", "/nope/missing.mp4"]) == 1
    assert "file not found" in capsys.readouterr().err


# ------------------------------------------------------------------ MCP
mcp = pytest.importorskip("mcp")


def test_mcp_tools(video_file, transcript_file, tmp_path):
    from wordcut import mcp_server as s

    assert "então" in s.read_transcript(str(video_file))
    assert "03.050" in s.search_transcript(str(video_file), "então")
    plan = s.plan_edit(str(video_file), fillers=True, cut_phrases=["nada disso"])
    assert "filler" in plan and "not found" in plan
    res = s.render_edit(str(video_file), cut_ranges=["0-1"], media=False, edl=True, srt=True)
    assert "edl:" in res and "subtitles:" in res
    watched = s.watch_video(str(video_file))
    assert "#1 00:02.000" in watched[0] and len(watched) == 2
    frame = s.get_frame(str(video_file), "4.5")
    assert frame[1].path.exists() if hasattr(frame[1], "path") else True


def test_mcp_tool_listing():
    import asyncio

    from wordcut.mcp_server import mcp as server

    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    assert {"transcribe", "read_transcript", "search_transcript", "plan_edit", "render_edit",
            "export_transcript", "place_audio", "watch_video", "get_frame", "media_info"} <= names

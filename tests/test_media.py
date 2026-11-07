import xml.etree.ElementTree as ET
from fractions import Fraction

import pytest

from wordcut import api, export
from wordcut.edit import PlanBuilder
from wordcut.media import detect_scenes, detect_silences, place_audio, probe, render_keep


def test_probe(video_file, audio_file):
    v = probe(video_file)
    assert v.has_video and v.has_audio and v.width == 320 and abs(v.fps - 25) < 0.01
    assert abs(v.duration - 6.0) < 0.1
    a = probe(audio_file)
    assert not a.has_video and a.has_audio and a.sample_rate == 16000


def test_detect_silences(audio_file):
    sil = detect_silences(audio_file, min_duration=0.25)
    assert len(sil) == 2
    (s1, e1), (s2, e2) = sil
    assert abs(s1 - 1.0) < 0.05 and abs(e1 - 2.5) < 0.05
    assert abs(s2 - 4.0) < 0.05 and abs(e2 - 4.3) < 0.05


def test_detect_scenes(video_file):
    cuts = detect_scenes(video_file)
    assert len(cuts) == 2
    assert abs(cuts[0] - 2.0) < 0.1 and abs(cuts[1] - 4.0) < 0.1


@pytest.mark.parametrize("which", ["audio", "video"])
def test_render_keep_duration(which, audio_file, video_file, tmp_path):
    src = audio_file if which == "audio" else video_file
    out = tmp_path / ("cut.wav" if which == "audio" else "cut.mp4")
    render_keep(src, [(0.0, 1.0), (2.5, 4.0)], out)
    info = probe(out)
    assert abs(info.duration - 2.5) < 0.08
    assert info.has_audio and info.has_video == (which == "video")


def test_render_removes_silence_end_to_end(audio_file, tmp_path):
    b = PlanBuilder(probe(audio_file).duration)
    b.remove_silences(detect_silences(audio_file, min_duration=0.5), keep=0.2)
    plan = b.build()
    out = render_keep(audio_file, plan.keep, tmp_path / "tight.wav")
    assert abs(probe(out).duration - (6.0 - 1.3)) < 0.08
    # the long pause is now 0.2 s; the short 0.3 s one (< 0.5 s threshold) is untouched
    assert [round(e - s, 1) for s, e in detect_silences(out, min_duration=0.15)] == [0.2, 0.3]


@pytest.mark.parametrize("mode", ["mix", "duck", "replace"])
def test_place_audio(mode, video_file, audio_file, tmp_path):
    out = place_audio(video_file, audio_file, tmp_path / f"placed_{mode}.mp4", at=1.0, mode=mode,
                      insert_start=0.0, insert_end=1.0)
    info = probe(out)
    assert info.has_video and info.has_audio
    assert abs(info.duration - 6.0) < 0.1


def test_place_audio_fills_original_silence(audio_file, tmp_path):
    # Original is silent at 1.0–2.5. Put the first second of tone there.
    out = place_audio(audio_file, audio_file, tmp_path / "filled.wav", at=1.2, insert_end=1.0)
    sil = detect_silences(out, min_duration=0.25)
    assert all(not (s < 1.6 < e) for s, e in sil), sil


# ------------------------------------------------------------------ exports
def test_srt_and_vtt(transcript):
    cues = export.subtitle_cues(transcript)
    srt = export.to_srt(cues)
    assert srt.startswith("1\n00:00:00,050 --> 00:00:00,950\nOlá, pessoal!")
    assert export.to_vtt(cues).startswith("WEBVTT")
    short = export.subtitle_cues(transcript, words_per_cue=2)
    assert all(len(c.text.split()) <= 2 for c in short)
    assert len(short) == 5


def test_wrap_long_lines():
    assert "\n" in export._wrap("uma frase bem comprida que não cabe em uma linha só", 30, 2)


def test_edl(video_file):
    info = probe(video_file)
    edl = export.to_edl([(0.0, 1.0), (2.5, 4.0)], info, title="t")
    lines = [ln for ln in edl.splitlines() if ln[:3].isdigit()]
    assert len(lines) == 2
    assert lines[0].split()[-4:] == ["00:00:00:00", "00:00:01:00", "00:00:00:00", "00:00:01:00"]
    assert lines[1].split()[-4:] == ["00:00:02:12", "00:00:04:00", "00:00:01:00", "00:00:02:13"]
    assert "AA/V" in lines[0] and "FROM CLIP NAME: talk.mp4" in edl


def _secs(v: str) -> Fraction:
    return Fraction(v[:-1])


def test_fcpxml_is_valid_and_consistent(video_file):
    info = probe(video_file)
    keep = [(0.0, 1.0), (2.5, 4.0), (4.3, 6.0)]
    xml = export.to_fcpxml(keep, info, markers=[(0.5, "start <&>")])
    root = ET.fromstring(xml.split("\n", 2)[2])  # skip XML decl + doctype
    clips = root.findall(".//spine/asset-clip")
    assert len(clips) == 3
    offset = Fraction(0)
    for c in clips:
        assert _secs(c.get("offset")) == offset
        offset += _secs(c.get("duration"))
    seq = root.find(".//sequence")
    assert _secs(seq.get("duration")) == offset
    assert abs(float(offset) - 4.2) < 0.05
    assert root.find(".//marker").get("value") == "start <&>"
    assert root.find(".//media-rep").get("src").startswith("file://")


def test_apply_plan_writes_everything(video_file, transcript_file, tmp_path):
    t = api.load_transcript_for(video_file)
    plan, info, report = api.plan_edit(video_file, t, fillers=True, silences=0.5)
    assert report["fillers"] == 1 and report["silences"] == 1
    written = api.apply_plan(video_file, plan, info, t, output=tmp_path / "e.mp4", edl=tmp_path / "e.edl",
                             fcpxml=tmp_path / "e.fcpxml", subtitles=tmp_path / "e.srt",
                             transcript_out=tmp_path / "e.transcript.json")
    assert set(written) == {"media", "edl", "fcpxml", "subtitles", "transcript"}
    assert abs(probe(written["media"]).duration - plan.new_duration) < 0.1
    srt = (tmp_path / "e.srt").read_text(encoding="utf-8")
    assert "éé" not in srt and "então" in srt


def test_default_outputs_never_clobber_source(video_file):
    outs = api.outputs_for(video_file)
    assert outs["media"].name == "talk.edited.mp4"
    assert outs["transcript"].name == "talk.edited.transcript.json"
    assert outs["edl"].name == "talk.edited.edl"
    assert video_file not in outs.values()


def test_render_refuses_to_overwrite_source(audio_file):
    from wordcut.media import MediaError

    with pytest.raises(MediaError, match="overwrite"):
        render_keep(audio_file, [(0, 1)], audio_file)

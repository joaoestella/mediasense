"""Exports: subtitles (SRT/VTT), plain text, word lists, and edit decision
lists for video editors (CMX3600 EDL for Premiere/Resolve/Avid, FCPXML for
Final Cut Pro and DaVinci Resolve)."""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from xml.sax.saxutils import quoteattr

from .media import MediaInfo
from .models import Transcript, Word, fmt_time, join_words

Range = tuple[float, float]


# ------------------------------------------------------------- subtitles
@dataclass
class Cue:
    start: float
    end: float
    text: str


def subtitle_cues(
    transcript: Transcript,
    max_chars: int = 42,
    max_lines: int = 2,
    max_duration: float = 6.0,
    words_per_cue: int | None = None,
    min_gap: float = 0.0,
) -> list[Cue]:
    """Split the transcript into readable subtitle cues.

    ``words_per_cue`` gives the short "1–3 words at a time" style used in
    vertical videos; otherwise cues follow line-length rules.
    """
    cues: list[Cue] = []
    for seg in transcript.segments:
        words = seg.words
        if not words:
            if seg.text.strip():
                cues.append(Cue(seg.start, seg.end, seg.text.strip()))
            continue
        cur: list[Word] = []
        for w in words:
            if cur:
                candidate = join_words([x.text for x in cur + [w]])
                too_many = words_per_cue is not None and len(cur) >= words_per_cue
                too_long = words_per_cue is None and (
                    len(candidate) > max_chars * max_lines or w.end - cur[0].start > max_duration
                )
                sentence_end = words_per_cue is None and cur[-1].text.rstrip().endswith((".", "?", "!"))
                if too_many or too_long or sentence_end:
                    cues.append(_cue(cur, max_chars, max_lines))
                    cur = []
            cur.append(w)
        if cur:
            cues.append(_cue(cur, max_chars, max_lines))
    # no overlaps, optional gap
    for a, b in zip(cues, cues[1:]):
        if a.end > b.start - min_gap:
            a.end = max(a.start + 0.05, b.start - min_gap)
    return cues


def _cue(words: list[Word], max_chars: int, max_lines: int) -> Cue:
    text = join_words([w.text for w in words])
    return Cue(words[0].start, words[-1].end, _wrap(text, max_chars, max_lines))


def _wrap(text: str, max_chars: int, max_lines: int) -> str:
    if len(text) <= max_chars or " " not in text:
        return text
    # balanced two-line split near the middle
    if max_lines >= 2:
        mid = len(text) // 2
        left = text.rfind(" ", 0, mid + 1)
        right = text.find(" ", mid)
        cut = min((p for p in (left, right) if p > 0), key=lambda p: abs(p - mid), default=-1)
        if cut > 0:
            return text[:cut].strip() + "\n" + text[cut:].strip()
    return text


def _srt_time(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(cues: Sequence[Cue]) -> str:
    out = []
    for i, c in enumerate(cues, 1):
        out.append(f"{i}\n{_srt_time(c.start)} --> {_srt_time(c.end)}\n{c.text}\n")
    return "\n".join(out)


def to_vtt(cues: Sequence[Cue]) -> str:
    out = ["WEBVTT\n"]
    for c in cues:
        out.append(f"{_srt_time(c.start).replace(',', '.')} --> {_srt_time(c.end).replace(',', '.')}\n{c.text}\n")
    return "\n".join(out)


def to_text(transcript: Transcript, timestamps: bool = True) -> str:
    lines = []
    for s in transcript.segments:
        who = f"{s.speaker}: " if s.speaker else ""
        lines.append(f"[{fmt_time(s.start)}] {who}{s.text.strip()}" if timestamps else f"{who}{s.text.strip()}")
    return "\n".join(lines) + "\n"


def to_words_csv(transcript: Transcript) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["index", "start", "end", "start_tc", "word", "confidence", "speaker"])
    for i, word in enumerate(transcript.iter_words()):
        w.writerow([i, f"{word.start:.3f}", f"{word.end:.3f}", fmt_time(word.start), word.text.strip(),
                    "" if word.confidence is None else f"{word.confidence:.3f}", word.speaker or ""])
    return buf.getvalue()


# ------------------------------------------------------------- timecode
def _fps_fraction(info: MediaInfo) -> Fraction:
    if info.fps_fraction and "/" in info.fps_fraction:
        num, den = info.fps_fraction.split("/")
        try:
            f = Fraction(int(num), int(den))
            if f > 0:
                return f
        except (ValueError, ZeroDivisionError):
            pass
    if info.fps:
        return Fraction(info.fps).limit_denominator(1001)
    return Fraction(30)


def _frames(t: float, fps: Fraction) -> int:
    return int(round(Fraction(t).limit_denominator(1_000_000) * fps))


def _tc(frames: int, base: int) -> str:
    ff = frames % base
    total_s = frames // base
    return f"{total_s // 3600:02d}:{total_s // 60 % 60:02d}:{total_s % 60:02d}:{ff:02d}"


# ------------------------------------------------------------- EDL
def to_edl(keep: Sequence[Range], info: MediaInfo, title: str = "wordcut edit", reel: str = "AX") -> str:
    """CMX3600 EDL (non-drop-frame). Import in Premiere, Resolve, Avid…

    The editor will ask you to relink the clip; point it at the source file.
    """
    fps = _fps_fraction(info)
    base = round(float(fps))
    if info.has_video and info.has_audio:
        track = "AA/V"
    elif info.has_video:
        track = "V"
    else:
        track = "AA"
    clip = Path(info.path).name
    lines = [f"TITLE: {title}", "FCM: NON-DROP FRAME", ""]
    rec = 0
    n = 0
    for a, b in keep:
        src_in, src_out = _frames(a, fps), _frames(b, fps)
        if src_out <= src_in:
            continue
        n += 1
        length = src_out - src_in
        lines.append(
            f"{n:03d}  {reel:<8} {track:<5} C        "
            f"{_tc(src_in, base)} {_tc(src_out, base)} {_tc(rec, base)} {_tc(rec + length, base)}"
        )
        lines.append(f"* FROM CLIP NAME: {clip}")
        lines.append("")
        rec += length
    return "\n".join(lines)


# ------------------------------------------------------------- FCPXML
def _rt(frames: int, fps: Fraction) -> str:
    """Rational time string aligned to the frame grid, e.g. "1001/30000s"."""
    v = Fraction(frames) / fps
    return f"{v.numerator}s" if v.denominator == 1 else f"{v.numerator}/{v.denominator}s"


def to_fcpxml(
    keep: Sequence[Range],
    info: MediaInfo,
    project: str = "wordcut edit",
    markers: Sequence[tuple[float, str]] = (),
) -> str:
    """FCPXML 1.9 with one asset-clip per kept range (Final Cut Pro, DaVinci Resolve).

    ``markers`` are (time-in-edited-timeline, note) pairs.
    """
    fps = _fps_fraction(info)
    frame_dur = 1 / fps
    fd = f"{frame_dur.numerator}/{frame_dur.denominator}s" if frame_dur.denominator != 1 else f"{frame_dur.numerator}s"
    width, height = info.width or 1920, info.height or 1080
    src = Path(info.path).resolve().as_uri()
    name = Path(info.path).stem
    total = _frames(info.duration, fps)

    clips = []
    offset = 0
    for a, b in keep:
        s, e = _frames(a, fps), _frames(b, fps)
        if e <= s:
            continue
        clip_markers = []
        for t, note in markers:
            mf = _frames(t, fps)
            if offset <= mf < offset + (e - s):
                # marker start is in the clip's *source* time
                clip_markers.append(
                    f'          <marker start="{_rt(s + mf - offset, fps)}" duration="{fd}" value={quoteattr(note)}/>'
                )
        body = ("\n" + "\n".join(clip_markers) + "\n        ") if clip_markers else ""
        clips.append(
            f'        <asset-clip ref="r2" offset="{_rt(offset, fps)}" name={quoteattr(name)} '
            f'start="{_rt(s, fps)}" duration="{_rt(e - s, fps)}" tcFormat="NDF">{body}</asset-clip>'
        )
        offset += e - s

    has_v = "1" if info.has_video else "0"
    has_a = "1" if info.has_audio else "0"
    audio_rate = info.sample_rate or 48000
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.9">
  <resources>
    <format id="r1" frameDuration="{fd}" width="{width}" height="{height}"/>
    <asset id="r2" name={quoteattr(name)} start="0s" duration="{_rt(total, fps)}" hasVideo="{has_v}" hasAudio="{has_a}" format="r1" audioSources="1" audioChannels="2" audioRate="{audio_rate}">
      <media-rep kind="original-media" src={quoteattr(src)}/>
    </asset>
  </resources>
  <library>
    <event name="wordcut">
      <project name={quoteattr(project)}>
        <sequence format="r1" duration="{_rt(offset, fps)}" tcStart="0s" tcFormat="NDF" audioLayout="stereo" audioRate="48k">
          <spine>
{chr(10).join(clips)}
          </spine>
        </sequence>
      </project>
    </event>
  </library>
</fcpxml>
"""


def write(path: str | Path, content: str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


__all__ = ["Cue", "subtitle_cues", "to_srt", "to_vtt", "to_text", "to_words_csv", "to_edl", "to_fcpxml",
           "write"]

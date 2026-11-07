"""MCP server: exposes wordcut to Claude Desktop, Claude Code and any MCP client.

Run with ``wordcut mcp`` (stdio). All processing is local.
"""

from __future__ import annotations

import functools
import json
import os
from pathlib import Path

try:  # MCP Python SDK 2.x
    from mcp.server.mcpserver import Image
    from mcp.server.mcpserver import MCPServer as _Server
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # SDK 1.x
    from mcp.server.fastmcp import FastMCP as _Server  # type: ignore
    from mcp.server.fastmcp import Image  # type: ignore
    from mcp.server.fastmcp.exceptions import ToolError  # type: ignore

from . import api
from .edit import find_phrase
from .media import extract_frame, probe
from .media import place_audio as _place_audio
from .models import Transcript, fmt_time, parse_time

INSTRUCTIONS = """\
wordcut turns speech into text with a timestamp for every word, and edits audio/video by text.

Typical flow:
1. transcribe(path) once per file. It writes <file>.transcript.json next to the media; later tools find it
   automatically when you pass the media path.
2. read_transcript / search_transcript to locate what the user is talking about.
3. plan_edit(...) to preview cuts. Show the user the cut list and confirm before render_edit(...).
   render_edit never overwrites the source; it writes <file>.edited.<ext> (plus optional EDL/FCPXML/SRT).
4. watch_video(path) to understand a video: it returns contact sheets of key frames with timestamps plus
   what is said in each shot. Use get_frame(path, time) to look closer at one moment.
5. place_audio(...) to drop a sound/voice clip into a video at a given time (mix, duck or replace).

Paths are on the user's machine; prefer absolute paths. Times accept seconds (75.5) or 1:15.5.
Whisper often drops hesitations; transcribe with verbatim=true when the user wants fillers removed.
"""

_server = _Server("wordcut", instructions=INSTRUCTIONS)


class _Tools:
    """``@mcp.tool()`` that turns expected failures into messages the model can read."""

    def __getattr__(self, name):
        return getattr(_server, name)

    def tool(self, *args, **kwargs):
        register = _server.tool(*args, **kwargs)

        def decorator(fn):
            @functools.wraps(fn)
            def wrapper(*a, **k):
                try:
                    return fn(*a, **k)
                except ToolError:
                    raise
                except (ValueError, FileNotFoundError, RuntimeError, OSError) as e:
                    raise ToolError(str(e)) from e

            register(wrapper)
            return wrapper

        return decorator


mcp = _Tools()


def _path(p: str) -> str:
    return str(Path(os.path.expanduser(p)).resolve())


def _transcript(path: str) -> Transcript:
    p = Path(_path(path))
    if p.suffix == ".json":
        return Transcript.load(p)
    t = api.load_transcript_for(p, required=True)
    assert t is not None
    return t


@mcp.tool()
def media_info(path: str) -> str:
    """Duration, resolution, fps and which streams (audio/video) a file has."""
    return json.dumps(probe(_path(path)).to_dict(), indent=2)


@mcp.tool()
def transcribe(
    path: str,
    language: str | None = None,
    model: str = "small",
    backend: str = "faster-whisper",
    verbatim: bool = False,
    diarize: bool = False,
    force: bool = False,
) -> str:
    """Transcribe audio/video with word-level timestamps (auto-detects language if not given).

    model: tiny | base | small | medium | large-v3 | turbo (bigger = slower, more accurate).
    backend: faster-whisper (default, local) | whisperx (local, tighter word times, speakers) | openai (API).
    verbatim: keep hesitations (um, éé, hum) so they can be removed later.
    diarize: label speakers (whisperx backend + HF_TOKEN env var).
    Reuses an existing <file>.transcript.json unless force=true.
    """
    from .transcribe import transcribe as _tr

    src = _path(path)
    out = api.default_transcript_path(src)
    if out.exists() and not force:
        t = Transcript.load(out)
        note = "(existing transcript reused; pass force=true to redo)"
    else:
        t = _tr(src, backend=backend, model=model, language=language,
                verbatim=verbatim, diarize=diarize)
        t.save(out)
        note = ""
    head = (f"transcript: {out} {note}\nlanguage: {t.language} · words: {len(t.words())} · "
            f"duration: {fmt_time(t.duration or 0)}\n\n")
    return head + api.transcript_excerpt(t, max_chars=12000)


@mcp.tool()
def read_transcript(path: str, start: str | None = None, end: str | None = None, words: bool = False) -> str:
    """Read a transcript (pass the media path or the .transcript.json).

    start/end limit the time range. words=true shows each word as word@seconds.
    """
    t = _transcript(path)
    return api.transcript_excerpt(t, parse_time(start) if start else None,
                                  parse_time(end) if end else None, words=words, max_chars=20000)


@mcp.tool()
def search_transcript(path: str, phrase: str) -> str:
    """Find every time a word/phrase is said (case, accent and punctuation insensitive)."""
    hits = find_phrase(_transcript(path), phrase)
    if not hits:
        return f"'{phrase}' not found."
    return "\n".join(f"{fmt_time(h.start)} → {fmt_time(h.end)}  ({h.start:.2f}s–{h.end:.2f}s)  {h.text}"
                     for h in hits)


def _plan(path, cut_phrases, cut_between, cut_ranges, keep_only, fillers, filler_words,
          silences, silence_keep, silence_source, occurrence):
    src = _path(path)
    t = api.load_transcript_for(src)
    plan, info, report = api.plan_edit(
        src, t,
        cut_phrases=cut_phrases or [],
        cut_between=[(a, b) for a, b in (cut_between or [])],
        cut_ranges=api.split_ranges(cut_ranges or []),
        keep_only=api.parse_range(keep_only) if keep_only else None,
        fillers=fillers, filler_words=filler_words or [],
        silences=silences, silence_keep=silence_keep, silence_source=silence_source,
        occurrence=occurrence,
    )
    return src, t, plan, info, report


@mcp.tool()
def plan_edit(
    path: str,
    cut_phrases: list[str] | None = None,
    cut_between: list[list[str]] | None = None,
    cut_ranges: list[str] | None = None,
    keep_only: str | None = None,
    fillers: bool = False,
    filler_words: list[str] | None = None,
    silences: float | None = None,
    silence_keep: float = 0.2,
    silence_source: str = "audio",
    occurrence: int | None = None,
) -> str:
    """Preview an edit without writing anything. Same arguments as render_edit.

    cut_phrases: remove these phrases (all occurrences, or only `occurrence`, 1-based).
    cut_between: [["from phrase", "to phrase"], ...] removes everything from one to the other, inclusive.
    cut_ranges: ["1:02-1:05.5", "90-95"].  keep_only: "0:30-2:00".
    fillers: remove um/uh/éé/hum/euh/äh…; filler_words adds more ("tipo", "you know").
    silences: shorten pauses longer than N seconds, leaving silence_keep seconds.
    silence_source: "audio" (level based) or "transcript" (gaps between words; better with background music).
    """
    _, _, plan, _, report = _plan(path, cut_phrases, cut_between, cut_ranges, keep_only, fillers,
                                  filler_words, silences, silence_keep, silence_source, occurrence)
    extra = f"\nnot found: {report['not_found']}" if report["not_found"] else ""
    return plan.summary(limit=200) + extra


@mcp.tool()
def render_edit(
    path: str,
    cut_phrases: list[str] | None = None,
    cut_between: list[list[str]] | None = None,
    cut_ranges: list[str] | None = None,
    keep_only: str | None = None,
    fillers: bool = False,
    filler_words: list[str] | None = None,
    silences: float | None = None,
    silence_keep: float = 0.2,
    silence_source: str = "audio",
    occurrence: int | None = None,
    output: str | None = None,
    edl: bool = False,
    fcpxml: bool = False,
    srt: bool = False,
    media: bool = True,
) -> str:
    """Apply an edit (see plan_edit for the cut arguments). Never touches the original.

    Writes <file>.edited.<ext> unless media=false or output is given. edl → Premiere/Resolve/Avid,
    fcpxml → Final Cut/Resolve, srt → subtitles re-timed to the edited version.
    """
    src, t, plan, info, report = _plan(path, cut_phrases, cut_between, cut_ranges, keep_only, fillers,
                                       filler_words, silences, silence_keep, silence_source, occurrence)
    if not plan.cuts:
        return "Nothing to cut." + (f" Not found: {report['not_found']}" if report["not_found"] else "")
    d = api.outputs_for(src)
    written = api.apply_plan(
        src, plan, info, t,
        output=(_path(output) if output else d["media"]) if media else None,
        edl=d["edl"] if edl else None,
        fcpxml=d["fcpxml"] if fcpxml else None,
        subtitles=d["srt"] if srt else None,
        transcript_out=d["transcript"] if t is not None else None,
    )
    lines = [plan.summary(limit=30), "", "written:"] + [f"  {k}: {v}" for k, v in written.items()]
    return "\n".join(lines)


@mcp.tool()
def export_transcript(path: str, formats: list[str], words_per_cue: int | None = None) -> str:
    """Write the transcript as files next to the media. formats: srt, vtt, txt, csv, json.

    words_per_cue (e.g. 2) makes short captions for vertical video.
    """
    t = _transcript(path)
    base = Path(_path(path))
    if base.name.endswith(".transcript.json"):
        stem_name = base.name[: -len(".transcript.json")]
    else:
        stem_name = base.stem
    out = []
    for f in formats:
        p = base.with_name(stem_name + "." + f.lstrip("."))
        api.write_subtitles(t, p, words_per_cue=words_per_cue)
        out.append(str(p))
    return "\n".join(out)


@mcp.tool()
def place_audio(
    base: str,
    insert: str,
    at: str,
    mode: str = "mix",
    volume: float = 1.0,
    duck_db: float = -15.0,
    insert_from: str | None = None,
    insert_to: str | None = None,
    output: str | None = None,
) -> str:
    """Put an audio clip (or a slice of it) into a video/audio starting at `at`.

    mode: mix (both play) | duck (original lowered by duck_db) | replace (original muted meanwhile).
    Video is copied without re-encoding. Writes <base>.placed.<ext> unless output is given.
    """
    b = Path(_path(base))
    out = _path(output) if output else str(b.with_name(b.stem + ".placed" + b.suffix))
    _place_audio(str(b), _path(insert), out, at=parse_time(at), mode=mode, volume=volume, duck_db=duck_db,
                 insert_start=parse_time(insert_from) if insert_from else 0.0,
                 insert_end=parse_time(insert_to) if insert_to else None)
    return f"written: {out}"


@mcp.tool()
def watch_video(path: str, max_frames: int = 24, threshold: float = 0.3, include_images: bool = True):
    """Understand a video: key frames at scene changes + what is said during each shot.

    Returns a shot list (time, speech) and contact-sheet images labelled with shot number and time.
    Transcribe first to get the speech column. Lower threshold = more sensitive scene detection.
    """
    from .video import storyboard

    src = _path(path)
    t = api.load_transcript_for(src)
    out = Path(src).with_name(Path(src).stem + ".storyboard")
    data = storyboard(src, out, t, threshold=threshold, max_frames=max_frames)
    lines = [f"video: {src}", f"duration {fmt_time(data['duration'])} · {data['resolution']} · "
             f"{len(data['shots'])} shots · folder: {out}"]
    if not t:
        lines.append("(no transcript yet — call transcribe(path) to include speech)")
    for s in data["shots"]:
        lines.append(f"#{s['index']} {s['start_tc']}–{s['end_tc']}: {s['speech'] or '—'}")
    result: list = ["\n".join(lines)]
    if include_images:
        for sheet in data["contact_sheets"]:
            result.append(Image(path=str(out / sheet)))
    return result


@mcp.tool()
def get_frame(path: str, time: str, width: int = 1024):
    """Return the video frame at `time` (e.g. "1:23.4") to look at a moment in detail."""
    src = _path(path)
    t = parse_time(time)
    out_dir = Path(src).with_name(Path(src).stem + ".storyboard")
    out_dir.mkdir(exist_ok=True)
    dst = out_dir / f"frame_{t:.2f}.jpg"
    extract_frame(src, t, dst, width)
    return [f"frame at {fmt_time(t)}", Image(path=str(dst))]


def main() -> None:
    _server.run()


if __name__ == "__main__":
    main()

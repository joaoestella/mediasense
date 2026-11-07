"""MCP server: exposes mediasense to Claude Desktop, Claude Code and any MCP client.

Run with ``mediasense mcp`` (stdio). All processing is local.
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

from mcp.types import ToolAnnotations

from . import __version__, api
from .edit import find_phrase
from .fetch import fetch, is_url
from .jobs import manager as jobs
from .media import extract_frame, probe
from .media import place_audio as _place_audio
from .models import Transcript, fmt_time, parse_time

INSTRUCTIONS = """\
mediasense gives you ears and eyes for audio and video on the user's machine: transcripts with a timestamp
for every word, key frames of videos, and editing by text. Every `path`/`source` argument also accepts a
link (TikTok, YouTube, Instagram, X, Vimeo…); it is downloaded once and reused.

- ALWAYS use these tools for video/audio links (TikTok, YouTube, Instagram, X, Vimeo…) instead of fetching the
  page or opening a browser: those sites block bots, and the page has no transcript anyway.
- To summarize / explain / answer questions about a file or link: call understand_media(source). It returns the
  transcript, shot list and contact sheets (key frames labelled with shot number and time) in one go.
- To edit: transcribe (verbatim=true if removing fillers) → plan_edit → show the user the cut list and get
  an OK → render_edit. Outputs are new files (<name>.edited.<ext>); the original is never touched.
- To place a clip: find the moment with search_transcript / understand_media, then place_audio.
- Pass `vocabulary` with brand names and jargon you can infer (title, screen text, the user's words) so
  they are spelled correctly. Fix remaining obvious mis-hearings when you report back.
- Long work (new videos, the first run that downloads the speech model) answers "STILL WORKING — job …".
  Tell the user it's processing, then call check_job(job_id) until the result arrives. Never restart it.
- Times accept seconds (75.5) or m:ss (1:15.5). Quote times to the user as m:ss.
"""

try:  # report our version to clients (SDK 2.x takes it here)
    _server = _Server("mediasense", instructions=INSTRUCTIONS, version=__version__)
except TypeError:  # SDK 1.x
    _server = _Server("mediasense", instructions=INSTRUCTIONS)
    if getattr(_server, "_mcp_server", None) is not None:
        _server._mcp_server.version = __version__


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


def _ann(title: str, read_only: bool, open_world: bool = False, idempotent: bool | None = None) -> dict:
    """Tool title + MCP annotations. No mediasense tool deletes or overwrites user files
    (results always get new names), so destructiveHint is False everywhere."""
    hints = {"readOnlyHint": read_only, "destructiveHint": False, "openWorldHint": open_world}
    if idempotent is not None:
        hints["idempotentHint"] = idempotent
    return {"title": title, "annotations": ToolAnnotations.model_validate(hints)}


_ANN = {
    "check_job": _ann("Check a running job", True, idempotent=True),
    "media_info": _ann("Media file info", True, idempotent=True),
    "transcribe": _ann("Transcribe speech", False, open_world=True, idempotent=True),
    "read_transcript": _ann("Read a transcript", True, idempotent=True),
    "search_transcript": _ann("Search a transcript", True, idempotent=True),
    "plan_edit": _ann("Preview an edit", True, idempotent=True),
    "render_edit": _ann("Render an edit to a new file", False, idempotent=False),
    "export_transcript": _ann("Export subtitles or text", False, idempotent=False),
    "place_audio": _ann("Place audio into a video", False, idempotent=False),
    "watch_video": _ann("Key frames of a video", False, open_world=True, idempotent=True),
    "get_frame": _ann("Get one video frame", False, idempotent=True),
    "understand_media": _ann("Watch and listen to a video or audio", False, open_world=True, idempotent=True),
}


def _path(p: str, download: bool = True) -> str:
    """Local path, or a link (TikTok, YouTube, Instagram…) downloaded on first use.

    ``download=False`` (read-only tools) only accepts links that were already downloaded."""
    if is_url(p):
        return str(fetch(p, download=download))
    return str(Path(os.path.expanduser(p.strip().strip('"'))).resolve())


def _transcript(path: str) -> Transcript:
    p = Path(_path(path, download=False))
    if p.suffix == ".json":
        return Transcript.load(p)
    t = api.load_transcript_for(p, required=True)
    assert t is not None
    return t


# ------------------------------------------------------------------ background jobs
def _wait_seconds() -> float:
    try:
        return max(1.0, min(float(os.environ.get("MEDIASENSE_TOOL_WAIT", "30")), 110.0))
    except ValueError:
        return 30.0


def _background(kind: str, key: str, label: str, work):
    """Run ``work(progress)`` as a job; answer with its result if it finishes within the wait,
    otherwise with a short 'still working' note (clients time out on long tool calls)."""
    job = jobs.submit(kind, key, label, work)
    jobs.wait(job, _wait_seconds())
    return _job_reply(job)


def _job_reply(job):
    if job.status == "done":
        return job.result
    if job.status == "error":
        raise ToolError(f"{job.kind} failed: {job.error}")
    ahead = jobs.queue_position(job) - 1 if job.status == "queued" else 0
    queue = f" It is waiting for {ahead + 1} earlier job(s) to finish." if job.status == "queued" else ""
    return (f"STILL WORKING — job {job.id}: {job.summary()}.{queue}\n"
            f'Call check_job(job_id="{job.id}") to continue; it waits up to {_wait_seconds():.0f}s and returns '
            "the result as soon as it is ready. Tell the user in one line that it's processing (long videos and "
            "the first-ever run, which downloads the speech model, take a few minutes). Don't start the same "
            "request again.")


@mcp.tool(**_ANN["check_job"])
def check_job(job_id: str = ""):
    """Get the result of a long-running mediasense job (understand_media, transcribe, render_edit, watch_video).

    Waits up to ~30s; returns the finished result, or the progress so far plus the same job_id to check again.
    Without job_id, lists jobs that are still running.
    """
    if not job_id:
        active = jobs.active()
        return "\n".join(f"{j.id}: {j.summary()}" for j in active) if active else "No jobs running."
    job = jobs.get(job_id)
    if job is None:
        raise ToolError(f"unknown job {job_id!r} (the server may have restarted) — run the original request "
                        "again; finished work on disk is reused")
    jobs.wait(job, _wait_seconds())
    return _job_reply(job)


@mcp.tool(**_ANN["media_info"])
def media_info(path: str) -> str:
    """Duration, resolution, fps and which streams (audio/video) a file has."""
    return json.dumps(probe(_path(path, download=False)).to_dict(), indent=2)


@mcp.tool(**_ANN["transcribe"])
def transcribe(
    path: str,
    language: str | None = None,
    model: str = "small",
    backend: str = "faster-whisper",
    verbatim: bool = False,
    diarize: bool = False,
    force: bool = False,
    vocabulary: list[str] | None = None,
) -> str:
    """Transcribe audio/video (file path or link) with word-level timestamps; auto-detects language.

    vocabulary: names/jargon likely in the audio (["Cloudflare", "Claude", "Next.js"]) so they are
    spelled right. Pass them whenever you know them (from the user, the title or the screen).

    model: tiny | base | small | medium | large-v3 | turbo (bigger = slower, more accurate).
    backend: faster-whisper (default, local) | whisperx (local, tighter word times, speakers) | openai (API).
    verbatim: keep hesitations (um, éé, hum) so they can be removed later.
    diarize: label speakers (whisperx backend + HF_TOKEN env var).
    Reuses an existing <file>.transcript.json unless force=true.
    """
    from .transcribe import transcribe as _tr

    def work(progress):
        if is_url(path):
            progress("opening the link (downloads the video the first time)…")
        src = _path(path)
        out = api.default_transcript_path(src)
        if out.exists() and not force:
            t = Transcript.load(out)
            note = "(existing transcript reused; pass force=true to redo)"
        else:
            t = _tr(src, backend=backend, model=model, language=language, verbatim=verbatim,
                    diarize=diarize, vocabulary=vocabulary, progress=progress)
            t.save(out)
            note = ""
        head = (f"transcript: {out} {note}\nlanguage: {t.language} · words: {len(t.words())} · "
                f"duration: {fmt_time(t.duration or 0)}\n\n")
        return head + api.transcript_excerpt(t, max_chars=12000)

    key = f"{path}|{language}|{model}|{backend}|{verbatim}|{diarize}|{force}|{vocabulary}"
    return _background("transcribe", key, path, work)


@mcp.tool(**_ANN["read_transcript"])
def read_transcript(path: str, start: str | None = None, end: str | None = None, words: bool = False) -> str:
    """Read a transcript (pass the media path or the .transcript.json).

    start/end limit the time range. words=true shows each word as word@seconds.
    """
    t = _transcript(path)
    return api.transcript_excerpt(t, parse_time(start) if start else None,
                                  parse_time(end) if end else None, words=words, max_chars=20000)


@mcp.tool(**_ANN["search_transcript"])
def search_transcript(path: str, phrase: str) -> str:
    """Find every time a word/phrase is said (case, accent and punctuation insensitive)."""
    hits = find_phrase(_transcript(path), phrase)
    if not hits:
        return f"'{phrase}' not found."
    return "\n".join(f"{fmt_time(h.start)} → {fmt_time(h.end)}  ({h.start:.2f}s–{h.end:.2f}s)  {h.text}"
                     for h in hits)


def _plan(path, cut_phrases, cut_between, cut_ranges, keep_only, fillers, filler_words,
          silences, silence_keep, silence_source, occurrence):
    src = _path(path, download=False)
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


@mcp.tool(**_ANN["plan_edit"])
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


@mcp.tool(**_ANN["render_edit"])
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
    def work(progress):
        progress("planning the edit…")
        src, t, plan, info, report = _plan(path, cut_phrases, cut_between, cut_ranges, keep_only, fillers,
                                           filler_words, silences, silence_keep, silence_source, occurrence)
        if not plan.cuts:
            return "Nothing to cut." + (f" Not found: {report['not_found']}" if report["not_found"] else "")
        progress(f"rendering {len(plan.cuts)} cut(s)…")
        d = api.fresh_outputs_for(src)  # never overwrite an earlier result
        written = api.apply_plan(
            src, plan, info, t,
            output=(api.unique_path(_path(output)) if output else d["media"]) if media else None,
            edl=d["edl"] if edl else None,
            fcpxml=d["fcpxml"] if fcpxml else None,
            subtitles=d["srt"] if srt else None,
            transcript_out=d["transcript"] if t is not None else None,
        )
        lines = [plan.summary(limit=30), "", "written:"] + [f"  {k}: {v}" for k, v in written.items()]
        return "\n".join(lines)

    key = json.dumps([path, cut_phrases, cut_between, cut_ranges, keep_only, fillers, filler_words, silences,
                      silence_keep, silence_source, occurrence, output, edl, fcpxml, srt, media], default=str)
    return _background("render_edit", key, path, work)


@mcp.tool(**_ANN["export_transcript"])
def export_transcript(path: str, formats: list[str], words_per_cue: int | None = None) -> str:
    """Write the transcript as files next to the media. formats: srt, vtt, txt, csv, json.

    words_per_cue (e.g. 2) makes short captions for vertical video.
    """
    t = _transcript(path)
    base = Path(_path(path, download=False))
    if base.name.endswith(".transcript.json"):
        stem_name = base.name[: -len(".transcript.json")]
    else:
        stem_name = base.stem
    out = []
    for f in formats:
        p = api.unique_path(base.with_name(stem_name + "." + f.lstrip(".")))
        api.write_subtitles(t, p, words_per_cue=words_per_cue)
        out.append(str(p))
    return "\n".join(out)


@mcp.tool(**_ANN["place_audio"])
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
    b = Path(_path(base, download=False))
    out = str(api.unique_path(_path(output) if output else b.with_name(b.stem + ".placed" + b.suffix)))
    _place_audio(str(b), _path(insert, download=False), out, at=parse_time(at), mode=mode, volume=volume,
                 duck_db=duck_db, insert_start=parse_time(insert_from) if insert_from else 0.0,
                 insert_end=parse_time(insert_to) if insert_to else None)
    return f"written: {out}"


@mcp.tool(**_ANN["watch_video"])
def watch_video(path: str, max_frames: int = 24, threshold: float | None = None, include_images: bool = True):
    """Understand a video: key frames at scene changes + what is said during each shot.

    Returns a shot list (time, speech) and contact-sheet images labelled with shot number and time.
    Transcribe first to get the speech column. Lower threshold = more sensitive scene detection.
    """
    from .video import storyboard

    def work(progress):
        if is_url(path):
            progress("opening the link (downloads the video the first time)…")
        src = _path(path)
        t = api.load_transcript_for(src)
        out = Path(src).with_name(Path(src).stem + ".storyboard")
        progress("picking key frames…")
        data = storyboard(src, out, t, threshold=threshold, max_frames=max_frames)
        lines = [f"video: {src}", f"duration {fmt_time(data['duration'])} · {data['resolution']} · "
                 f"{len(data['shots'])} shots · folder: {out}"]
        if not t:
            lines.append("(no transcript yet — call transcribe(path) to include speech)")
        for sh in data["shots"]:
            lines.append(f"#{sh['index']} {sh['start_tc']}–{sh['end_tc']}: {sh['speech'] or '—'}")
        result: list = ["\n".join(lines)]
        if include_images:
            for sheet in data["contact_sheets"]:
                result.append(Image(path=str(out / sheet)))
        return result

    return _background("watch_video", f"{path}|{max_frames}|{threshold}|{include_images}", path, work)


@mcp.tool(**_ANN["get_frame"])
def get_frame(path: str, time: str, width: int = 1024):
    """Return the video frame at `time` (e.g. "1:23.4") to look at a moment in detail."""
    src = _path(path, download=False)
    t = parse_time(time)
    out_dir = Path(src).with_name(Path(src).stem + ".storyboard")
    out_dir.mkdir(exist_ok=True)
    dst = out_dir / f"frame_{t:.2f}.jpg"
    extract_frame(src, t, dst, width)
    return [f"frame at {fmt_time(t)}", Image(path=str(dst))]


@mcp.tool(**_ANN["understand_media"])
def understand_media(
    source: str,
    language: str | None = None,
    vocabulary: list[str] | None = None,
    model: str = "small",
    max_frames: int = 24,
    include_images: bool = True,
    force: bool = False,
):
    """Watch and listen to a video or audio – a TikTok, YouTube, Instagram, X or Vimeo link, or a file –
    to summarize it, explain it or answer questions about it. Use this instead of opening the link in a
    browser or fetching the page (those sites block that). One call downloads the link, transcribes the
    speech with timestamps and returns key-frame contact sheets with what is said in each shot, plus the
    post's title/description. Long videos reply "STILL WORKING" – then call check_job.

    vocabulary: brand names / jargon to spell right. language: ISO code if known (pt, en…).
    """

    def work(progress):
        data = api.understand(source, language=language, model=model, vocabulary=vocabulary,
                              max_frames=max_frames, force=force, progress=progress)
        out: list = [api.understand_report(data)]
        sb = data.get("storyboard")
        if include_images and sb:
            for sheet in sb.get("contact_sheets", []):
                out.append(Image(path=str(Path(data["storyboard_dir"]) / sheet)))
        return out

    key = f"{source}|{language}|{model}|{max_frames}|{include_images}|{force}|{vocabulary}"
    return _background("understand_media", key, source, work)


# ------------------------------------------------------------------ prompts
# Show up as slash commands, e.g. /mcp__mediasense__summarize in Claude Code.
@_server.prompt()
def summarize(source: str) -> str:
    """Summarize an audio/video file or link (TikTok, YouTube, voice note…)."""
    return (f"Use the mediasense `understand_media` tool on: {source}\n"
            "Then summarize it in my language: a one-line gist, the key points with timestamps (m:ss), "
            "any steps/instructions as a numbered list, links or names mentioned, and the call to action "
            "if there is one. Correct obvious transcription mistakes in names using the title, "
            "description and what is visible on screen.")


@_server.prompt()
def explain_video(source: str, question: str = "") -> str:
    """Explain what happens in a video, shot by shot (optionally answer a question about it)."""
    ask = f" Then answer: {question}" if question else ""
    return (f"Use the mediasense `understand_media` tool on: {source}\n"
            "Look at the contact sheets and the speech for each shot, and explain the video chronologically "
            "with timestamps: what is shown, what is said, and what the creator is trying to achieve "
            "(structure, hook, persuasion techniques). Use get_frame to read on-screen text you can't make "
            f"out.{ask}")


@_server.prompt()
def clean_up(source: str) -> str:
    """Remove filler words and long pauses from a recording (preview first, then render)."""
    return (f"Clean up this recording with mediasense: {source}\n"
            "1. transcribe it with verbatim=true (reuse an existing transcript only if it was verbatim).\n"
            "2. plan_edit with fillers=true and silences=0.7, and show me the cut list in plain words "
            "(how many fillers, how many pauses, old → new length).\n"
            "3. Wait for my OK, then render_edit with the same options plus srt=true, and tell me where the "
            "files are.")


@_server.prompt()
def subtitles(source: str, style: str = "standard") -> str:
    """Make subtitles for a video ('standard' or 'short' 1–3 word captions for vertical video)."""
    wpc = "words_per_cue=2" if style.lower().startswith("short") else "the default layout"
    return (f"Transcribe {source} with mediasense (pass vocabulary for any names you can infer), "
            f"check the transcript for obvious mistakes, then export_transcript as srt using {wpc}. "
            "Tell me where the file is.")


def main() -> None:
    _server.run()


if __name__ == "__main__":
    main()

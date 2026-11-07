"""High-level operations shared by the CLI and the MCP server."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path

from . import export
from .edit import EditPlan, PlanBuilder, remap_transcript
from .media import MediaInfo, detect_silences, probe, render_keep
from .models import Transcript, parse_time

Range = tuple[float, float]


def default_transcript_path(media: str | Path) -> Path:
    p = Path(media)
    return p.with_name(p.stem + ".transcript.json")


def load_transcript_for(media: str | Path | None, transcript: str | Path | None = None,
                        required: bool = False) -> Transcript | None:
    candidates = [Path(transcript)] if transcript else []
    if media is not None and not transcript:
        candidates.append(default_transcript_path(media))
    for c in candidates:
        if c.exists():
            return Transcript.load(c)
    if transcript:
        raise FileNotFoundError(f"transcript not found: {transcript}")
    if required:
        raise FileNotFoundError(
            f"no transcript for {media}; run `mediasense transcribe {media}` first"
        )
    return None


def parse_range(text: str) -> Range:
    """"1:02-1:05.5", "62..65.5" or "62,65.5" -> (62.0, 65.5)."""
    for sep in ("..", ",", "-"):
        if sep in text:
            a, b = text.split(sep, 1)
            return parse_time(a), parse_time(b)
    raise ValueError(f"cannot parse range '{text}' (use START-END, e.g. 1:02-1:05)")


def plan_edit(
    media: str | Path,
    transcript: Transcript | None = None,
    cut_phrases: Iterable[str] = (),
    cut_between: Iterable[tuple[str, str]] = (),
    cut_ranges: Iterable[Range] = (),
    keep_only: Range | None = None,
    fillers: bool = False,
    filler_words: Iterable[str] = (),
    silences: float | None = None,
    silence_keep: float = 0.2,
    silence_source: str = "audio",
    noise_db: float = -35.0,
    occurrence: int | None = None,
    pad: float = 0.12,
) -> tuple[EditPlan, MediaInfo, dict]:
    """Build an edit plan. Returns (plan, media info, report of what matched)."""
    info = probe(media)
    b = PlanBuilder(info.duration, transcript, max_pad=pad)
    report: dict = {"not_found": []}
    for phrase in cut_phrases:
        hits = b.remove_phrase(phrase, occurrence)
        if not hits:
            report["not_found"].append(phrase)
    for start_p, end_p in cut_between:
        if b.remove_between(start_p, end_p) is None:
            report["not_found"].append(f"{start_p} … {end_p}")
    for a, e in cut_ranges:
        b.remove_range(a, e)
    if keep_only:
        b.keep_only(*keep_only)
    if fillers or list(filler_words):
        report["fillers"] = b.remove_fillers(filler_words)
    if silences is not None:
        if silence_source == "transcript":
            gaps = b.transcript_gaps(silences)
        else:
            gaps = detect_silences(media, noise_db=noise_db, min_duration=silences)
        report["silences"] = b.remove_silences(gaps, keep=silence_keep)
    return b.build(), info, report


def apply_plan(
    media: str | Path,
    plan: EditPlan,
    info: MediaInfo,
    transcript: Transcript | None = None,
    output: str | Path | None = None,
    edl: str | Path | None = None,
    fcpxml: str | Path | None = None,
    subtitles: str | Path | None = None,
    transcript_out: str | Path | None = None,
) -> dict[str, str]:
    """Render and/or export. Returns {kind: path} of everything written."""
    written: dict[str, str] = {}
    keep = plan.keep
    if output:
        render_keep(media, keep, output)
        written["media"] = str(output)
    if edl:
        export.write(edl, export.to_edl(keep, info, title=Path(media).stem))
        written["edl"] = str(edl)
    if fcpxml:
        export.write(fcpxml, export.to_fcpxml(keep, info, project=Path(media).stem + " (mediasense)"))
        written["fcpxml"] = str(fcpxml)
    if transcript and (subtitles or transcript_out):
        edited = remap_transcript(transcript, plan)
        if subtitles:
            write_subtitles(edited, subtitles)
            written["subtitles"] = str(subtitles)
        if transcript_out:
            edited.save(transcript_out)
            written["transcript"] = str(transcript_out)
    return written


def write_subtitles(transcript: Transcript, path: str | Path, words_per_cue: int | None = None,
                    max_chars: int = 42) -> Path:
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".srt":
        content = export.to_srt(export.subtitle_cues(transcript, max_chars=max_chars, words_per_cue=words_per_cue))
    elif ext == ".vtt":
        content = export.to_vtt(export.subtitle_cues(transcript, max_chars=max_chars, words_per_cue=words_per_cue))
    elif ext == ".txt":
        content = export.to_text(transcript)
    elif ext == ".csv":
        content = export.to_words_csv(transcript)
    elif ext == ".json":
        return transcript.save(path)
    else:
        raise ValueError(f"unsupported export format '{ext}' (use .srt, .vtt, .txt, .csv or .json)")
    return export.write(path, content)


def outputs_for(media: str | Path, suffix: str = "edited") -> dict[str, Path]:
    p = Path(media)
    base = f"{p.stem}.{suffix}"

    def named(ext: str) -> Path:
        # build names explicitly: Path.with_suffix would swap ".edited" for the extension
        return p.with_name(base + ext)

    return {
        "media": named(p.suffix),
        "edl": named(".edl"),
        "fcpxml": named(".fcpxml"),
        "srt": named(".srt"),
        "transcript": named(".transcript.json"),
    }


def unique_path(path: str | Path) -> Path:
    """``path`` if it doesn't exist yet, else ``name (2).ext``, ``name (3).ext`` …"""
    p = Path(path)
    if not p.exists():
        return p
    stem, ext = (p.name[: -len(".transcript.json")], ".transcript.json") if p.name.endswith(
        ".transcript.json") else (p.stem, p.suffix)
    n = 2
    while (p.with_name(f"{stem} ({n}){ext}")).exists():
        n += 1
    return p.with_name(f"{stem} ({n}){ext}")


def fresh_outputs_for(media: str | Path) -> dict[str, Path]:
    """Like :func:`outputs_for`, but never pointing at files that already exist."""
    n = 1
    while True:
        outs = outputs_for(media, "edited" if n == 1 else f"edited ({n})")
        if not any(p.exists() for p in outs.values()):
            return outs
        n += 1


def transcript_excerpt(t: Transcript, start: float | None = None, end: float | None = None,
                       words: bool = False, max_chars: int = 20000) -> str:
    """Compact, model-friendly view with timestamps."""
    from .models import fmt_time

    lines: list[str] = []
    for s in t.segments:
        if start is not None and s.end < start:
            continue
        if end is not None and s.start > end:
            break
        who = f"{s.speaker}: " if s.speaker else ""
        if words:
            ws = " ".join(f"{w.text.strip()}@{w.start:.2f}" for w in s.words
                          if (start is None or w.end >= start) and (end is None or w.start <= end))
            lines.append(f"[{fmt_time(s.start)}–{fmt_time(s.end)}] {who}{ws}")
        else:
            lines.append(f"[{fmt_time(s.start)}–{fmt_time(s.end)}] {who}{s.text.strip()}")
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n… (truncated; ask for a time range, total {fmt_time(t.duration or 0)})"
    return text


def split_ranges(values: Sequence[str]) -> list[Range]:
    return [parse_range(v) for v in values]


def understand(
    source: str,
    language: str | None = None,
    model: str = "small",
    vocabulary: list[str] | str | None = None,
    max_frames: int = 24,
    backend: str = "faster-whisper",
    force: bool = False,
    progress=None,
) -> dict:
    """Everything a model needs to understand a file or link, in one call:
    metadata, transcript with timestamps and (for video) a storyboard of shots.

    Reuses the transcript/storyboard already on disk unless ``force``.
    """
    from .fetch import is_url, read_info, resolve
    from .transcribe import transcribe

    say = progress or (lambda _m: None)
    if is_url(source):
        say("opening the link (downloads the video the first time)…")
    src = resolve(source)
    info = probe(src)
    meta = read_info(src)
    result: dict = {"path": str(src), "info": info.to_dict(), "metadata": meta}

    transcript = None
    tpath = default_transcript_path(src)
    if info.has_audio:
        if tpath.exists() and not force:
            transcript = Transcript.load(tpath)
        else:
            transcript = transcribe(src, backend=backend, model=model, language=language,
                                    vocabulary=vocabulary, progress=say)
            transcript.save(tpath)
        result["transcript_path"] = str(tpath)
    result["transcript"] = transcript

    if info.has_video:
        from .video import storyboard

        out = Path(src).with_name(Path(src).stem + ".storyboard")
        sb_json = out / "storyboard.json"
        cached = None
        if sb_json.exists() and not force:
            import json

            cached = json.loads(sb_json.read_text(encoding="utf-8"))
            # a storyboard made before the transcript existed has no speech: rebuild it
            if transcript is not None and not any(s.get("speech") for s in cached.get("shots", [])):
                cached = None
        if cached:
            result["storyboard"] = cached
        else:
            say("picking key frames…")
            result["storyboard"] = storyboard(src, out, transcript, max_frames=max_frames)
        result["storyboard_dir"] = str(out)
    return result


def understand_report(result: dict, max_chars: int = 15000) -> str:
    """Model-friendly text version of :func:`understand`."""
    from .models import fmt_time

    info = result["info"]
    lines = [f"file: {result['path']}"]
    kind = "video" if info["has_video"] else "audio"
    extra = f" · {info['width']}x{info['height']}" if info["has_video"] else ""
    lines.append(f"{kind} · {fmt_time(info['duration'])}{extra}")
    meta = result.get("metadata") or {}
    for key in ("title", "uploader", "webpage_url", "upload_date"):
        if meta.get(key):
            lines.append(f"{key}: {meta[key]}")
    if meta.get("description"):
        lines.append(f"description: {str(meta['description'])[:600]}")
    t: Transcript | None = result.get("transcript")
    if t is not None:
        lines += ["", f"## Transcript (language: {t.language}, {len(t.words())} words)",
                  transcript_excerpt(t, max_chars=max_chars)]
    elif not info["has_audio"]:
        lines += ["", "(no audio track)"]
    sb = result.get("storyboard")
    if sb:
        sheets = [str(Path(result["storyboard_dir"]) / name) for name in sb.get("contact_sheets", [])]
        if sheets:
            lines += ["", "## Key frames",
                      "Contact sheets (open these images to see the video; each tile is labelled #shot and time):"]
            lines += [f"- {p}" for p in sheets]
        lines += ["", f"## Shots ({len(sb['shots'])}; contact sheets show #number and start time)"]
        for s in sb["shots"]:
            lines.append(f"#{s['index']} {s['start_tc']}–{s['end_tc']}: {s['speech'] or '—'}")
    return "\n".join(lines)

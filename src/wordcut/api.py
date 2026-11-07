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
            f"no transcript for {media}; run `wordcut transcribe {media}` first"
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
        export.write(fcpxml, export.to_fcpxml(keep, info, project=Path(media).stem + " (wordcut)"))
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

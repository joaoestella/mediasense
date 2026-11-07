"""Thin, dependency-free wrappers around ffmpeg / ffprobe."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

Range = tuple[float, float]

VIDEO_CONTAINERS_H264 = {".mp4", ".mov", ".m4v", ".mkv"}


class MediaError(RuntimeError):
    pass


def _guard_overwrite(src: str | Path, dst: str | Path) -> None:
    """Never write the result on top of an input file."""
    if Path(dst).resolve() == Path(src).resolve():
        raise MediaError(f"refusing to overwrite the source file {src}; choose another output name")


_static_ffmpeg_tried = False


def _try_static_ffmpeg() -> bool:
    """If the optional static-ffmpeg package is installed, fetch FFmpeg binaries (once, ~70 MB) and put
    them on PATH. Used by the one-click Claude Desktop extension, where users may have no FFmpeg."""
    global _static_ffmpeg_tried
    if _static_ffmpeg_tried:
        return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
    _static_ffmpeg_tried = True
    try:
        import contextlib
        import sys

        import static_ffmpeg  # type: ignore
    except ImportError:
        return False
    try:
        # it prints download progress to stdout, which would corrupt the MCP stdio channel
        with contextlib.redirect_stdout(sys.stderr):
            static_ffmpeg.add_paths(weak=True)
    except Exception:
        return False
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def require_ffmpeg() -> None:
    if (shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None) and _try_static_ffmpeg():
        return
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise MediaError(
                f"'{tool}' not found on PATH. Install FFmpeg: https://ffmpeg.org/download.html "
                "(macOS: brew install ffmpeg · Windows: winget install ffmpeg · Linux: apt install ffmpeg)"
            )


def run(cmd: Sequence[str], capture: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(list(cmd), capture_output=capture, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-15:])
        raise MediaError(f"command failed ({proc.returncode}): {' '.join(cmd[:6])} ...\n{tail}")
    return proc


@lru_cache(maxsize=1)
def ffmpeg_major_version() -> int:
    require_ffmpeg()
    out = run(["ffmpeg", "-version"]).stdout
    m = re.search(r"ffmpeg version (?:n)?(\d+)", out)
    return int(m.group(1)) if m else 6


@dataclass
class MediaInfo:
    path: str
    duration: float
    has_video: bool
    has_audio: bool
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    fps_fraction: str | None = None
    sample_rate: int | None = None

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def probe(path: str | Path) -> MediaInfo:
    require_ffmpeg()
    path = str(path)
    if not os.path.exists(path):
        raise MediaError(f"file not found: {path}")
    out = run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path]
    ).stdout
    data = json.loads(out)
    streams = data.get("streams", [])
    v = next(
        (s for s in streams if s.get("codec_type") == "video"
         and not s.get("disposition", {}).get("attached_pic")),
        None,
    )
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = float(data.get("format", {}).get("duration") or 0.0)
    if not duration:
        for s in streams:
            if s.get("duration"):
                duration = max(duration, float(s["duration"]))
    fps = fps_frac = None
    if v:
        fps_frac = v.get("avg_frame_rate") or v.get("r_frame_rate")
        if not fps_frac or fps_frac in ("0/0",):
            fps_frac = v.get("r_frame_rate")
        try:
            num, den = fps_frac.split("/")
            fps = float(num) / float(den) if float(den) else None
        except (ValueError, AttributeError, ZeroDivisionError):
            fps = None
    return MediaInfo(
        path=path,
        duration=duration,
        has_video=v is not None,
        has_audio=a is not None,
        width=int(v["width"]) if v and v.get("width") else None,
        height=int(v["height"]) if v and v.get("height") else None,
        fps=fps,
        fps_fraction=fps_frac,
        sample_rate=int(a["sample_rate"]) if a and a.get("sample_rate") else None,
    )


def extract_audio(src: str | Path, dst: str | Path, sample_rate: int = 16000) -> Path:
    """Decode any audio/video into mono PCM WAV (what speech models expect)."""
    require_ffmpeg()
    run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vn", "-ac", "1",
         "-ar", str(sample_rate), "-c:a", "pcm_s16le", str(dst)])
    return Path(dst)


# ---------------------------------------------------------------- analysis
_SIL_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SIL_END = re.compile(r"silence_end:\s*(-?[\d.]+)")


def detect_silences(path: str | Path, noise_db: float = -35.0, min_duration: float = 0.5) -> list[Range]:
    """Return (start, end) of every silence longer than ``min_duration``."""
    info = probe(path)
    if not info.has_audio:
        raise MediaError("file has no audio stream")
    proc = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn",
                "-af", f"silencedetect=noise={noise_db}dB:d={min_duration}", "-f", "null", "-"])
    ranges: list[Range] = []
    start = None
    for line in proc.stderr.splitlines():
        m = _SIL_START.search(line)
        if m:
            start = max(0.0, float(m.group(1)))
            continue
        m = _SIL_END.search(line)
        if m and start is not None:
            ranges.append((start, float(m.group(1))))
            start = None
    if start is not None:
        ranges.append((start, info.duration))
    return ranges


_PTS_TIME = re.compile(r"pts_time:\s*([\d.]+)")


def detect_scenes(path: str | Path, threshold: float = 0.3) -> list[float]:
    """Timestamps (seconds) where the picture changes a lot (scene cuts)."""
    info = probe(path)
    if not info.has_video:
        raise MediaError("file has no video stream")
    proc = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-an",
                "-vf", f"scale=320:-2,select='gt(scene,{threshold})',showinfo", "-f", "null", "-"])
    times = []
    for line in proc.stderr.splitlines():
        if "Parsed_showinfo" in line:
            m = _PTS_TIME.search(line)
            if m:
                times.append(float(m.group(1)))
    return times


def extract_frame(path: str | Path, t: float, dst: str | Path, width: int = 768) -> Path:
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(path),
         "-frames:v", "1", "-vf", f"scale='min({width},iw)':-2", "-q:v", "3", str(dst)])
    return Path(dst)


# ---------------------------------------------------------------- rendering
def _filter_script_args(script_path: str) -> list[str]:
    if ffmpeg_major_version() >= 7:
        return ["-/filter_complex", script_path]
    return ["-filter_complex_script", script_path]


def _encoder_args(dst: Path, has_video: bool) -> list[str]:
    ext = dst.suffix.lower()
    args: list[str] = []
    if has_video:
        if ext in VIDEO_CONTAINERS_H264:
            args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p"]
        if ext in VIDEO_CONTAINERS_H264 - {".mkv"}:
            args += ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]
    else:
        if ext in (".m4a", ".mp4", ".aac"):
            args += ["-c:a", "aac", "-b:a", "192k"]
        elif ext == ".mp3":
            args += ["-c:a", "libmp3lame", "-q:a", "2"]
    return args


def render_keep(
    src: str | Path,
    keep: Sequence[Range],
    dst: str | Path,
    fade: float = 0.008,
) -> Path:
    """Write a new file that contains only the ``keep`` ranges, joined in order.

    Re-encodes so cuts are sample/frame accurate. A tiny audio fade at every
    join avoids clicks.
    """
    _guard_overwrite(src, dst)
    info = probe(src)
    dst = Path(dst)
    keep = [(max(0.0, a), min(b, info.duration or b)) for a, b in keep if b - a > 0.001]
    if not keep:
        raise MediaError("nothing left to keep after the edit")
    n = len(keep)
    v, a = info.has_video, info.has_audio
    lines: list[str] = []
    if v:
        lines.append(f"[0:v]split={n}" + "".join(f"[vs{i}]" for i in range(n)) + ";")
    if a:
        lines.append(f"[0:a]asplit={n}" + "".join(f"[as{i}]" for i in range(n)) + ";")
    for i, (s, e) in enumerate(keep):
        if v:
            lines.append(f"[vs{i}]trim=start={s:.6f}:end={e:.6f},setpts=PTS-STARTPTS[v{i}];")
        if a:
            length = e - s
            f = min(fade, length / 4)
            chain = f"[as{i}]atrim=start={s:.6f}:end={e:.6f},asetpts=PTS-STARTPTS"
            if f > 0:
                chain += f",afade=t=in:d={f:.4f},afade=t=out:st={length - f:.6f}:d={f:.4f}"
            lines.append(chain + f"[a{i}];")
    pads = "".join((f"[v{i}]" if v else "") + (f"[a{i}]" if a else "") for i in range(n))
    outs = ("[vout]" if v else "") + ("[aout]" if a else "")
    lines.append(f"{pads}concat=n={n}:v={int(v)}:a={int(a)}{outs}")

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as fh:
        fh.write("\n".join(lines))
        script = fh.name
    try:
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src), *_filter_script_args(script)]
        if v:
            cmd += ["-map", "[vout]"]
        if a:
            cmd += ["-map", "[aout]"]
        cmd += _encoder_args(dst, v)
        cmd.append(str(dst))
        run(cmd)
    finally:
        os.unlink(script)
    return dst


def place_audio(
    base: str | Path,
    insert: str | Path,
    dst: str | Path,
    at: float,
    mode: str = "mix",
    volume: float = 1.0,
    duck_db: float = -15.0,
    insert_start: float = 0.0,
    insert_end: float | None = None,
) -> Path:
    """Put ``insert`` (or a slice of it) into ``base`` starting at ``at`` seconds.

    mode:
      mix      – play both
      duck     – lower the original by ``duck_db`` while the insert plays
      replace  – silence the original while the insert plays
    Video (if any) is copied untouched.
    """
    _guard_overwrite(base, dst)
    _guard_overwrite(insert, dst)
    if mode not in ("mix", "duck", "replace"):
        raise MediaError("mode must be mix, duck or replace")
    b = probe(base)
    ins = probe(insert)
    if not ins.has_audio:
        raise MediaError("insert file has no audio")
    seg_end = insert_end if insert_end is not None else ins.duration
    seg_len = max(0.0, seg_end - insert_start)
    end_at = at + seg_len
    delay_ms = int(round(at * 1000))
    dst = Path(dst)
    ins_chain = (f"[1:a]atrim=start={insert_start:.6f}:end={seg_end:.6f},asetpts=PTS-STARTPTS,"
                 f"volume={volume},adelay={delay_ms}:all=1[ins]")
    if b.has_audio:
        if mode == "mix":
            base_chain = "[0:a]anull[orig]"
        else:
            gain = 0 if mode == "replace" else 10 ** (duck_db / 20)
            base_chain = f"[0:a]volume=enable='between(t,{at:.3f},{end_at:.3f})':volume={gain:.4f}[orig]"
        graph = (f"{ins_chain};{base_chain};"
                 f"[orig][ins]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]")
    else:
        graph = f"{ins_chain};[ins]apad[aout]"
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(base), "-i", str(insert), "-filter_complex", graph]
    if b.has_video:
        cmd += ["-map", "0:v:0", "-c:v", "copy"]
    cmd += ["-map", "[aout]", "-t", f"{b.duration:.6f}"]
    ext = dst.suffix.lower()
    if b.has_video and ext in VIDEO_CONTAINERS_H264 - {".mkv"}:
        cmd += ["-c:a", "aac", "-b:a", "192k"]
    elif not b.has_video:
        cmd += _encoder_args(dst, False)
    cmd.append(str(dst))
    run(cmd)
    return dst

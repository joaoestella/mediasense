"""Let a language model "watch" a video: key frames at scene changes, each
paired with what is being said at that moment."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .media import detect_scenes, extract_frame, probe
from .models import Transcript, fmt_time, join_words


@dataclass
class Shot:
    index: int
    start: float
    end: float
    frame: str
    speech: str

    def to_dict(self) -> dict:
        return {"index": self.index, "start": round(self.start, 3), "end": round(self.end, 3),
                "start_tc": fmt_time(self.start), "end_tc": fmt_time(self.end),
                "frame": self.frame, "speech": self.speech}


def pick_times(duration: float, scenes: list[float], max_frames: int = 40,
               min_gap: float = 1.5, max_gap: float = 20.0) -> list[float]:
    """Choose shot start times: every scene cut, plus filler frames so no gap
    exceeds ``max_gap``, thinned so none are closer than ``min_gap`` and the
    total stays under ``max_frames``."""
    pts = sorted({0.0, *[t for t in scenes if 0 < t < duration]})
    filled: list[float] = []
    bounds = pts + [duration]
    for a, b in zip(bounds, bounds[1:]):
        filled.append(a)
        gap = b - a
        if gap > max_gap:
            k = int(gap // max_gap)
            step = gap / (k + 1)
            filled.extend(a + step * i for i in range(1, k + 1))
    thinned: list[float] = []
    for t in filled:
        if not thinned or t - thinned[-1] >= min_gap:
            thinned.append(t)
    if len(thinned) > max_frames:
        step = len(thinned) / max_frames
        thinned = [thinned[int(i * step)] for i in range(max_frames)]
    return thinned


def storyboard(
    video: str | Path,
    out_dir: str | Path,
    transcript: Transcript | None = None,
    threshold: float | None = None,
    max_frames: int = 40,
    min_gap: float = 1.5,
    max_gap: float | None = None,
    width: int = 768,
    contact_sheet: bool = True,
) -> dict:
    """``threshold``/``max_gap`` default to values adapted to the video: vertical
    short-form (TikTok, Reels, Shorts) gets more sensitive cut detection, and every
    video gets a frame at least every ~5% of its length (4–20 s)."""
    info = probe(video)
    if not info.has_video:
        raise ValueError("storyboard needs a video file")
    vertical = bool(info.width and info.height and info.height > info.width)
    if threshold is None:
        threshold = 0.22 if vertical else 0.3
    if max_gap is None:
        max_gap = min(20.0, max(4.0, info.duration / 20))
    out = Path(out_dir)
    frames_dir = out / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    scenes = detect_scenes(video, threshold)
    starts = pick_times(info.duration, scenes, max_frames, min_gap, max_gap)
    ends = starts[1:] + [info.duration]

    shots: list[Shot] = []
    for i, (s, e) in enumerate(zip(starts, ends)):
        # grab slightly after the cut so we don't capture a transition frame
        t = min(s + min(0.4, (e - s) / 3), max(0.0, info.duration - 0.05))
        name = f"shot_{i:03d}_{fmt_time(s).replace(':', 'm').replace('.', 's')}.jpg"
        extract_frame(video, t, frames_dir / name, width)
        speech = ""
        if transcript:
            speech = join_words([w.text for w in transcript.iter_words() if s <= (w.start + w.end) / 2 < e])
        shots.append(Shot(i, s, e, f"frames/{name}", speech))

    sheets = _contact_sheets(out, shots) if contact_sheet else []

    data = {
        "video": str(video),
        "duration": round(info.duration, 3),
        "resolution": f"{info.width}x{info.height}",
        "fps": info.fps,
        "scene_cuts_detected": len(scenes),
        "vertical": vertical,
        "language": transcript.language if transcript else None,
        "contact_sheets": sheets,
        "shots": [s.to_dict() for s in shots],
    }
    (out / "storyboard.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "storyboard.md").write_text(to_markdown(data), encoding="utf-8")
    return data


def to_markdown(data: dict) -> str:
    lines = [f"# Storyboard — {Path(data['video']).name}", "",
             f"Duration {fmt_time(data['duration'])} · {data['resolution']} · "
             f"{len(data['shots'])} shots ({data['scene_cuts_detected']} scene cuts detected)", ""]
    for s in data["shots"]:
        lines.append(f"## {s['start_tc']} – {s['end_tc']}")
        lines.append(f"![shot {s['index']}]({s['frame']})")
        if s["speech"]:
            lines.append(f"> {s['speech']}")
        lines.append("")
    return "\n".join(lines)


def _contact_sheets(out: Path, shots: list[Shot], cols: int = 4, rows: int = 4, thumb_w: int = 384) -> list[str]:
    """Grid images with timestamps – lets a model see many shots in one image."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return []
    try:
        font = ImageFont.load_default(size=22)
    except TypeError:  # Pillow < 10.1
        font = ImageFont.load_default()
    cols = max(1, min(cols, len(shots)))
    per = cols * rows
    paths = []
    for page in range(0, len(shots), per):
        batch = shots[page:page + per]
        thumbs = []
        for s in batch:
            im = Image.open(out / s.frame).convert("RGB")
            h = int(im.height * thumb_w / im.width)
            thumbs.append((s, im.resize((thumb_w, h))))
        th = max(t.height for _, t in thumbs)
        r = (len(thumbs) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * thumb_w, r * th), "black")
        draw = ImageDraw.Draw(sheet)
        for k, (s, im) in enumerate(thumbs):
            x, y = (k % cols) * thumb_w, (k // cols) * th
            sheet.paste(im, (x, y))
            label = f"#{s.index}  {fmt_time(s.start)}"
            box = draw.textbbox((x + 6, y + 6), label, font=font)
            draw.rectangle((box[0] - 4, box[1] - 3, box[2] + 4, box[3] + 3), fill="black")
            draw.text((x + 6, y + 6), label, fill="white", font=font)
        name = f"contact_sheet_{page // per + 1:02d}.jpg"
        sheet.save(out / name, quality=85)
        paths.append(name)
    return paths

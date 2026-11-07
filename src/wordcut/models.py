"""Transcript data model: words with start/end times, grouped into segments."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA_VERSION = 1


@dataclass
class Word:
    text: str
    start: float
    end: float
    confidence: float | None = None
    speaker: str | None = None

    def to_dict(self) -> dict:
        d = {"text": self.text, "start": round(self.start, 3), "end": round(self.end, 3)}
        if self.confidence is not None:
            d["confidence"] = round(self.confidence, 3)
        if self.speaker is not None:
            d["speaker"] = self.speaker
        return d


@dataclass
class Segment:
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)
    speaker: str | None = None

    def to_dict(self) -> dict:
        d = {
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "text": self.text,
            "words": [w.to_dict() for w in self.words],
        }
        if self.speaker is not None:
            d["speaker"] = self.speaker
        return d


@dataclass
class Transcript:
    segments: list[Segment]
    language: str | None = None
    duration: float | None = None
    source: str | None = None
    backend: str | None = None
    model: str | None = None

    # ---- convenience -------------------------------------------------
    def words(self) -> list[Word]:
        return [w for s in self.segments for w in s.words]

    def iter_words(self) -> Iterator[Word]:
        for s in self.segments:
            yield from s.words

    @property
    def text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments).strip()

    def words_between(self, start: float, end: float) -> list[Word]:
        """Words that overlap the [start, end] interval."""
        return [w for w in self.iter_words() if w.end > start and w.start < end]

    # ---- serialization -----------------------------------------------
    def to_dict(self) -> dict:
        return {
            "schema": SCHEMA_VERSION,
            "language": self.language,
            "duration": None if self.duration is None else round(self.duration, 3),
            "source": self.source,
            "backend": self.backend,
            "model": self.model,
            "segments": [s.to_dict() for s in self.segments],
        }

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    @classmethod
    def from_dict(cls, data: dict) -> Transcript:
        segments = []
        for s in data.get("segments", []):
            words = [
                Word(
                    text=w["text"],
                    start=float(w["start"]),
                    end=float(w["end"]),
                    confidence=w.get("confidence"),
                    speaker=w.get("speaker"),
                )
                for w in s.get("words", [])
            ]
            segments.append(
                Segment(
                    start=float(s["start"]),
                    end=float(s["end"]),
                    text=s.get("text", " ".join(w.text for w in words)),
                    words=words,
                    speaker=s.get("speaker"),
                )
            )
        return cls(
            segments=segments,
            language=data.get("language"),
            duration=data.get("duration"),
            source=data.get("source"),
            backend=data.get("backend"),
            model=data.get("model"),
        )

    @classmethod
    def load(cls, path: str | Path) -> Transcript:
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def fill_missing_word_times(words: list[dict], seg_start: float, seg_end: float) -> list[dict]:
    """Some aligners (e.g. WhisperX) leave numbers/symbols without timestamps.

    Interpolate them between their timed neighbours so every word is usable.
    Mutates and returns ``words`` (dicts with optional "start"/"end").
    """
    n = len(words)
    i = 0
    while i < n:
        if words[i].get("start") is not None and words[i].get("end") is not None:
            i += 1
            continue
        j = i
        while j < n and (words[j].get("start") is None or words[j].get("end") is None):
            j += 1
        left = words[i - 1]["end"] if i > 0 else seg_start
        right = words[j]["start"] if j < n else seg_end
        if right < left:
            right = left
        step = (right - left) / (j - i)
        for k in range(i, j):
            words[k]["start"] = left + step * (k - i)
            words[k]["end"] = left + step * (k - i + 1)
        i = j
    return words


def regroup(words: Iterable[Word], max_gap: float = 1.0, max_len: float = 12.0) -> list[Segment]:
    """Build segments from a flat word list (used after remapping/cutting)."""
    segments: list[Segment] = []
    cur: list[Word] = []
    for w in words:
        if cur and (
            w.start - cur[-1].end > max_gap
            or w.end - cur[0].start > max_len
            or (w.speaker != cur[-1].speaker)
            or cur[-1].text.rstrip().endswith((".", "?", "!", "。", "？", "！"))
        ):
            segments.append(_mk_segment(cur))
            cur = []
        cur.append(w)
    if cur:
        segments.append(_mk_segment(cur))
    return segments


def _mk_segment(words: list[Word]) -> Segment:
    return Segment(
        start=words[0].start,
        end=words[-1].end,
        text=join_words([w.text for w in words]),
        words=list(words),
        speaker=words[0].speaker,
    )


def join_words(tokens: list[str]) -> str:
    """Join word tokens into readable text.

    Whisper tokens usually carry their own leading space; aligned words usually
    don't. CJK/Thai text has no spaces between words.
    """
    if any(t.startswith(" ") for t in tokens):
        return "".join(tokens).strip()
    out = ""
    for t in tokens:
        if out and not (_is_cjk(out[-1]) and t and _is_cjk(t[0])):
            out += " "
        out += t
    return out.strip()


def _is_cjk(ch: str) -> bool:
    cp = ord(ch)
    return (
        0x3040 <= cp <= 0x30FF  # kana
        or 0x3400 <= cp <= 0x9FFF  # CJK
        or 0xAC00 <= cp <= 0xD7AF  # hangul
        or 0x0E00 <= cp <= 0x0E7F  # thai
        or 0xF900 <= cp <= 0xFAFF
    )


def fmt_time(t: float, sep: str = ".") -> str:
    """Seconds -> HH:MM:SS.mmm (or MM:SS.mmm under an hour)."""
    if t < 0:
        t = 0.0
    ms = int(round(t * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"
    return f"{m:02d}:{s:02d}{sep}{ms:03d}"


def parse_time(value: str | float | int) -> float:
    """Accepts 12.5, "12.5", "1:02.3", "01:02:03.5", "1m2s"."""
    if isinstance(value, (int, float)):
        return float(value)
    v = value.strip().lower()
    if v.endswith("s") and "m" in v and ":" not in v:
        mins, secs = v[:-1].split("m", 1)
        return float(mins or 0) * 60 + float(secs or 0)
    if v.endswith("s") and ":" not in v:
        return float(v[:-1])
    parts = v.split(":")
    total = 0.0
    for p in parts:
        total = total * 60 + float(p)
    return total

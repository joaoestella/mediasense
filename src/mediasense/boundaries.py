"""Find where speech really starts and stops, so a cut never clips a word.

Whisper's word timestamps are only roughly right: the end of a word is often
100-300 ms early (worst for the last word before a pause) and a start can be
late. Cutting exactly on them chops off the tail of the last kept word or the
first sound of the next one.

So before rendering, every cut point is moved to a real gap in the sound. The
audio is turned into a loudness curve (10 ms steps); a moment counts as quiet
when it is close to the recording's own noise floor, and a gap is a quiet
stretch long enough not to be the short closure inside a word (the silence
before the "t" in "gato"). A cut that starts in the middle of speech is pushed
forward to the next gap; a cut that ends in speech is pulled back to the
previous gap. If there is no gap within reach, the quietest moment is used.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from .media import extract_audio

HOP = 0.01  # seconds per loudness step
WINDOW = 0.03  # seconds of audio measured at each step
MIN_GAP = 0.1  # a quiet stretch shorter than this can be the closure inside a word
MARGIN = 0.02  # stay this far into a gap, away from the speech next to it
REACH = 0.4  # how far a cut point may move


@dataclass
class Envelope:
    """Loudness (dBFS) of the audio every ``HOP`` seconds, with a speech/quiet threshold."""

    db: list[float]
    threshold: float
    floor: float
    loud: float

    @classmethod
    def from_samples(cls, samples, sample_rate: int = 16000) -> Envelope:
        import numpy as np

        x = np.asarray(samples, dtype=np.float32)
        hop = max(1, int(round(HOP * sample_rate)))
        win = max(hop, int(round(WINDOW * sample_rate)))
        n = max(1, int(np.ceil(len(x) / hop)))
        padded = np.concatenate([np.zeros(win // 2, np.float32), x, np.zeros(win, np.float32)])
        sq = np.concatenate([[0.0], np.cumsum(padded.astype(np.float64) ** 2)])
        starts = np.arange(n) * hop
        energy = (sq[starts + win] - sq[starts]) / win
        db = 10 * np.log10(np.maximum(energy, 1e-12))
        floor = float(np.percentile(db, 10))
        loud = float(np.percentile(db, 95))
        # quiet = within a fifth of the way from the noise floor to normal speech, never less
        # than 6 dB above the floor (noise wobble isn't speech) and always at least 12 dB below
        # normal speech (audio that never pauses, like music under talking, has no "quiet")
        threshold = min(floor + max(6.0, 0.2 * (loud - floor)), loud - 12.0)
        return cls(db=[float(v) for v in db], threshold=threshold, floor=floor, loud=loud)

    @classmethod
    def from_media(cls, path: str | Path) -> Envelope:
        from .transcribe import load_wav_array

        with tempfile.TemporaryDirectory() as tmp:
            wav = extract_audio(path, Path(tmp) / "env.wav", sample_rate=16000)
            return cls.from_samples(load_wav_array(wav), 16000)

    # -- queries ---------------------------------------------------------
    @property
    def duration(self) -> float:
        return len(self.db) * HOP

    def _i(self, t: float) -> int:
        return min(len(self.db) - 1, max(0, int(round(t / HOP))))

    def quiet(self, t: float) -> bool:
        return self.db[self._i(t)] < self.threshold

    def gaps(self, start: float, end: float, min_gap: float = MIN_GAP) -> list[tuple[float, float]]:
        """Quiet stretches of at least ``min_gap`` seconds overlapping ``start``..``end``."""
        # widen the scan so a gap that only touches the window is measured in full
        lo, hi = self._i(start - 1.0), self._i(end + 1.0)
        out, run = [], None
        for i in range(lo, hi + 1):
            if self.db[i] < self.threshold:
                run = i if run is None else run
                continue
            if run is not None:
                out.append((run * HOP, i * HOP))
                run = None
        if run is not None:
            out.append((run * HOP, (hi + 1) * HOP))
        return [(a, b) for a, b in out if b - a >= min_gap - 1e-9 and b >= start and a <= end]

    def quietest(self, start: float, end: float) -> float:
        lo, hi = self._i(start), self._i(end)
        if hi < lo:
            return start
        best = min(range(lo, hi + 1), key=lambda i: self.db[i])
        return best * HOP


def snap_cut(start: float, end: float, env: Envelope, reach: float = REACH,
             limits: tuple[float, float] | None = None) -> tuple[float, float] | None:
    """Move a cut so that both ends fall in a gap between sounds.

    ``start`` is where the kept audio before the cut stops, ``end`` where the kept
    audio after it resumes. ``limits`` = (latest start, earliest end) keeps the
    words the cut is meant to remove inside it. Returns the adjusted cut, or None
    when nothing that is safe to remove is left (the whole span is speech).
    """
    s, e = start, end
    s_max, e_min = limits if limits else (end, start)
    if s > 0.0:
        s = _snap_forward(s, max(s, min(e, s + reach, s_max)), env)
    if e < env.duration - HOP:
        e = _snap_back(e, min(e, max(s, e - reach, e_min)), env)
    if e - s < 0.01:
        return None
    return s, e


def _snap_forward(t: float, limit: float, env: Envelope) -> float:
    """First safe point at or after ``t``: lets the speech before the cut finish."""
    for a, b in env.gaps(t, max(limit, t + HOP)):
        if a <= t <= b:  # already in a gap: just keep clear of the speech before it
            return max(t, min(a + MARGIN, b))
        if a <= limit:
            return min(a + MARGIN, b, limit)
    return env.quietest(t, limit)


def _snap_back(t: float, limit: float, env: Envelope) -> float:
    """Last safe point at or before ``t``: keeps the start of the speech after the cut."""
    for a, b in reversed(env.gaps(min(limit, t - HOP), t)):
        if a <= t <= b:
            return min(t, max(b - MARGIN, a))
        if b >= limit:
            return max(b - MARGIN, a, limit)
    return env.quietest(limit, t)

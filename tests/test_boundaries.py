"""Cut points must land in real pauses, even when the transcript's word times are off."""

import wave

import numpy as np
import pytest

from mediasense import api
from mediasense.boundaries import Envelope, snap_cut
from mediasense.edit import PlanBuilder
from mediasense.models import Segment, Transcript, Word

SR = 16000


def _speech(spans, duration=3.0, seed=1, ramp=0.03):
    """Voiced, speech-like bursts (harmonics + a little breath noise) over a quiet room."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(duration * SR)) / SR
    x = rng.normal(0, 10 ** (-62 / 20), t.size)  # room noise
    for a, b in spans:
        m = (t >= a) & (t < b)
        tt = t[m] - a
        voice = sum(np.sin(2 * np.pi * 180 * k * tt) / k for k in range(1, 8))
        edge = np.minimum(1, np.minimum(tt, (b - a) - tt) / ramp)  # rise/fall
        x[m] += 0.12 * voice * edge + rng.normal(0, 0.01, m.sum()) * edge
    return x.astype(np.float32)


def _words(items):
    return Transcript(language="pt", duration=3.0,
                      segments=[Segment(0.0, 3.0, " ".join(w for w, _, _ in items),
                                        [Word(" " + w, s, e) for w, s, e in items])])


# the real sounds: "então" 0.50-1.20, "éé" 1.35-1.65, "vamos" 1.80-2.50
REAL = [(0.50, 1.20), (1.35, 1.65), (1.80, 2.50)]
# what Whisper typically reports: the word before the filler ends 200 ms early,
# the word after it starts 150 ms late
REPORTED = [("então", 0.52, 1.00), ("éé", 1.10, 1.70), ("vamos", 1.95, 2.48)]


def _env():
    return Envelope.from_samples(_speech(REAL), SR)


def _loud(env, a, b):
    return [i * 0.01 for i in range(int(a / 0.01), int(b / 0.01)) if not env.quiet(i * 0.01)]


def test_envelope_tells_speech_from_silence():
    env = _env()
    assert env.quiet(0.2) and env.quiet(1.27) and env.quiet(2.8)
    assert not env.quiet(0.9) and not env.quiet(1.5) and not env.quiet(2.2)
    ((a, b),) = env.gaps(1.2, 1.4)
    assert 1.19 <= a <= 1.23 and 1.33 <= b <= 1.37


def test_filler_cut_no_longer_clips_the_words_around_it():
    env = _env()
    b = PlanBuilder(3.0, _words(REPORTED))
    b.remove_fillers()
    raw = b.build()
    (cut,) = raw.cuts
    assert cut.start < 1.15  # trusting the transcript cuts into the end of "então"…
    plan, moved = raw.snapped(env)
    (cut,) = plan.cuts
    assert moved == 1 and cut.adjusted
    assert 1.20 <= cut.start <= 1.30, cut  # …snapping lets "então" finish
    assert 1.65 <= cut.end <= 1.80, cut  # and keeps the start of "vamos"
    assert not _loud(env, cut.start, cut.end)[:1] or min(_loud(env, cut.start, cut.end)) >= 1.33
    assert "moved to the pause" in plan.summary()


def test_cut_already_in_a_pause_stays_put():
    env = _env()
    assert snap_cut(1.25, 1.31, env) == (1.25, 1.31)


def test_short_closure_inside_a_word_is_not_a_pause():
    # "ga_to": a 50 ms stop closure inside one word must not count as the word's end
    env = Envelope.from_samples(_speech([(0.5, 0.8), (0.86, 1.1), (1.5, 2.0)], ramp=0.01), SR)
    s, e = snap_cut(0.75, 1.6, env)  # transcript says the word ended at 0.75, inside "ga"
    assert 1.1 <= s <= 1.2  # the cut waits for the real end, after "to"
    assert 1.4 <= e <= 1.5


def test_all_speech_span_keeps_the_original_cut():
    env = Envelope.from_samples(_speech([(0.0, 3.0)]), SR)
    plan = PlanBuilder(3.0).remove_range(1.0, 1.5).build()
    snapped, moved = plan.snapped(env)
    assert snapped.cuts[0].start == pytest.approx(1.0, abs=0.45)
    assert snapped.cuts[0].end > snapped.cuts[0].start


def test_plan_edit_snaps_by_default(tmp_path):
    wav = tmp_path / "talk.wav"
    with wave.open(str(wav), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(SR)
        fh.writeframes((np.clip(_speech(REAL), -1, 1) * 32767).astype(np.int16).tobytes())
    t = _words(REPORTED)
    plan, _, report = api.plan_edit(wav, t, fillers=True)
    assert report["adjusted"] == 1
    assert plan.cuts[0].start >= 1.2 and plan.cuts[0].end <= 1.8
    raw, _, report = api.plan_edit(wav, t, fillers=True, snap=False)
    assert "adjusted" not in report and raw.cuts[0].start < 1.15


def test_silence_without_any_pause_is_not_cut():
    # the transcript shows a gap, but music keeps playing through it: nothing to remove
    env = Envelope.from_samples(_speech([(0.0, 3.0)]), SR)
    b = PlanBuilder(3.0)
    b.remove_silences([(1.0, 1.6)], keep=0.1)
    snapped, moved = b.build().snapped(env)
    assert moved == 1 and snapped.cuts == []

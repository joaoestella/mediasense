"""Synthetic media + a matching transcript, so tests need no speech model.

Timeline (6 s):
    0.0–1.0  tone   "Olá, pessoal!"   (two words)
    1.0–2.5  silence
    2.5–4.0  tone   "éé então hoje"  (filler + two words)
    4.0–4.3  silence
    4.3–6.0  tone   "vamos testar isso."
Video: red 0–2 s, blue 2–4 s, green 4–6 s (two scene cuts).
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from mediasense.models import Segment, Transcript, Word

TONE = "0.5*sin(2*PI*440*t)*(lt(t,1)+between(t,2.5,4)+gte(t,4.3))"


def _ff(*args: str) -> None:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg required")
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args], check=True)


@pytest.fixture(scope="session")
def media_dir(tmp_path_factory):
    return tmp_path_factory.mktemp("media")


@pytest.fixture(scope="session")
def audio_file(media_dir):
    p = media_dir / "talk.wav"
    _ff("-f", "lavfi", "-i", f"aevalsrc='{TONE}':s=16000:d=6", "-c:a", "pcm_s16le", str(p))
    return p


@pytest.fixture(scope="session")
def video_file(media_dir):
    p = media_dir / "talk.mp4"
    _ff(
        "-f", "lavfi", "-i", "color=c=red:s=320x240:r=25:d=2",
        "-f", "lavfi", "-i", "color=c=blue:s=320x240:r=25:d=2",
        "-f", "lavfi", "-i", "color=c=green:s=320x240:r=25:d=2",
        "-f", "lavfi", "-i", f"aevalsrc='{TONE}':s=48000:d=6",
        "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
        "-map", "[v]", "-map", "3:a", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
        str(p),
    )
    return p


@pytest.fixture
def transcript():
    def w(t, s, e, c=0.9):
        return Word(t, s, e, c)

    return Transcript(
        language="pt",
        duration=6.0,
        segments=[
            Segment(0.0, 1.0, "Olá, pessoal!", [w(" Olá,", 0.05, 0.45), w(" pessoal!", 0.5, 0.95)]),
            Segment(2.5, 4.0, "éé então hoje", [w(" éé", 2.55, 2.95), w(" então", 3.05, 3.45),
                                                w(" hoje", 3.5, 3.95)]),
            Segment(4.3, 6.0, "vamos testar isso.", [w(" vamos", 4.35, 4.8), w(" testar", 4.9, 5.4),
                                                     w(" isso.", 5.45, 5.95)]),
        ],
    )


@pytest.fixture
def transcript_file(transcript, video_file):
    from mediasense.api import default_transcript_path

    p = default_transcript_path(video_file)
    transcript.source = str(video_file)
    transcript.save(p)
    yield p
    p.unlink(missing_ok=True)

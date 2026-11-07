"""Background jobs for the MCP server.

MCP clients (Claude Desktop in particular) give up on a tool call after a minute or
two, while transcribing a long video on a CPU — or downloading the speech model the
first time — can take several minutes. Long tools therefore run as jobs: the call
waits a bounded time, returns the result if it's ready, otherwise returns a short
"still working" note with a job id that ``check_job`` can be asked about.
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import wait as wait_futures
from dataclasses import dataclass, field
from typing import Any

_TRANSCRIBING = re.compile(r"transcribing ([\d.]+)s of audio")
_SEGMENT = re.compile(r"^\s*([\d.]+)s\s")


@dataclass
class Job:
    id: str
    kind: str
    key: str
    label: str
    status: str = "queued"  # queued | running | done | error
    stage: str = "waiting to start"
    percent: float | None = None
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    result: Any = None
    error: str | None = None
    future: Future | None = None
    _audio_seconds: float | None = None

    # progress callback handed to the work function
    def progress(self, message: str) -> None:
        msg = str(message).strip()
        m = _TRANSCRIBING.search(msg)
        if m:
            self._audio_seconds = float(m.group(1)) or None
            self.percent = 0.0
        m = _SEGMENT.match(msg)
        if m and self._audio_seconds:
            self.percent = min(99.0, 100.0 * float(m.group(1)) / self._audio_seconds)
            self.stage = "transcribing"
            return
        if msg:
            self.stage = msg.rstrip("…. ")

    @property
    def elapsed(self) -> float:
        end = self.finished or time.time()
        return end - (self.started or self.created)

    def summary(self) -> str:
        pct = f" – {self.percent:.0f}%" if self.percent is not None and self.status == "running" else ""
        return f"{self.kind} of {self.label}: {self.status}{pct} ({self.stage}, {self.elapsed:.0f}s so far)"


class JobManager:
    """One worker thread: heavy jobs run one after another (they'd fight over the CPU anyway)."""

    def __init__(self, workers: int = 1, keep: int = 50):
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="mediasense-job")
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._keep = keep

    def submit(self, kind: str, key: str, label: str, fn: Callable[[Callable[[str], None]], Any]) -> Job:
        """Start ``fn(progress)`` in the background, or return the identical job already running."""
        with self._lock:
            for job in self._jobs.values():
                if job.kind == kind and job.key == key and job.status in ("queued", "running"):
                    return job
            job = Job(id=uuid.uuid4().hex[:8], kind=kind, key=key, label=label)

            def run() -> Any:
                job.status, job.started, job.stage = "running", time.time(), "starting"
                try:
                    job.result = fn(job.progress)
                    job.status = "done"
                    job.stage = "finished"
                    return job.result
                except BaseException as e:  # reported through check_job
                    job.status, job.error = "error", f"{type(e).__name__}: {e}" if not str(e) else str(e)
                    raise
                finally:
                    job.finished = time.time()

            job.future = self._pool.submit(run)
            self._jobs[job.id] = job
            self._trim()
            return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id.strip())

    def wait(self, job: Job, seconds: float) -> Job:
        if job.future is not None and seconds > 0:
            wait_futures([job.future], timeout=seconds)
        return job

    def queue_position(self, job: Job) -> int:
        queued = sorted((j for j in self._jobs.values() if j.status == "queued"), key=lambda j: j.created)
        return next((i + 1 for i, j in enumerate(queued) if j.id == job.id), 0)

    def active(self) -> list[Job]:
        return [j for j in self._jobs.values() if j.status in ("queued", "running")]

    def _trim(self) -> None:
        done = sorted((j for j in self._jobs.values() if j.status in ("done", "error")), key=lambda j: j.created)
        for j in done[: max(0, len(self._jobs) - self._keep)]:
            self._jobs.pop(j.id, None)


manager = JobManager()

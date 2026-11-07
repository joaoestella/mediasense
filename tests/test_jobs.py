"""Long MCP tool calls become background jobs with check_job."""

import time

import pytest

from mediasense.jobs import Job, JobManager

pytest.importorskip("mcp")


def test_progress_parsing():
    j = Job(id="x", kind="t", key="k", label="l")
    j.progress("loading model 'small' on cpu (first run downloads it)…")
    assert j.stage.startswith("loading model") and j.percent is None
    j.progress("transcribing 200.0s of audio…")
    j.progress("   50.0s  some words")
    assert j.stage == "transcribing" and j.percent == 25.0
    j.progress("  400.0s  more")  # never claims 100% before it's done
    assert j.percent == 99.0


def test_manager_dedupes_and_reports_errors():
    m = JobManager()
    a = m.submit("k", "same", "x", lambda p: time.sleep(0.3) or 1)
    b = m.submit("k", "same", "x", lambda p: 2)
    assert a is b
    m.wait(a, 2)
    assert a.status == "done" and a.result == 1
    bad = m.submit("k", "other", "x", lambda p: (_ for _ in ()).throw(ValueError("boom")))
    m.wait(bad, 2)
    assert bad.status == "error" and "boom" in bad.error


def test_mcp_slow_tool_returns_job_then_result(monkeypatch, video_file, transcript_file):
    from mediasense import api
    from mediasense import mcp_server as s

    monkeypatch.setenv("MEDIASENSE_TOOL_WAIT", "1")
    real = api.understand

    def slow(*a, progress=None, **k):
        progress("transcribing 10.0s of audio…")
        progress("    5.0s  halfway")
        time.sleep(3.5)
        return real(*a, progress=progress, **k)

    monkeypatch.setattr(api, "understand", slow)
    first = s.understand(str(video_file))
    assert isinstance(first, str) and first.startswith("STILL WORKING")
    assert "50%" in first and "check_job" in first
    job_id = first.split("job ")[1].split(":")[0]
    # asking again while it runs returns the same job instead of starting another
    again = s.understand(str(video_file))
    assert isinstance(again, str) and job_id in again
    assert job_id in s.check_job()  # listing
    for _ in range(10):
        result = s.check_job(job_id)
        if not isinstance(result, str):
            break
    assert isinstance(result, list) and "## Shots" in result[0]


def test_mcp_job_error_is_readable(monkeypatch):
    from mediasense import mcp_server as s

    monkeypatch.setenv("MEDIASENSE_TOOL_WAIT", "2")
    with pytest.raises(Exception) as e:
        s.understand("/nope/missing.mp4")
    assert "file not found" in str(e.value)
    with pytest.raises(Exception, match="unknown job"):
        s.check_job("deadbeef")

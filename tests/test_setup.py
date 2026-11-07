import json

from mediasense import setup as st
from mediasense.cli import main


def test_setup_creates_config(tmp_path, monkeypatch):
    cfg = tmp_path / "Claude" / "claude_desktop_config.json"
    monkeypatch.setenv("MEDIASENSE_CLAUDE_CONFIG", str(cfg))
    monkeypatch.setattr(st.shutil, "which", lambda name: "/opt/bin/mediasense" if name == "mediasense" else None)
    lines = st.setup(cookies_browser="firefox")
    data = json.loads(cfg.read_text(encoding="utf-8"))
    entry = data["mcpServers"]["mediasense"]
    assert entry["args"] == ["mcp"] and entry["command"].endswith("mediasense")
    assert entry["env"] == {"MEDIASENSE_COOKIES_FROM_BROWSER": "firefox"}
    assert any("added" in ln for ln in lines) and any("Claude Code: not found" in ln for ln in lines)


def test_setup_keeps_other_settings_and_backs_up(tmp_path, monkeypatch):
    cfg = tmp_path / "claude_desktop_config.json"
    original = {"preferences": {"theme": "dark"}, "mcpServers": {"other": {"command": "x"}}}
    cfg.write_text(json.dumps(original), encoding="utf-8-sig")  # BOM, as Notepad writes it
    monkeypatch.setenv("MEDIASENSE_CLAUDE_CONFIG", str(cfg))
    st.setup(code=False)
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["preferences"] == {"theme": "dark"}
    assert set(data["mcpServers"]) == {"other", "mediasense"}
    assert len(list(tmp_path.glob("claude_desktop_config.json.*.bak"))) == 1
    assert "already set up" in st.setup(code=False)[0]  # idempotent, no second backup
    assert len(list(tmp_path.glob("*.bak"))) == 1


def test_setup_refuses_broken_json(tmp_path, monkeypatch):
    cfg = tmp_path / "claude_desktop_config.json"
    cfg.write_text("{ not json", encoding="utf-8")
    monkeypatch.setenv("MEDIASENSE_CLAUDE_CONFIG", str(cfg))
    line = st.setup(code=False)[0]
    assert "not valid JSON" in line and cfg.read_text(encoding="utf-8") == "{ not json"


def test_setup_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    cfg = tmp_path / "c.json"
    monkeypatch.setenv("MEDIASENSE_CLAUDE_CONFIG", str(cfg))
    assert main(["setup", "--desktop", "-n"]) == 0
    assert "would write" in capsys.readouterr().out and not cfg.exists()


def test_claude_code_command(monkeypatch):
    calls = []
    monkeypatch.setattr(st.shutil, "which", lambda n: {"claude": "/bin/claude", "mediasense": "/bin/mediasense"}.get(n))

    class R:
        returncode = 0
        stdout = stderr = ""

    monkeypatch.setattr(st.subprocess, "run", lambda cmd, **k: calls.append(cmd) or R())
    assert "added" in st.configure_code({"MEDIASENSE_COOKIES_FROM_BROWSER": "firefox"})
    add = calls[-1]
    assert add[:6] == ["/bin/claude", "mcp", "add", "--scope", "user", "mediasense"]
    assert add[6:8] == ["-e", "MEDIASENSE_COOKIES_FROM_BROWSER=firefox"]
    assert add[8] == "--" and add[9].endswith("mediasense") and add[10] == "mcp"


def test_doctor_runs(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("MEDIASENSE_CLAUDE_CONFIG", str(tmp_path / "none.json"))
    checks = {name: ok for name, ok, _ in st.doctor()}
    assert checks["FFmpeg"] is True and "Claude Desktop" in checks
    main(["doctor"])
    assert "FFmpeg" in capsys.readouterr().out


def test_doctor_uses_full_claude_path_and_survives_errors(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIASENSE_CLAUDE_CONFIG", str(tmp_path / "none.json"))
    real_which = st.shutil.which
    monkeypatch.setattr(st.shutil, "which",
                        lambda n: r"C:\\npm\\claude.cmd" if n == "claude" else real_which(n))
    seen = []
    real_run = st.subprocess.run

    def fake_run(cmd, **k):
        if cmd[0].endswith("claude.cmd"):
            seen.append(cmd)
            raise FileNotFoundError(2, "The system cannot find the file specified")
        return real_run(cmd, **k)

    monkeypatch.setattr(st.subprocess, "run", fake_run)
    checks = {name: (ok, detail) for name, ok, detail in st.doctor()}
    assert seen and seen[0][0].endswith("claude.cmd")
    assert checks["Claude Code"][0] is None and "couldn't check" in checks["Claude Code"][1]

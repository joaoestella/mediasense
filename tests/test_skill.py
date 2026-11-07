import zipfile
from pathlib import Path

from mediasense import setup as st
from mediasense import skill
from mediasense.cli import main

REPO_SKILL = Path(__file__).resolve().parents[1] / "skills" / "mediasense" / "SKILL.md"


def test_skill_text_is_the_repo_skill():
    text = skill.skill_text()
    assert text == REPO_SKILL.read_text(encoding="utf-8")
    assert text.startswith("---\nname: mediasense\ndescription: ")
    desc = text.split("description: ", 1)[1].split("\n", 1)[0]
    assert len(desc) <= 1024 and "<" not in desc and ">" not in desc  # upload limits


def test_zip_layout(tmp_path):
    z = skill.write_skill_zip(tmp_path)
    assert z.name == "mediasense-skill.zip"
    with zipfile.ZipFile(z) as zf:
        assert zf.namelist() == ["mediasense/SKILL.md"]
        assert zf.read("mediasense/SKILL.md").decode() == skill.skill_text()


def test_claude_code_skill_install_is_idempotent(tmp_path):
    first = skill.install_claude_code_skill(home=tmp_path)
    assert "installed" in first
    assert skill.claude_code_skill_path(tmp_path).read_text(encoding="utf-8") == skill.skill_text()
    assert "up to date" in skill.install_claude_code_skill(home=tmp_path)


def test_setup_mentions_skill_for_desktop(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIASENSE_CLAUDE_CONFIG", str(tmp_path / "c.json"))
    lines = st.setup(code=False)
    howto = [ln for ln in lines if "Settings → Skills" in ln]
    assert howto and "mediasense-skill.zip" in howto[0]
    assert (tmp_path / "mediasense-home" / "mediasense-skill.zip").exists()


def test_setup_installs_claude_code_skill(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "configure_code", lambda env, dry_run: "Claude Code: added")
    monkeypatch.setattr(skill.Path, "home", lambda: tmp_path)
    lines = st.setup(desktop=False)
    assert any("Claude Code skill: installed" in ln for ln in lines)
    assert (tmp_path / ".claude" / "skills" / "mediasense" / "SKILL.md").exists()


def test_cli_skill(tmp_path, capsys):
    assert main(["skill", "-o", str(tmp_path / "x.zip")]) == 0
    assert (tmp_path / "x.zip").exists() and "Settings → Skills" in capsys.readouterr().out
    assert main(["skill", "--print"]) == 0
    assert "name: mediasense" in capsys.readouterr().out

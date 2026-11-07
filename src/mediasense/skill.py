"""The mediasense Claude skill: tells Claude to use these tools for media links (instead of the browser)
and how to work with them. Installed for Claude Code automatically; packaged as a zip for Claude Desktop /
claude.ai, where skills are uploaded in Settings → Skills."""

from __future__ import annotations

import zipfile
from pathlib import Path

SKILL_NAME = "mediasense"


def skill_text() -> str:
    try:
        from importlib.resources import files

        return files("mediasense").joinpath("_skill/SKILL.md").read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        # editable install / running from a checkout
        repo = Path(__file__).resolve().parents[2] / "skills" / SKILL_NAME / "SKILL.md"
        return repo.read_text(encoding="utf-8")


def claude_code_skill_path(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".claude" / "skills" / SKILL_NAME / "SKILL.md"


def install_claude_code_skill(home: Path | None = None, dry_run: bool = False) -> str:
    dst = claude_code_skill_path(home)
    text = skill_text()
    if dst.exists() and dst.read_text(encoding="utf-8") == text:
        return f"Claude Code skill: up to date ({dst})"
    if dry_run:
        return f"Claude Code skill: would write {dst}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    verb = "updated" if dst.exists() else "installed"
    dst.write_text(text, encoding="utf-8")
    return f"Claude Code skill: {verb} ({dst})"


def write_skill_zip(dest: str | Path) -> Path:
    """Zip in the layout Claude's skill upload expects: mediasense/SKILL.md."""
    dest = Path(dest)
    if dest.is_dir() or not dest.suffix:
        dest = dest / f"{SKILL_NAME}-skill.zip"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{SKILL_NAME}/SKILL.md", skill_text())
    return dest


DESKTOP_HOWTO = (
    "Claude Desktop / claude.ai: upload {zip} in Settings → Skills (Configurações → Skills) → add/upload. "
    "It makes Claude use mediasense for TikTok/YouTube links instead of trying the browser."
)

"""`mediasense setup` – connect mediasense to Claude Desktop / Claude Code without editing JSON by hand,
and `mediasense doctor` – check that everything needed is in place."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SERVER_NAME = "mediasense"


# ------------------------------------------------------------------ locating things
def desktop_config_paths() -> list[Path]:
    """Every Claude Desktop config location that exists on this machine (or the default one).

    The reliable way to check is Claude Desktop → Settings → Developer → Edit Config, which opens
    the folder the app really reads.
    """
    override = os.environ.get("MEDIASENSE_CLAUDE_CONFIG")
    if override:
        return [Path(override)]
    system = platform.system()
    home = Path.home()
    candidates: list[Path] = []
    if system == "Windows":
        appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        # Microsoft Store (MSIX) builds keep their data in %LOCALAPPDATA%\Claude-Data\claude_<id>
        # (current) or a virtualised Packages\Claude_<id>\LocalCache\Roaming\Claude (older)
        store_data = sorted((local / "Claude-Data").glob("claude_*")) if (local / "Claude-Data").is_dir() else []
        if store_data:
            return [d / "claude_desktop_config.json" for d in store_data]
        candidates.append(appdata / "Claude" / "claude_desktop_config.json")
        packages = local / "Packages"
        if packages.is_dir():
            for pkg in packages.glob("Claude_*"):
                candidates.append(pkg / "LocalCache" / "Roaming" / "Claude" / "claude_desktop_config.json")
    elif system == "Darwin":
        candidates.append(home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
        candidates.append(base / "Claude" / "claude_desktop_config.json")
    existing = [p for p in candidates if p.parent.is_dir()]
    return existing or candidates[:1]


def server_command() -> list[str]:
    """Absolute command that starts the MCP server (GUI apps don't see the terminal's PATH)."""
    # absolute() rather than resolve(): keep the stable ~/.local/bin link pipx creates,
    # not the venv path behind it
    exe = shutil.which("mediasense")
    if exe:
        return [str(Path(exe).absolute()), "mcp"]
    argv0 = Path(sys.argv[0])
    if argv0.name.lower().startswith("mediasense") and argv0.exists():
        return [str(argv0.absolute()), "mcp"]
    return [sys.executable, "-m", "mediasense", "mcp"]


# ------------------------------------------------------------------ Claude Desktop
def configure_desktop(config_path: Path, env: dict[str, str] | None = None, dry_run: bool = False) -> str:
    """Add/refresh the mediasense entry, keeping everything else in the file. Returns a status line."""
    data: dict = {}
    if config_path.exists() and config_path.read_text(encoding="utf-8-sig").strip():
        try:
            data = json.loads(config_path.read_text(encoding="utf-8-sig"))
        except ValueError as e:
            raise ValueError(f"{config_path} is not valid JSON ({e}); fix or delete it, then run setup again") from e
        if not isinstance(data, dict):
            raise ValueError(f"{config_path} doesn't contain a JSON object")
    cmd = server_command()
    entry: dict = {"command": cmd[0], "args": cmd[1:]}
    if env:
        entry["env"] = env
    servers = data.setdefault("mcpServers", {})
    previous = servers.get(SERVER_NAME)
    if previous == entry:
        return f"Claude Desktop: already set up ({config_path})"
    servers[SERVER_NAME] = entry
    if dry_run:
        preview = json.dumps({"mcpServers": {SERVER_NAME: entry}}, indent=2)
        return f"Claude Desktop: would write {config_path}:\n{preview}"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if config_path.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(config_path, config_path.with_name(f"{config_path.name}.{stamp}.bak"))
    config_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    verb = "updated" if previous else "added"
    return f"Claude Desktop: {verb} ({config_path}) — quit Claude Desktop completely and open it again"


# ------------------------------------------------------------------ Claude Code
def configure_code(env: dict[str, str] | None = None, dry_run: bool = False) -> str:
    claude = shutil.which("claude")
    if not claude:
        return "Claude Code: not found (skipped)"
    cmd = server_command()
    # name goes before -e: --env is variadic and would swallow it otherwise
    add = [claude, "mcp", "add", "--scope", "user", SERVER_NAME]
    for k, v in (env or {}).items():
        add += ["-e", f"{k}={v}"]
    add += ["--", *cmd]
    if dry_run:
        return "Claude Code: would run " + " ".join(add)
    try:
        subprocess.run([claude, "mcp", "remove", "--scope", "user", SERVER_NAME], capture_output=True, text=True,
                       timeout=60)
        proc = subprocess.run(add, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        return f"Claude Code: failed ({e})"
    if proc.returncode != 0:
        return f"Claude Code: failed ({(proc.stderr or proc.stdout).strip()[:200]})"
    return "Claude Code: added (available in every project; check with `claude mcp list`)"


def setup(desktop: bool = True, code: bool = True, cookies_browser: str | None = None,
          dry_run: bool = False) -> list[str]:
    from .skill import DESKTOP_HOWTO, install_claude_code_skill, write_skill_zip

    env = {"MEDIASENSE_COOKIES_FROM_BROWSER": cookies_browser} if cookies_browser else None
    lines = []
    if desktop:
        for p in desktop_config_paths():
            try:
                lines.append(configure_desktop(p, env, dry_run))
            except (ValueError, OSError) as e:
                lines.append(f"Claude Desktop: {e}")
        if not dry_run:
            from .fetch import download_dir

            try:
                lines.append(DESKTOP_HOWTO.format(zip=write_skill_zip(download_dir())))
            except OSError as e:
                lines.append(f"Claude Desktop skill: couldn't write the zip ({e})")
    if code:
        status = configure_code(env, dry_run)
        lines.append(status)
        if not status.startswith("Claude Code: not found"):
            try:
                lines.append(install_claude_code_skill(dry_run=dry_run))
            except OSError as e:
                lines.append(f"Claude Code skill: failed ({e})")
    return lines


# ------------------------------------------------------------------ doctor
def doctor() -> list[tuple[str, bool | None, str]]:
    """(check, ok?, detail). ok=None means optional / informational."""
    from . import __version__

    out: list[tuple[str, bool | None, str]] = [("mediasense", True, __version__)]

    ff = shutil.which("ffmpeg")
    if ff:
        try:
            first = subprocess.run([ff, "-version"], capture_output=True, text=True).stdout.splitlines()[0]
            out.append(("FFmpeg", True, first.replace("ffmpeg version ", "").split(" Copyright")[0]))
        except Exception as e:
            out.append(("FFmpeg", False, str(e)))
    else:
        out.append(("FFmpeg", False, "not found – winget install ffmpeg / brew install ffmpeg / apt install ffmpeg"))

    try:
        import faster_whisper  # type: ignore

        out.append(("Speech model (faster-whisper)", True, getattr(faster_whisper, "__version__", "installed")))
    except ImportError:
        out.append(("Speech model (faster-whisper)", False, 'pip install "mediasense[whisper]"'))

    out.append(_gpu_check())

    try:
        import yt_dlp  # type: ignore

        out.append(("Links (yt-dlp)", True, yt_dlp.version.__version__))
    except ImportError:
        out.append(("Links (yt-dlp)", False, 'pip install "mediasense[url]"'))
    try:
        import curl_cffi  # type: ignore  # noqa: F401

        out.append(("Browser impersonation (curl-cffi)", True, "TikTok & co."))
    except ImportError:
        out.append(("Browser impersonation (curl-cffi)", None, "optional; helps with TikTok"))

    try:
        import mcp  # type: ignore  # noqa: F401

        out.append(("MCP server", True, " ".join(server_command())))
    except ImportError:
        out.append(("MCP server", False, 'pip install "mediasense[mcp]"'))

    configured = []
    for p in desktop_config_paths():
        try:
            if SERVER_NAME in json.loads(p.read_text(encoding="utf-8-sig")).get("mcpServers", {}):
                configured.append(str(p))
        except (OSError, ValueError, AttributeError):
            pass
    out.append(("Claude Desktop", bool(configured) or None,
                f"configured in {configured[0]}" if configured else "not configured – run `mediasense setup`"))
    claude = shutil.which("claude")
    if claude:
        # full path: on Windows `claude` is a .cmd shim that subprocess can't find by bare name
        try:
            proc = subprocess.run([claude, "mcp", "get", SERVER_NAME], capture_output=True, text=True,
                                  timeout=60)
            ok = proc.returncode == 0
            out.append(("Claude Code", ok or None, "configured" if ok else "not configured – run `mediasense setup`"))
        except (OSError, subprocess.SubprocessError) as e:
            out.append(("Claude Code", None, f"couldn't check ({e})"))
    return out


def _gpu_check() -> tuple[str, bool | None, str]:
    try:
        import ctranslate2  # type: ignore
    except ImportError:
        return ("GPU", None, "n/a")
    try:
        n = ctranslate2.get_cuda_device_count()
    except Exception:
        n = 0
    if not n:
        return ("GPU", None, "no NVIDIA GPU – using CPU")
    from .transcribe import missing_cuda_libs

    missing = missing_cuda_libs()
    if missing:
        return ("GPU", None, f"NVIDIA GPU found, but {', '.join(missing)} missing – using CPU (slower)")
    return ("GPU", True, f"{n} CUDA device(s)")

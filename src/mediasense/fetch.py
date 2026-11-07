"""Accept links as well as files: TikTok, YouTube, Instagram, X, Vimeo… (via yt-dlp).

Downloads go to ``~/Downloads/mediasense`` (override with ``MEDIASENSE_HOME``)
and are reused: asking about the same link twice doesn't download it again.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_URL = re.compile(r"^https?://", re.IGNORECASE)


class FetchError(RuntimeError):
    pass


class _SilentLogger:
    """yt-dlp prints errors itself even when quiet; we report them once, cleanly."""

    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        pass


def is_url(value: str) -> bool:
    return bool(_URL.match(value.strip()))


def download_dir() -> Path:
    base = os.environ.get("MEDIASENSE_HOME")
    path = Path(base).expanduser() if base else Path.home() / "Downloads" / "mediasense"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe(text: str) -> str:
    return re.sub(r"[^\w.-]+", "_", text, flags=re.UNICODE).strip("_")[:80] or "media"


def fetch(url: str, dest: str | Path | None = None, audio_only: bool = False, download: bool = True) -> Path:
    """Download ``url`` (or return the cached file) and return its local path.

    ``download=False`` only returns an already-downloaded copy (read-only tools use this).
    """
    try:
        import yt_dlp  # type: ignore
    except ImportError as e:
        raise FetchError('links need yt-dlp. Install with:  pip install "mediasense[url]"') from e
    out_dir = Path(dest) if dest else download_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    opts = {
        "quiet": True,
        "logger": _SilentLogger(),
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "paths": {"home": str(out_dir)},
        "outtmpl": {"default": "%(extractor)s_%(id)s.%(ext)s"},
        "restrictfilenames": True,
        # good quality without huge files; merged into mp4 (needs ffmpeg, which we require anyway)
        "format": "bestaudio/best" if audio_only else "bv*[height<=1080]+ba/b[height<=1080]/b",
        "merge_output_format": None if audio_only else "mp4",
    }
    if not download:
        found = _find_downloaded(url, out_dir)
        if found:
            return found
        raise FetchError("this link hasn't been downloaded yet – call understand_media or transcribe on it first")
    errors: list[str] = []
    for label, extra in _attempts():
        try:
            return _download(yt_dlp, url, out_dir, {**opts, **extra})
        except FetchError as e:
            errors.append(f"{label}: {e}")
            if not _worth_retrying(str(e)):
                break
    hint = ("" if _can_impersonate() else
            ' Sites like TikTok often need browser impersonation: pip install "mediasense[url]" '
            "(installs curl-cffi) and try again.")
    raise FetchError("couldn't download this link. " + " | ".join(errors) + hint +
                     " If it still fails, update yt-dlp, or set MEDIASENSE_COOKIES_FROM_BROWSER=firefox "
                     "to use your browser's login.")


def _can_impersonate() -> bool:
    try:
        import curl_cffi  # type: ignore  # noqa: F401

        return True
    except ImportError:
        return False


def _attempts() -> list[tuple[str, dict]]:
    """Plain request first; then pretend to be Chrome (TikTok & co. block bots); then cookies."""
    attempts: list[tuple[str, dict]] = [("direct", {})]
    if _can_impersonate():
        try:
            from yt_dlp.networking.impersonate import ImpersonateTarget  # type: ignore

            attempts.append(("as Chrome", {"impersonate": ImpersonateTarget.from_str("chrome")}))
        except Exception:
            pass
    browser = os.environ.get("MEDIASENSE_COOKIES_FROM_BROWSER")
    if browser:
        last = dict(attempts[-1][1])
        last["cookiesfrombrowser"] = (browser.strip().lower(),)
        attempts.append((f"with {browser} cookies", last))
    return attempts


def _worth_retrying(message: str) -> bool:
    m = message.lower()
    # these won't change with a different client
    return not any(x in m for x in ("unsupported url", "private video", "video unavailable",
                                    "is not a valid url", "removed", "404"))


def _download(yt_dlp, url: str, out_dir: Path, opts: dict) -> Path:
    with yt_dlp.YoutubeDL(opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
        except Exception as e:  # yt-dlp raises its own DownloadError subclasses
            raise FetchError(_clean(e)) from e
        if info.get("_type") == "playlist" and info.get("entries"):
            info = next(iter(info["entries"]))
        cached = _cached_file(out_dir, info)
        if cached:
            return cached
        try:
            info = ydl.extract_info(info.get("webpage_url") or url, download=True)
        except Exception as e:
            raise FetchError(_clean(e)) from e
    path = _downloaded_path(info, out_dir)
    if not path or not path.exists():
        raise FetchError("download finished but the file was not found")
    _write_info(path, info)
    return path


def _clean(e: Exception) -> str:
    msg = str(e).splitlines()[0]
    msg = msg.replace("ERROR: ", "")
    return msg.split("; please report this issue")[0]


def _find_downloaded(url: str, out_dir: Path) -> Path | None:
    """Cached copy of ``url`` without touching the network: match the .info.json we keep per download."""
    import json

    want = url.split("?")[0].rstrip("/")
    for info_file in out_dir.glob("*.info.json"):
        try:
            seen = (json.loads(info_file.read_text(encoding="utf-8")).get("webpage_url") or "").split("?")[0]
        except (OSError, ValueError):
            continue
        if seen.rstrip("/") == want:
            stem = info_file.name[: -len(".info.json")]
            for p in out_dir.glob(stem + ".*"):
                if p.is_file() and p.suffix.lower() not in (".json", ".part", ".ytdl"):
                    return p
    return None


def _stem(info: dict) -> str:
    return f"{_safe(str(info.get('extractor', 'web')))}_{_safe(str(info.get('id', 'media')))}"


def _cached_file(out_dir: Path, info: dict) -> Path | None:
    stem = _stem(info).lower()
    for p in out_dir.iterdir():
        if p.is_file() and p.stem.lower() == stem and p.suffix.lower() not in (".json", ".part", ".ytdl"):
            return p
    return None


def _downloaded_path(info: dict, out_dir: Path) -> Path | None:
    for d in info.get("requested_downloads") or []:
        fp = d.get("filepath") or d.get("_filename")
        if fp:
            return Path(fp)
    if info.get("filepath"):
        return Path(info["filepath"])
    return _cached_file(out_dir, info)


def _write_info(path: Path, info: dict) -> None:
    """Keep title/author/description next to the file – useful context for summaries."""
    import json

    keep = {k: info.get(k) for k in ("title", "uploader", "channel", "description", "webpage_url",
                                      "upload_date", "duration", "view_count", "like_count", "tags")}
    try:
        path.with_suffix(".info.json").write_text(json.dumps(keep, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def read_info(path: str | Path) -> dict | None:
    import json

    p = Path(path).with_suffix(".info.json")
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
    return None


def resolve(path_or_url: str, download: bool = True) -> Path:
    """File path or link -> local file path."""
    if is_url(path_or_url):
        return fetch(path_or_url, download=download)
    p = Path(os.path.expanduser(path_or_url.strip().strip('"'))).resolve()
    if not p.exists():
        raise FileNotFoundError(f"file not found: {p}")
    return p

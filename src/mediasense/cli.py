"""mediasense command-line interface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, api
from .media import MediaError, place_audio, probe
from .models import Transcript, fmt_time, parse_time


def _p(*a, **k):
    print(*a, **k, file=sys.stderr)


# ------------------------------------------------------------ commands
def _media(value: str) -> str:
    """CLI arguments may be a link; download it (once) and use the local copy."""
    from .fetch import is_url, resolve

    if is_url(value):
        _p("downloading…")
        path = str(resolve(value))
        _p(f"saved to {path}")
        return path
    return value


def cmd_info(args) -> int:
    args.file = _media(args.file)
    info = probe(args.file)
    print(json.dumps(info.to_dict(), indent=2))
    return 0


def cmd_transcribe(args) -> int:
    from .transcribe import transcribe

    args.file = _media(args.file)
    tr = transcribe(args.file, backend=args.backend, model=args.model, language=args.language,
                    device=args.device, verbatim=args.verbatim, diarize=args.diarize,
                    hf_token=args.hf_token, progress=None if args.quiet else _p, vocabulary=args.vocab)
    out = Path(args.output) if args.output else api.default_transcript_path(args.file)
    tr.save(out)
    _p(f"language: {tr.language} · {len(tr.words())} words · {fmt_time(tr.duration or 0)}")
    print(out)
    for extra in args.also or []:
        p = out.with_name(out.name.replace(".transcript.json", "") + "." + extra.lstrip("."))
        api.write_subtitles(tr, p)
        print(p)
    return 0


def cmd_show(args) -> int:
    tr = Transcript.load(args.transcript)
    start = parse_time(args.start) if args.start else None
    end = parse_time(args.end) if args.end else None
    print(api.transcript_excerpt(tr, start, end, words=args.words, max_chars=10**9))
    return 0


def cmd_search(args) -> int:
    from .edit import find_phrase

    tr = Transcript.load(args.transcript)
    hits = find_phrase(tr, args.phrase)
    if args.json:
        print(json.dumps([h.to_dict() for h in hits], ensure_ascii=False, indent=2))
    else:
        for h in hits:
            print(f"{fmt_time(h.start)} → {fmt_time(h.end)}  {h.text}")
        _p(f"{len(hits)} match(es)")
    return 0 if hits else 1


def cmd_edit(args) -> int:
    args.file = _media(args.file)
    tr = api.load_transcript_for(args.file, args.transcript)
    needs_tr = bool(
        args.cut or args.cut_between or args.fillers or args.filler_word
        or (args.silence_source == "transcript" and args.silences is not None)
    )
    if needs_tr and tr is None:
        _p(f"error: no transcript found. Run: mediasense transcribe {args.file}")
        return 2
    plan, info, report = api.plan_edit(
        args.file, tr,
        cut_phrases=args.cut or [],
        cut_between=[tuple(x) for x in (args.cut_between or [])],
        cut_ranges=api.split_ranges(args.cut_range or []),
        keep_only=api.parse_range(args.keep_only) if args.keep_only else None,
        fillers=args.fillers, filler_words=args.filler_word or [],
        silences=args.silences, silence_keep=args.silence_keep,
        silence_source=args.silence_source, noise_db=args.noise_db,
        occurrence=args.occurrence, pad=args.pad,
    )
    for nf in report["not_found"]:
        _p(f"warning: not found in transcript: {nf!r}")
    if args.json:
        print(json.dumps({**plan.to_dict(), "report": report}, ensure_ascii=False, indent=2))
    else:
        _p(plan.summary(limit=10**6 if args.verbose else 40))
    if args.plan_out:
        Path(args.plan_out).write_text(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    if args.dry_run:
        return 0
    if not plan.cuts:
        _p("nothing to cut.")
        return 0
    defaults = api.outputs_for(args.file)
    written = api.apply_plan(
        args.file, plan, info, tr,
        output=None if args.no_render else (args.output or defaults["media"]),
        edl=(defaults["edl"] if args.edl is True else args.edl) if args.edl else None,
        fcpxml=(defaults["fcpxml"] if args.fcpxml is True else args.fcpxml) if args.fcpxml else None,
        subtitles=(defaults["srt"] if args.srt is True else args.srt) if args.srt else None,
        transcript_out=defaults["transcript"] if tr is not None else None,
    )
    for kind, path in written.items():
        print(f"{kind}: {path}")
    return 0


def cmd_export(args) -> int:
    tr = Transcript.load(args.transcript)
    for out in args.output:
        api.write_subtitles(tr, out, words_per_cue=args.words_per_cue, max_chars=args.max_chars)
        print(out)
    return 0


def cmd_place(args) -> int:
    out = args.output or str(Path(args.base).with_name(Path(args.base).stem + ".placed" + Path(args.base).suffix))
    place_audio(args.base, args.insert, out, at=parse_time(args.at), mode=args.mode,
                volume=args.volume, duck_db=args.duck_db,
                insert_start=parse_time(args.insert_from) if args.insert_from else 0.0,
                insert_end=parse_time(args.insert_to) if args.insert_to else None)
    print(out)
    return 0


def cmd_storyboard(args) -> int:
    from .video import storyboard

    args.file = _media(args.file)
    tr = api.load_transcript_for(args.file, args.transcript)
    out = args.output or str(Path(args.file).with_name(Path(args.file).stem + ".storyboard"))
    data = storyboard(args.file, out, tr, threshold=args.threshold, max_frames=args.max_frames,
                      min_gap=args.min_gap, max_gap=args.max_gap, width=args.width)
    _p(f"{len(data['shots'])} shots, {len(data['contact_sheets'])} contact sheet(s)"
       + ("" if tr else " — no transcript found, speech column empty"))
    print(Path(out) / "storyboard.md")
    return 0


def cmd_understand(args) -> int:
    data = api.understand(args.source, language=args.language, model=args.model, vocabulary=args.vocab,
                          max_frames=args.max_frames, force=args.force, progress=_p)
    print(api.understand_report(data, max_chars=10**9))
    if data.get("storyboard_dir"):
        _p(f"\nkey frames: {data['storyboard_dir']}")
    return 0


def cmd_fetch(args) -> int:
    from .fetch import fetch

    print(fetch(args.url, dest=args.output, audio_only=args.audio))
    return 0


def cmd_setup(args) -> int:
    from .setup import setup

    only_desktop, only_code = args.desktop, args.code
    lines = setup(desktop=not only_code or only_desktop, code=not only_desktop or only_code,
                  cookies_browser=args.cookies_browser, dry_run=args.dry_run)
    for line in lines:
        print(line)
    if not args.dry_run:
        print("\nTry it: ask Claude \"summarize this video: <link>\". Run `mediasense doctor` if something's off.")
    return 0


def cmd_skill(args) -> int:
    from .skill import DESKTOP_HOWTO, install_claude_code_skill, skill_text, write_skill_zip

    if args.print:
        print(skill_text())
        return 0
    if args.claude_code:
        print(install_claude_code_skill())
        return 0
    from .fetch import download_dir

    path = write_skill_zip(args.output or download_dir())
    print(DESKTOP_HOWTO.format(zip=path))
    return 0


def cmd_doctor(args) -> int:
    from .setup import doctor

    worst = 0
    for name, ok, detail in doctor():
        mark = "OK " if ok else ("-- " if ok is None else "!! ")
        if ok is False:
            worst = 1
        print(f"{mark} {name:<36} {detail}")
    return worst


def cmd_mcp(args) -> int:
    from .mcp_server import main

    main()
    return 0


# ------------------------------------------------------------ parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mediasense",
        description="Transcribe audio/video with word-level timestamps, then edit by text.",
    )
    p.add_argument("--version", action="version", version=f"mediasense {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("understand", help="transcript + key frames + metadata of a file or link, in one go")
    s.add_argument("source", help="file path or link (TikTok, YouTube, Instagram, X…)")
    s.add_argument("-l", "--language")
    s.add_argument("-m", "--model", default="small")
    s.add_argument("--vocab", metavar="WORDS", help='names/jargon to spell right, e.g. "Cloudflare, Claude"')
    s.add_argument("--max-frames", type=int, default=24)
    s.add_argument("--force", action="store_true", help="redo transcript and storyboard")
    s.set_defaults(func=cmd_understand)

    s = sub.add_parser("fetch", help="download a video/audio from a link (TikTok, YouTube…)")
    s.add_argument("url")
    s.add_argument("-o", "--output", help="folder (default: ~/Downloads/mediasense)")
    s.add_argument("--audio", action="store_true", help="audio only")
    s.set_defaults(func=cmd_fetch)

    s = sub.add_parser("info", help="show media properties")
    s.add_argument("file")
    s.set_defaults(func=cmd_info)

    s = sub.add_parser("transcribe", help="speech -> text with a timestamp for every word")
    s.add_argument("file", help="file path or link")
    s.add_argument("-o", "--output", help="transcript JSON (default: <file>.transcript.json)")
    s.add_argument("-b", "--backend", default="faster-whisper", choices=["faster-whisper", "whisperx", "openai"])
    s.add_argument("-m", "--model", help="model size/name: tiny, base, small, medium, large-v3, turbo …")
    s.add_argument("-l", "--language", help="ISO code (pt, en, ja…). Omit to auto-detect")
    s.add_argument("--device", default="auto", help="auto, cpu or cuda")
    s.add_argument("--verbatim", action="store_true", help="keep hesitations (um, é…, hum) so they can be cut")
    s.add_argument("--diarize", action="store_true", help="label speakers (whisperx + HF token)")
    s.add_argument("--hf-token", help="Hugging Face token for diarization (or HF_TOKEN env)")
    s.add_argument("--vocab", metavar="WORDS", help='names/jargon to spell right, e.g. "Cloudflare, Claude"')
    s.add_argument("--also", nargs="*", metavar="EXT", help="also write e.g. srt vtt txt csv")
    s.add_argument("-q", "--quiet", action="store_true")
    s.set_defaults(func=cmd_transcribe)

    s = sub.add_parser("show", help="print the transcript with timestamps")
    s.add_argument("transcript")
    s.add_argument("--start")
    s.add_argument("--end")
    s.add_argument("-w", "--words", action="store_true", help="show the start time of every word")
    s.set_defaults(func=cmd_show)

    s = sub.add_parser("search", help="find where a word or phrase is said")
    s.add_argument("transcript")
    s.add_argument("phrase")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_search)

    s = sub.add_parser("edit", help="cut by text, fillers, silences or time ranges")
    s.add_argument("file", help="audio or video to edit")
    s.add_argument("-t", "--transcript", help="default: <file>.transcript.json")
    s.add_argument("-o", "--output", help="edited media (default: <file>.edited.<ext>)")
    g = s.add_argument_group("what to remove")
    g.add_argument("--cut", action="append", metavar="PHRASE", help="remove a phrase (every occurrence)")
    g.add_argument("--occurrence", type=int, help="with --cut: only the N-th occurrence (1-based)")
    g.add_argument("--cut-between", nargs=2, action="append", metavar=("FROM", "TO"),
                   help="remove from phrase FROM through phrase TO")
    g.add_argument("--cut-range", action="append", metavar="START-END", help="e.g. 1:02-1:05.5")
    g.add_argument("--keep-only", metavar="START-END", help="trim to this range")
    g.add_argument("--fillers", action="store_true", help="remove hesitations (um, uh, éé, hum, euh, äh…)")
    g.add_argument("--filler-word", action="append", metavar="WORD",
                   help="extra words to treat as fillers, e.g. 'tipo', 'you know'")
    g.add_argument("--silences", type=float, metavar="SECONDS", help="shorten pauses longer than this")
    g.add_argument("--silence-keep", type=float, default=0.2, help="pause length left behind (default 0.2s)")
    g.add_argument("--silence-source", choices=["audio", "transcript"], default="audio",
                   help="detect pauses from the audio level or from gaps between words (better with music)")
    g.add_argument("--noise-db", type=float, default=-35.0, help="silence threshold in dB (default -35)")
    g.add_argument("--pad", type=float, default=0.12, help="max seconds to extend word cuts into pauses")
    o = s.add_argument_group("output")
    o.add_argument("-n", "--dry-run", action="store_true", help="only show what would be cut")
    o.add_argument("--no-render", action="store_true", help="don't write media, only exports")
    o.add_argument("--edl", nargs="?", const=True, metavar="PATH", help="CMX3600 EDL (Premiere/Resolve/Avid)")
    o.add_argument("--fcpxml", nargs="?", const=True, metavar="PATH", help="FCPXML (Final Cut/Resolve)")
    o.add_argument("--srt", nargs="?", const=True, metavar="PATH", help="subtitles re-timed to the edit")
    o.add_argument("--plan-out", metavar="PATH", help="save the plan as JSON")
    o.add_argument("--json", action="store_true", help="print the plan as JSON")
    o.add_argument("-v", "--verbose", action="store_true", help="list every cut")
    s.set_defaults(func=cmd_edit)

    s = sub.add_parser("export", help="transcript -> .srt .vtt .txt .csv .json")
    s.add_argument("transcript")
    s.add_argument("output", nargs="+")
    s.add_argument("--words-per-cue", type=int, help="short captions, e.g. 2 for vertical video")
    s.add_argument("--max-chars", type=int, default=42)
    s.set_defaults(func=cmd_export)

    s = sub.add_parser("place", help="put an audio clip into a video/audio at a timestamp")
    s.add_argument("base", help="video or audio that receives the clip")
    s.add_argument("insert", help="audio (or video) whose sound is inserted")
    s.add_argument("--at", required=True, help="where in BASE it starts, e.g. 1:23.5")
    s.add_argument("--mode", default="mix", choices=["mix", "duck", "replace"])
    s.add_argument("--volume", type=float, default=1.0)
    s.add_argument("--duck-db", type=float, default=-15.0)
    s.add_argument("--from", dest="insert_from", help="start of the slice of INSERT to use")
    s.add_argument("--to", dest="insert_to", help="end of the slice of INSERT to use")
    s.add_argument("-o", "--output")
    s.set_defaults(func=cmd_place)

    s = sub.add_parser("storyboard", help="key frames + speech, so an AI can 'watch' a video")
    s.add_argument("file")
    s.add_argument("-t", "--transcript")
    s.add_argument("-o", "--output", help="output folder (default: <file>.storyboard/)")
    s.add_argument("--threshold", type=float, help="scene-change sensitivity 0–1, lower = more (auto)")
    s.add_argument("--max-frames", type=int, default=40)
    s.add_argument("--min-gap", type=float, default=1.5)
    s.add_argument("--max-gap", type=float, help="add a frame at least this often, seconds (auto)")
    s.add_argument("--width", type=int, default=768)
    s.set_defaults(func=cmd_storyboard)

    s = sub.add_parser("setup", help="connect mediasense to Claude Desktop and Claude Code")
    s.add_argument("--desktop", action="store_true", help="only Claude Desktop")
    s.add_argument("--code", action="store_true", help="only Claude Code")
    s.add_argument("--cookies-browser", metavar="NAME",
                   help="let link downloads use this browser's cookies (e.g. firefox), for sites that block bots")
    s.add_argument("-n", "--dry-run", action="store_true", help="show what would change")
    s.set_defaults(func=cmd_setup)

    s = sub.add_parser("skill", help="the Claude skill: zip for Claude Desktop, or install for Claude Code")
    s.add_argument("-o", "--output", help="where to write mediasense-skill.zip (default: ~/Downloads/mediasense)")
    s.add_argument("--claude-code", action="store_true", help="install into ~/.claude/skills instead")
    s.add_argument("--print", action="store_true", help="print SKILL.md")
    s.set_defaults(func=cmd_skill)

    s = sub.add_parser("doctor", help="check FFmpeg, model, GPU, links and the Claude connection")
    s.set_defaults(func=cmd_doctor)

    s = sub.add_parser("mcp", help="run the MCP server (stdio) for Claude and other MCP clients")
    s.set_defaults(func=cmd_mcp)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (MediaError, FileNotFoundError, ValueError, RuntimeError) as e:
        _p(f"error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

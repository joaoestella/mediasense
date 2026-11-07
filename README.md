# wordcut

**Give AI assistants ears and eyes for your media.** wordcut transcribes audio and video in ~99 languages
with a timestamp for every word, lets you **edit by text** (cut phrases, filler words and long pauses),
exports subtitles and timelines for your video editor, drops audio clips into a video at the right moment,
and builds storyboards so an AI can **watch a video** and explain it.

Everything runs locally on your machine. Use it from the command line or plug it into Claude (or any
[MCP](https://modelcontextprotocol.io) client) and just ask in plain language.

[Português 🇧🇷](README.pt-BR.md)

```text
"Remove every 'um' and shorten the pauses"     → talk.edited.mp4 + talk.edited.srt
"When does she mention the budget?"            → 03:41.200 → 03:42.050  "the budget"
"Cut from 'off the record' to 'anyway'"        → plan shown, confirmed, rendered
"What happens in this video?"                  → shot-by-shot summary with timestamps
"Put intro.mp3 under the logo at 0:12, ducked" → talk.placed.mp4
```

## Features

| | |
|---|---|
| 🎙️ **Transcribe** | faster-whisper (default), WhisperX (tighter word timing + speaker labels) or any OpenAI-compatible API. Auto language detection. |
| ⏱️ **Word timestamps** | Every word has start/end times, in one JSON format you can reuse anywhere. |
| ✂️ **Edit by text** | Cut phrases, passages ("from X to Y"), filler words (um, uh, éé, hum, euh, äh, えーと…), long silences, or time ranges. Preview first; the original is never touched. |
| 🎬 **Editor round-trip** | CMX3600 **EDL** (Premiere, Resolve, Avid) and **FCPXML** (Final Cut Pro, Resolve) so you keep editing on your own timeline. |
| 💬 **Subtitles** | SRT/VTT, balanced two-line cues, or 1–3-word "Reels/TikTok" captions; re-timed automatically after edits. |
| 🔊 **Place audio** | Insert a clip at a timestamp in mix, duck or replace mode. Video is stream-copied. |
| 👀 **Watch video** | Scene-change key frames + what is said in each shot, as Markdown/JSON and labelled contact sheets an AI can read. |
| 🤖 **MCP server + Claude skill** | Ten tools for Claude Desktop, Claude Code and other MCP clients. |

## Install

Requirements: Python 3.10+ and [FFmpeg](https://ffmpeg.org/download.html)
(`brew install ffmpeg` · `winget install ffmpeg` · `sudo apt install ffmpeg`).

```bash
pipx install "wordcut[all] @ git+https://github.com/OWNER/wordcut"
# or: uv tool install "wordcut[all] @ git+https://github.com/OWNER/wordcut"
# or, in a virtualenv: pip install "wordcut[all] @ git+https://github.com/OWNER/wordcut"
```

Extras: `whisper` (faster-whisper), `whisperx`, `api` (OpenAI-compatible), `mcp`, `all` (= whisper + mcp).
The first transcription downloads the model (≈ 75 MB for `tiny`, ≈ 500 MB for `small`, ≈ 3 GB for `large-v3`).

## Use with Claude

**Claude Code**

```bash
claude mcp add wordcut -- wordcut mcp
```

**Claude Desktop** – Settings → Developer → Edit Config, then add:

```json
{
  "mcpServers": {
    "wordcut": { "command": "wordcut", "args": ["mcp"] }
  }
}
```

If Claude can't find the command, use the full path from `which wordcut` (macOS/Linux) or
`where wordcut` (Windows). Restart Claude, then ask things like *"transcribe ~/Desktop/interview.m4a and
tell me when they talk about pricing"*.

**Skill (optional):** copy `skill/wordcut/` into your skills folder (Claude Code: `~/.claude/skills/`), or
upload it as a skill in Claude. It teaches Claude the workflow: transcribe once, preview cuts, confirm,
then render.

### MCP tools

| Tool | What it does |
|---|---|
| `transcribe` | speech → `<file>.transcript.json` (reused on later calls) |
| `read_transcript` | text with timestamps, optional time range or per-word times |
| `search_transcript` | every time a phrase is said |
| `plan_edit` | preview cuts (phrases, passages, fillers, silences, ranges) |
| `render_edit` | write the edited file and/or EDL, FCPXML, SRT |
| `export_transcript` | SRT, VTT, TXT, CSV, JSON |
| `place_audio` | insert a clip at a timestamp (mix / duck / replace) |
| `watch_video` | contact sheets + speech per shot |
| `get_frame` | one frame at a given time |
| `media_info` | duration, resolution, fps, streams |

## Command line

```bash
wordcut transcribe interview.mp4                  # -> interview.transcript.json
wordcut transcribe interview.mp4 -m large-v3 -l pt --also srt txt
wordcut transcribe podcast.mp3 --verbatim         # keep "um"s so they can be cut
wordcut show interview.transcript.json --words    # word@seconds
wordcut search interview.transcript.json "next quarter"

# edit by text – always preview with -n first
wordcut edit interview.mp4 --fillers --silences 0.7 -n
wordcut edit interview.mp4 --fillers --silences 0.7 --srt --edl --fcpxml
wordcut edit interview.mp4 --cut "off the record" --cut-between "let me check" "okay so" --cut-range 12:03-12:40
wordcut edit interview.mp4 --keep-only 1:00-2:30 -o clip.mp4
wordcut edit vlog.mp4 --fillers --filler-word tipo --filler-word "you know"

wordcut export interview.transcript.json captions.srt --words-per-cue 2
wordcut place video.mp4 voiceover.wav --at 0:12.5 --mode duck --duck-db -18
wordcut place video.mp4 interview.mp4 --at 30 --from 1:02 --to 1:09   # reuse a line from another file
wordcut storyboard talk.mp4                       # -> talk.storyboard/storyboard.md + contact sheets
```

`--silence-source transcript` detects pauses from gaps between words, which works better than audio
level when there is background music.

### Transcript format

```json
{
  "schema": 1, "language": "pt", "duration": 6.0, "backend": "faster-whisper", "model": "small",
  "segments": [
    { "start": 0.0, "end": 1.0, "text": "Olá, pessoal!",
      "words": [ { "text": " Olá,", "start": 0.05, "end": 0.45, "confidence": 0.98 },
                 { "text": " pessoal!", "start": 0.5, "end": 0.95, "confidence": 0.91 } ] }
  ]
}
```

## Backends and models

| Backend | Install | Best for |
|---|---|---|
| `faster-whisper` (default) | `wordcut[whisper]` | Good accuracy, CPU or NVIDIA GPU, simplest install |
| `whisperx` | `wordcut[whisperx]` | Most precise word boundaries (forced alignment) and `--diarize` speaker labels (needs a Hugging Face token in `HF_TOKEN`) |
| `openai` | `wordcut[api]` | No local model. Uses `OPENAI_API_KEY`; set `OPENAI_BASE_URL` for compatible providers. Files over 25 MB must be split. |

Models: `tiny`, `base`, `small` (default), `medium`, `large-v3`, `turbo`. On CPU, `small` runs roughly at
real-time speed on a modern laptop; `large-v3` is several times slower. A CUDA GPU is used automatically.

## Good to know

- **Whisper cleans up speech.** It often leaves out hesitations. Use `--verbatim` (MCP: `verbatim=true`)
  when you want to cut fillers; it primes the model to keep them. Ambiguous words like "like", "tipo" or
  "né" are never removed unless you list them.
- **Word timing is approximate** (typically within 0.1–0.2 s with faster-whisper, tighter with WhisperX).
  Cuts extend slightly into the surrounding pauses (`--pad`, default 0.12 s) and each join gets an 8 ms
  fade to avoid clicks.
- Edits re-encode (H.264/AAC for MP4/MOV) so cuts are frame-accurate. `place` copies the video stream.
- EDL timecode is non-drop-frame. When importing, relink to the original file if the editor asks.

## Development

```bash
git clone https://github.com/OWNER/wordcut && cd wordcut
pip install -e ".[dev,mcp]"
pytest
```

Tests generate synthetic audio/video with FFmpeg and use fake model libraries, so they run in seconds
without downloading anything. Contributions welcome. Good first issues: more filler words for your
language, an FCP7 XML exporter, and a GUI.

## License

MIT

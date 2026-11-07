# mediasense

**Give Claude ears and eyes.** Paste a TikTok, YouTube or Instagram link (or point at a file) in Claude
Code or Claude Desktop and ask in plain language. Claude can then summarize the video, explain what
happens in it, find the moment something is said, or edit it.

```text
you: summarize this https://www.tiktok.com/@someone/video/7350…
you: what does he show at 0:30? and what tool is he talking about?
you: clean up ~/Desktop/podcast.m4a — remove the "um"s and long pauses
you: make short captions for ~/Videos/reel.mp4
you: put intro.mp3 under the first 5 seconds of talk.mp4, ducked
```

mediasense runs **on your computer**: it downloads the link, transcribes speech in ~99 languages with a
timestamp for every word, picks key frames at scene changes, and hands Claude a transcript, a shot list
and contact-sheet images. Nothing is uploaded anywhere.

[Português 🇧🇷](README.pt-BR.md)

## Setup

**Windows** – paste in PowerShell:

```powershell
irm https://raw.githubusercontent.com/joaoestella/mediasense/main/install.ps1 | iex
```

**macOS / Linux** – paste in a terminal:

```bash
curl -LsSf https://raw.githubusercontent.com/joaoestella/mediasense/main/install.sh | sh
```

That installs whatever is missing (Python, FFmpeg, pipx), installs mediasense and connects it to Claude
Desktop and Claude Code. Then **quit Claude Desktop completely** (tray/menu-bar icon → Quit), open it
again and ask *"summarize this video: <link>"*. The first transcription downloads the speech model
(≈ 500 MB).

<details>
<summary>Manual setup</summary>

1. Install FFmpeg: `winget install ffmpeg` · `brew install ffmpeg` · `sudo apt install ffmpeg`
2. `pipx install "mediasense[all] @ git+https://github.com/joaoestella/mediasense"`
3. `mediasense setup` – adds mediasense to Claude Desktop's config (keeping your other settings and a
   backup) and to Claude Code if it's installed. `--desktop` / `--code` to pick one, `-n` to preview.

</details>

Something not working? `mediasense doctor` checks FFmpeg, the speech model, the GPU, link downloads and
the Claude connection.

> **Note:** Claude Desktop sends files you attach in the chat to the model, not to your computer, so give
> mediasense a **link or a file path** instead (in Claude Code you can drag the file into the terminal).

### Slash commands

The server adds ready-made prompts. In Claude Code they appear as `/mcp__mediasense__summarize` and so on.

| Prompt | Does |
|---|---|
| `summarize` | gist, key points with timestamps, steps, names and links, call to action |
| `explain_video` | shot-by-shot explanation, plus how the video is built (hook, structure) |
| `clean_up` | remove fillers and long pauses, show the plan first, render after your OK |
| `subtitles` | SRT captions, standard or short 1–3-word style for vertical video |

### Tools Claude gets

| Tool | What it does |
|---|---|
| `understand` | **one call**: download link, transcribe, key frames + speech per shot, post title/description |
| `transcribe` | speech → `<file>.transcript.json` with word timestamps; `vocabulary` fixes brand names |
| `read_transcript`, `search_transcript` | read a time range or find every time a phrase is said |
| `plan_edit`, `render_edit` | cut phrases, passages, fillers, silences or ranges; preview, then render |
| `export_transcript` | SRT, VTT, TXT, CSV, JSON |
| `place_audio` | insert a clip at a timestamp (mix / duck / replace) |
| `watch_video`, `get_frame` | contact sheets, or one frame at a given time |
| `media_info` | duration, resolution, fps, streams |

All tools accept a file path **or a link**. Links are downloaded once to `~/Downloads/mediasense`
(change with `MEDIASENSE_HOME`) and reused.

## Command line

Everything also works without Claude:

```bash
mediasense understand "https://youtu.be/…"           # transcript + shots + metadata, as text
mediasense transcribe talk.mp4 -l pt --vocab "Cloudflare, Claude" --also srt
mediasense search talk.transcript.json "next quarter"
mediasense edit talk.mp4 --fillers --silences 0.7 -n   # -n = only show the plan
mediasense edit talk.mp4 --fillers --silences 0.7 --srt --edl --fcpxml
mediasense edit talk.mp4 --cut-between "let me check" "okay so" --cut-range 12:03-12:40
mediasense export talk.transcript.json captions.srt --words-per-cue 2
mediasense place video.mp4 voiceover.wav --at 0:12.5 --mode duck
mediasense storyboard reel.mp4
mediasense fetch "https://www.tiktok.com/@…/video/…"
```

Edits never touch the original: results are written as `<name>.edited.*` / `<name>.placed.*`.
EDL opens in Premiere, Resolve and Avid; FCPXML in Final Cut Pro and Resolve.

## Good to know

- **Names and jargon.** Whisper may write "Cloud Fair" for Cloudflare. Pass `--vocab` (Claude does this
  automatically when it can infer the names from the title or the screen).
- **Fillers.** Whisper tends to leave hesitations out; transcribe with `--verbatim` when you want to cut
  them. Real words are protected per language ("um" is an article in Portuguese, so it is never cut
  there). Ambiguous words like "like", "tipo" or "né" are only removed if you list them.
- **Speed.** A CUDA GPU is used automatically when its libraries are installed; otherwise mediasense
  falls back to the CPU (roughly real-time with `small`). On a laptop CPU, `base` is faster and less
  accurate, `medium` is slower and more accurate.
- **Word timing** is approximate (0.1–0.2 s). For tighter cuts and speaker labels use
  `--backend whisperx` (`pip install "mediasense[whisperx]"`).
- Downloading from links follows each site's terms. Use it for content you're allowed to download.

## Development

```bash
git clone https://github.com/joaoestella/mediasense && cd mediasense
pip install -e ".[dev,mcp]"
pytest
```

The tests build synthetic audio/video with FFmpeg and use fake model and downloader libraries, so they
run in seconds with nothing to download. Ideas welcome: more filler words for your language, a
one-click Claude Desktop extension (`.mcpb`), and a GUI.

## License

MIT

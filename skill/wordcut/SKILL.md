---
name: wordcut
description: Listen to audio and watch video the user provides - transcribe speech in any language with a timestamp for every word, find when something is said, cut audio/video by text (phrases, filler words, silences), make subtitles or EDL/FCPXML for video editors, drop an audio clip into a video at the right moment, and explain what happens in a video. Use whenever the user shares or mentions an audio/video file and wants it understood, transcribed, timed, edited or captioned.
---

# wordcut

wordcut gives you ears and eyes for media files on the user's machine. Use the `wordcut` MCP tools when
they are connected; otherwise run the `wordcut` CLI in a shell (same features, `wordcut --help`).

## Golden rules

- **Never modify the original.** Every output is a new file (`<name>.edited.mp4`, `<name>.placed.mp4`…).
- **Preview before cutting.** Run `plan_edit` (CLI: `wordcut edit … --dry-run`), show the user the cut list
  in plain words ("I'll remove 14 'um's and shorten 6 pauses, 2:31 → 2:12"), then `render_edit`.
- **Transcribe once.** The transcript is saved as `<name>.transcript.json` next to the media and every
  other tool finds it automatically.
- Times are seconds (`75.5`) or `m:ss.s` (`1:15.5`). Quote timestamps back to the user as `m:ss`.

## Workflows

### "What does this audio say?" / "Summarize this voice note"
1. `transcribe(path)` — language is auto-detected; pass `language` if detection is wrong.
2. Answer from the transcript, citing times (`at 0:42 she says…`).

### "When does he say X?"
`search_transcript(path, "X")` → list of start/end times. Case, accents and punctuation are ignored.

### Cutting by text
- Remove a phrase: `cut_phrases=["off the record"]` (all occurrences; `occurrence=2` for only the 2nd).
- Remove a passage: `cut_between=[["first words of it", "last words of it"]]`.
- Remove fillers: transcribe with `verbatim=true` first (Whisper otherwise hides most "um"s), then
  `fillers=true`. Context-dependent words ("like", "tipo", "né") only via `filler_words` after asking.
- Tighten pauses: `silences=0.7` (pauses over 0.7 s shrink to `silence_keep`, default 0.2 s). With music in
  the background use `silence_source="transcript"`.
- Raw times: `cut_ranges=["1:02-1:05.5"]`, `keep_only="0:30-2:00"`.
- For people who edit in Premiere/Resolve/Final Cut, add `edl=true` or `fcpxml=true` and they can keep
  editing on their own timeline. `srt=true` gives subtitles matching the edited version.

### Subtitles
`export_transcript(path, ["srt"])`; `words_per_cue=2` for TikTok/Reels style captions.

### Putting audio into a video
1. Transcribe the video and use `search_transcript` / `watch_video` to find the moment.
2. `place_audio(base=video, insert=audio, at="1:23.4", mode="duck")` — `mix`, `duck` (lowers the original) or
   `replace`. `insert_from`/`insert_to` use only a slice of the clip (find it by transcribing the clip too).

### "What happens in this video?"
1. `transcribe(path)` (so shots include speech), then `watch_video(path)`.
2. Look at the contact sheets: each tile shows shot number and start time; the text lists what is said per
   shot. Describe the video chronologically with timestamps; `get_frame(path, "2:10")` to inspect detail.

## Choosing a model
`tiny`/`base`: fast drafts · `small` (default): good balance · `medium`/`large-v3`: best accuracy, slow on
CPU · `turbo`: near large-v3 quality, faster. If a GPU is available it is used automatically.
`backend="whisperx"` gives tighter word boundaries (best for precise cuts) and speaker labels.

## Troubleshooting
- "ffmpeg not found": the user must install FFmpeg (brew/winget/apt install ffmpeg).
- "backend needs …": `pip install "wordcut[whisper]"` (or `[whisperx]`, `[api]`).
- Cut sounds clipped: lower `pad` (CLI `--pad 0.05`) or use the whisperx backend.
- Phrase "not found": `read_transcript(path, start, end)` to see how the model actually spelled it.

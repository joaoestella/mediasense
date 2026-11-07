---
name: mediasense
description: Watch and listen to videos and audio for the user - TikTok, YouTube, Instagram Reels, X, Vimeo links or local files (voice notes, podcasts, recordings). Use whenever the user shares a media link or file and wants it summarized, explained, transcribed, searched, captioned or edited, using the mediasense tools instead of fetching the page or opening a browser.
---

# mediasense

mediasense runs on the user's computer and gives you ears and eyes for media: it downloads links,
transcribes speech (any language, timestamp per word), picks key frames, and edits audio/video by text.

## First: use the tools, not the web

When the user pastes a TikTok / YouTube / Instagram / X / Vimeo link or points at a media file:

- **Do not** WebFetch the page or open it in a browser. Those sites block bots, and the page has no
  transcript anyway. Go straight to the mediasense tools.
- The tools are named `mediasense` → `understand_media`, `transcribe`, `check_job`, … In some apps they
  are listed but not loaded yet: search your tools for "mediasense" and load them.
- If no mediasense tool exists at all, mediasense isn't installed or Claude Desktop wasn't restarted.
  Say so and point to https://github.com/joaoestella/mediasense#setup (quit Claude Desktop from the tray
  icon and reopen it after installing). In a terminal you can also run the CLI: `mediasense --help`.

## Golden rules

- **Long jobs.** `understand_media`, `transcribe`, `render_edit` and `watch_video` reply
  `STILL WORKING — job <id>` when they need more than ~30 s (long videos on a CPU; the first run also
  downloads the ~500 MB speech model). Tell the user it's processing, with the percentage, then call
  `check_job(job_id)` until the result arrives. Don't start the request again.
- **Never modify the original.** Every output is a new file (`<name>.edited.mp4`, `<name>.placed.mp4`…).
- **Preview before cutting.** Run `plan_edit` (CLI: `mediasense edit … --dry-run`), show the user the cut
  list in plain words ("I'll remove 14 'um's and shorten 6 pauses, 2:31 → 2:12"), then `render_edit`.
- **Transcribe once.** The transcript is saved as `<name>.transcript.json` next to the media and every
  other tool finds it automatically.
- Times are seconds (`75.5`) or `m:ss.s` (`1:15.5`). Quote timestamps back to the user as `m:ss`.

## Workflows

### "Summarize this" / "What is this video about?" / a pasted link
1. `understand_media(source)` with the link or file path. One call returns the post metadata, the
   transcript and, for videos, contact sheets of key frames with what is said per shot.
2. Pass `vocabulary` with names you can infer (title, hashtags, the user's message) and `language` if
   known.
3. **Cross-check speech with the screen.** Speech recognition mangles names and tickers ("YouTube 4"
   for ITUB4, "Cloud Fair" for Cloudflare). Fix them using the title, description and on-screen text in
   the contact sheets. Use `get_frame(path, "m:ss")` to read small text.
4. Answer in the user's language, citing times (`at 0:42 he shows…`).

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
- For people who edit in Premiere/Resolve/Final Cut, add `edl=true` or `fcpxml=true` so they can keep
  editing on their own timeline. `srt=true` gives subtitles matching the edited version.

### Subtitles
`export_transcript(path, ["srt"])`; `words_per_cue=2` for TikTok/Reels-style captions.

### Putting audio into a video
1. Transcribe the video and use `search_transcript` / `understand_media` to find the moment.
2. `place_audio(base=video, insert=audio, at="1:23.4", mode="duck")`: `mix`, `duck` (lowers the original)
   or `replace`. `insert_from`/`insert_to` use only a slice of the clip.

### "What happens in this video?"
1. `understand_media(source)`.
2. Look at the contact sheets: each tile shows shot number and start time; the text lists what is said per
   shot. Describe the video chronologically with timestamps; `get_frame(path, "2:10")` to inspect detail.

## Choosing a model
`tiny`/`base`: fast drafts · `small` (default): good balance · `medium`/`large-v3`: best accuracy, slow on
CPU · `turbo`: near large-v3 quality, faster. A CUDA GPU is used automatically when its libraries exist.
`backend="whisperx"` gives tighter word boundaries (best for precise cuts) and speaker labels.

## Troubleshooting
- A link fails to download: sites change often; the user can update with
  `python -m pipx runpip mediasense install -U yt-dlp`, or set `MEDIASENSE_COOKIES_FROM_BROWSER=firefox`.
- "ffmpeg not found": the user must install FFmpeg (`winget install ffmpeg` / `brew install ffmpeg`).
- Anything else: ask the user to run `mediasense doctor` and paste the output.

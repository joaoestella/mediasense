# Privacy Policy

_Last updated: 7 November 2026_

mediasense is open-source software that runs **entirely on your own computer**. It has no servers, no
accounts, no analytics and no telemetry. The author receives no data from you.

## What mediasense collects

Nothing is collected or sent to the author. mediasense only works on the files and links you give it
(directly or through Claude).

## What it does with your data, and where it is stored

- **Media you point it at** is read from your disk. Videos and audio downloaded from links are saved in
  your download folder (`Downloads/mediasense` by default, configurable with `MEDIASENSE_HOME` or the
  extension's "Download folder" setting).
- **Results** – transcripts (`<name>.transcript.json`), key frames (`<name>.storyboard/`), edited
  files, subtitles and edit lists – are written next to the media on your computer.
- **Speech recognition and video analysis run locally** with an open-source model on your CPU or GPU.
- **What Claude sees:** when you use mediasense from Claude, the tool results (transcript text, key-frame
  images, file names and metadata such as a post's title and description) are returned to the Claude app
  so Claude can answer you. That content is then handled under
  [Anthropic's privacy policy](https://www.anthropic.com/legal/privacy), like anything else you share in
  a conversation.

## Network connections

mediasense connects to the internet only for these purposes:

| When | Connects to | What is sent |
|---|---|---|
| You ask about a link | the site of that link (TikTok, YouTube, Instagram, X, Vimeo…), via yt-dlp | a normal request for that video, like a browser would |
| You turn on "browser cookies" (optional) | the same site only | your login cookies for that site, read locally from the browser you chose, so the site lets you download |
| First transcription | Hugging Face (huggingface.co) | a download request for the speech model (~500 MB) |
| FFmpeg is missing and the extension is used | GitHub (github.com) | a download request for FFmpeg binaries |
| Installing or updating | PyPI / GitHub | standard package downloads |
| Only if you choose `--backend openai` | the transcription API you configure (OpenAI or a compatible provider) | the audio, under that provider's privacy policy |

No other data leaves your computer.

## Sharing with third parties

mediasense does not share, sell or transfer data to anyone. The services listed above receive only what
is described there, under their own privacy policies.

## Retention and deletion

Everything mediasense creates stays on your computer until you delete it. To remove it all, delete the
download folder, the `*.transcript.json` files and `*.storyboard` folders next to your media, and the
speech model cache (`~/.cache/huggingface`). Uninstalling mediasense removes the program itself.

## Children

mediasense is a general-purpose tool and is not directed at children.

## Changes

Changes to this policy are published in this file in the
[GitHub repository](https://github.com/joaoestella/mediasense/blob/main/PRIVACY.md), with the date above.

## Contact

Questions or concerns: open an issue at <https://github.com/joaoestella/mediasense/issues>.

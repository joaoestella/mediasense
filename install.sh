#!/usr/bin/env sh
# mediasense installer for macOS and Linux.
#   curl -LsSf https://raw.githubusercontent.com/joaoestella/mediasense/main/install.sh | sh
#
# Installs (only what's missing): FFmpeg, pipx, mediasense — then connects it to Claude Desktop / Claude Code.
set -eu

PACKAGE="${MEDIASENSE_PACKAGE:-mediasense[all] @ https://github.com/joaoestella/mediasense/archive/refs/heads/main.zip}"
step() { printf '\n\033[36m==> %s\033[0m\n' "$1"; }
have() { command -v "$1" >/dev/null 2>&1; }

step "Checking Python"
PY=""
for c in python3 python; do
  if have "$c" && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.10+ is required. macOS: brew install python · Ubuntu/Debian: sudo apt install python3 python3-venv"
  exit 1
fi
"$PY" --version

step "Checking FFmpeg"
if have ffmpeg; then
  echo "found"
elif have brew; then
  brew install ffmpeg
elif have apt-get; then
  sudo apt-get update && sudo apt-get install -y ffmpeg
elif have dnf; then
  sudo dnf install -y ffmpeg
elif have pacman; then
  sudo pacman -S --noconfirm ffmpeg
else
  echo "Please install FFmpeg (https://ffmpeg.org/download.html) and run this again."; exit 1
fi

step "Installing pipx"
if ! have pipx; then
  if have brew; then
    brew install pipx
  else
    "$PY" -m pip install --user --upgrade pipx 2>/dev/null || "$PY" -m pip install --user --upgrade --break-system-packages pipx
  fi
fi
PIPX="pipx"
have pipx || PIPX="$PY -m pipx"
$PIPX ensurepath >/dev/null 2>&1 || true
export PATH="$HOME/.local/bin:$PATH"

step "Installing mediasense (a few minutes)"
$PIPX install --force "$PACKAGE"

step "Connecting to Claude"
mediasense setup
mediasense doctor || true

printf '\n\033[33mOne more step: in Claude open Settings > Skills and upload ~/Downloads/mediasense/mediasense-skill.zip\033[0m\n'
printf '\n\033[32mDone! Restart Claude Desktop and ask: "summarize this video: <link>"\033[0m\n'
echo "Open a new terminal to use the mediasense command. The first transcription downloads the speech model (~500 MB)."

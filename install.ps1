# mediasense installer for Windows.
# Run in PowerShell:
#   irm https://raw.githubusercontent.com/joaoestella/mediasense/main/install.ps1 | iex
#
# Installs (only what's missing): Python 3.12, FFmpeg, pipx, mediasense — then connects it to
# Claude Desktop / Claude Code.

$ErrorActionPreference = "Stop"
$Package = if ($env:MEDIASENSE_PACKAGE) { $env:MEDIASENSE_PACKAGE } else { "mediasense[all] @ https://github.com/joaoestella/mediasense/archive/refs/heads/main.zip" }

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User") + ";" +
                "$env:USERPROFILE\.local\bin"
}
function Have($cmd) { [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }
function Winget-Install($id) {
    if (-not (Have "winget")) {
        throw "winget not found. Install '$id' manually, then run this script again."
    }
    winget install --id $id -e --silent --accept-source-agreements --accept-package-agreements
    Refresh-Path
}

Step "Checking Python"
$py = $null
foreach ($c in @("python", "py")) {
    if (Have $c) {
        $v = & $c -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($v -and [version]$v -ge [version]"3.10") { $py = $c; break }
    }
}
if (-not $py) {
    Write-Host "Python 3.10+ not found, installing Python 3.12..."
    Winget-Install "Python.Python.3.12"
    $py = "python"
}
Write-Host "using $(& $py --version)"

Step "Checking FFmpeg"
if (Have "ffmpeg") { Write-Host "found" } else { Winget-Install "Gyan.FFmpeg" }

Step "Installing pipx"
& $py -m pip install --user --upgrade --quiet pipx
& $py -m pipx ensurepath | Out-Null
Refresh-Path

Step "Installing mediasense (downloads ~200 MB of libraries, takes a few minutes)"
& $py -m pipx install --force $Package
Refresh-Path

Step "Connecting to Claude"
$exe = "$env:USERPROFILE\.local\bin\mediasense.exe"
if (-not (Test-Path $exe)) { $exe = "mediasense" }
& $exe setup
& $exe doctor

Write-Host "`nDone! Quit Claude Desktop completely (tray icon > Quit), open it again and ask:" -ForegroundColor Green
Write-Host '  "summarize this video: <paste a TikTok/YouTube link>"' -ForegroundColor Green
Write-Host "The first transcription downloads the speech model (~500 MB)."

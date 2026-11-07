# mediasense installer for Windows.
# Run in PowerShell:
#   irm https://raw.githubusercontent.com/joaoestella/mediasense/main/install.ps1 | iex
#
# Installs (only what's missing): Python 3.12, FFmpeg, pipx, mediasense — then connects it to
# Claude Desktop / Claude Code.

$ErrorActionPreference = "Stop"
# pipx and friends print emoji; the default Windows console code page can't encode them
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

$Package = if ($env:MEDIASENSE_PACKAGE) { $env:MEDIASENSE_PACKAGE } else { "mediasense[all]" }
$Fallback = "mediasense[all] @ https://github.com/joaoestella/mediasense/archive/refs/heads/main.zip"

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Have($cmd) { [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }

function Add-NewPath {
    # Append what installers just added, without dropping what this session already had
    # (e.g. an activated Anaconda environment).
    $parts = @($env:Path -split ";") +
             @([Environment]::GetEnvironmentVariable("Path", "Machine") -split ";") +
             @([Environment]::GetEnvironmentVariable("Path", "User") -split ";") +
             @("$env:USERPROFILE\.local\bin")
    $env:Path = (($parts | Where-Object { $_ } | Select-Object -Unique) -join ";")
}

function Quiet([scriptblock]$block) {
    # Windows PowerShell 5.1 turns redirected stderr of a program into a terminating error
    # when ErrorActionPreference is Stop; relax it for commands whose noise we discard.
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $block 2>$null } finally { $ErrorActionPreference = $old }
}

function Check($what) {
    if ($LASTEXITCODE -ne 0) { throw "$what failed (exit code $LASTEXITCODE). Scroll up for the reason." }
}

function Winget-Install($id) {
    if (-not (Have "winget")) { throw "winget not found. Install '$id' manually, then run this again." }
    winget install --id $id -e --silent --accept-source-agreements --accept-package-agreements
    Check "winget install $id"
    Add-NewPath
}

function Find-Python {
    # Returns the absolute path of a Python >= 3.10, so later PATH changes can't swap it out.
    foreach ($c in @("python", "py", "python3")) {
        if (-not (Have $c)) { continue }
        $pyArgs = @()
        if ($c -eq "py") { $pyArgs = @("-3") }
        $code = "import sys; print(sys.version_info >= (3, 10)); print(sys.executable)"
        $out = Quiet { & $c @pyArgs -c $code }
        if ($LASTEXITCODE -eq 0 -and $out -and $out[0] -eq "True" -and (Test-Path $out[1])) { return $out[1] }
    }
    return $null
}

try {
    Step "Checking Python"
    $py = Find-Python
    if (-not $py) {
        Write-Host "Python 3.10+ not found, installing Python 3.12..."
        Winget-Install "Python.Python.3.12"
        $py = Find-Python
        if (-not $py) { throw "Python was installed but isn't visible yet. Open a new PowerShell and run this again." }
    }
    Write-Host "using $(& $py --version) at $py"

    Step "Checking FFmpeg"
    if (Have "ffmpeg") { Write-Host "found" } else { Winget-Install "Gyan.FFmpeg" }

    Step "Installing pipx"
    & $py -m pip install --user --upgrade --quiet --disable-pip-version-check pipx
    Check "pip install pipx"
    Quiet { & $py -m pipx ensurepath --force | Out-Null }   # harmless if already done
    Add-NewPath

    # A running copy (Claude Desktop keeps the MCP server alive) locks mediasense.exe on Windows
    $running = Get-Process mediasense -ErrorAction SilentlyContinue
    if ($running) {
        Write-Host "Stopping the running mediasense server so it can be updated..."
        $running | Stop-Process -Force
        Start-Sleep -Seconds 1
    }

    Step "Installing mediasense (downloads ~200 MB of libraries, takes a few minutes)"
    & $py -m pipx install --force $Package
    if ($LASTEXITCODE -ne 0 -and -not $env:MEDIASENSE_PACKAGE) {
        Write-Host "Not on PyPI yet (or PyPI unreachable) - installing from GitHub instead..."
        & $py -m pipx install --force $Fallback
    }
    Check "pipx install mediasense"

    $binDir = Quiet { & $py -m pipx environment --value PIPX_BIN_DIR }
    $exe = if ($binDir) { Join-Path $binDir "mediasense.exe" } else { "$env:USERPROFILE\.local\bin\mediasense.exe" }
    if (-not (Test-Path $exe)) { throw "mediasense.exe not found at $exe" }

    Step "Connecting to Claude"
    & $exe setup
    Check "mediasense setup"
    Write-Host ""
    & $exe doctor

    $skillZip = Join-Path $env:USERPROFILE "Downloads\mediasense\mediasense-skill.zip"
    if (Test-Path $skillZip) {
        Write-Host "`nOne more step: in Claude open Settings > Skills and upload mediasense-skill.zip" -ForegroundColor Yellow
        Write-Host "(opening the folder for you). It makes Claude use mediasense for video links." -ForegroundColor Yellow
        Start-Process explorer.exe "/select,`"$skillZip`""
    }

    Write-Host "`nDone! Quit Claude Desktop completely (tray icon > Quit), open it again and ask:" -ForegroundColor Green
    Write-Host '  "summarize this video: <paste a TikTok/YouTube link>"' -ForegroundColor Green
    Write-Host "The first transcription downloads the speech model (~500 MB)."
    Write-Host "Open a new terminal to use the 'mediasense' command directly."
}
catch {
    Write-Host "`nInstallation stopped: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Help: https://github.com/joaoestella/mediasense#setup" -ForegroundColor Red
}

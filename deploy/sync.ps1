[CmdletBinding()]
param([string]$Config, [switch]$BuildOnly)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (-not $Config) { $Config = Join-Path $PSScriptRoot 'sync.local.json' }
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program failed (exit $LASTEXITCODE)." }
}

Push-Location $projectRoot
try {
    if (-not $BuildOnly) {
        if (-not (Test-Path -LiteralPath $Config)) {
            throw 'Copy deploy/sync.example.json to deploy/sync.local.json and fill in your server settings first.'
        }
        $settings = Get-Content -LiteralPath $Config -Raw -Encoding UTF8 | ConvertFrom-Json
        $serverHost = [string]$settings.host
        $serverUser = [string]$settings.user
        $remoteDir = ([string]$settings.remote_dir).TrimEnd('/')
        if ($serverHost -notmatch '^[a-zA-Z0-9_][a-zA-Z0-9_.-]*$') { throw 'Invalid server hostname or SSH alias.' }
        if ($serverUser -notmatch '^[a-zA-Z0-9_][a-zA-Z0-9_-]*$') { throw 'Invalid SSH user.' }
        if ($remoteDir -notmatch '^/[a-zA-Z0-9_./-]+$' -or $remoteDir.Split('/') -contains '..') {
            throw 'remote_dir must be an absolute Linux project path without spaces or ..'
        }
        $sshPort = [int]$settings.ssh_port
        $appPort = [int]$settings.app_port
        $readyTimeout = [int]$settings.timeout
        if ($sshPort -lt 1 -or $sshPort -gt 65535 -or $appPort -lt 1 -or $appPort -gt 65535) { throw 'Invalid port.' }
        if ($readyTimeout -lt 1 -or $readyTimeout -gt 600) { throw 'timeout must be between 1 and 600 seconds.' }
        $serviceName = [string]$settings.service
        if ($serviceName -and $serviceName -notmatch '^[a-zA-Z0-9_][a-zA-Z0-9_.@-]*$') { throw 'Invalid systemd service name.' }
        $ssh = (Get-Command ssh -CommandType Application -ErrorAction Stop).Source
        $scp = (Get-Command scp -CommandType Application -ErrorAction Stop).Source
        $common = @('-o', 'ConnectTimeout=15', '-o', 'ServerAliveInterval=15')
        if ($settings.identity_file) {
            $keyPath = (Resolve-Path -LiteralPath $settings.identity_file).Path
            $common += @('-i', $keyPath)
        }
        $sshArgs = @('-p', "$sshPort") + $common
        $scpArgs = @('-P', "$sshPort") + $common
        $target = "${serverUser}@${serverHost}"
    }

    $pnpm = (Get-Command pnpm.cmd -ErrorAction Stop).Source
    Write-Host '[1/4] Install dependencies and build frontend locally'
    Invoke-Checked $pnpm @('--dir', 'frontend', 'install', '--frozen-lockfile')
    Invoke-Checked $pnpm @('--dir', 'frontend', 'run', 'build')

    $releaseId = [guid]::NewGuid().ToString('N')
    $outputDir = Join-Path $projectRoot ".deploy/$releaseId"
    New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
    $archivePath = Join-Path $outputDir 'release.zip'
    $releasePaths = @('app', 'migrations', 'scripts', 'frontend/dist', 'pyproject.toml', 'uv.lock', 'alembic.ini', '.python-version', 'deploy/update.py')
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [System.IO.Compression.ZipFile]::Open($archivePath, [System.IO.Compression.ZipArchiveMode]::Create)
    try {
        foreach ($relative in $releasePaths) {
            $source = Get-Item -LiteralPath (Join-Path $projectRoot $relative) -Force
            $items = @($source)
            if ($source.PSIsContainer) { $items += @(Get-ChildItem -LiteralPath $source.FullName -Recurse -Force) }
            foreach ($item in $items) {
                if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) { throw "Release contains a link: $($item.FullName)" }
                if ($item.PSIsContainer) { continue }
                $entryName = $item.FullName.Substring($projectRoot.Length + 1).Replace('\', '/')
                if ($entryName -match '(^|/)__pycache__/|\.pyc$') { continue }
                [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($archive, $item.FullName, $entryName, [System.IO.Compression.CompressionLevel]::Optimal) | Out-Null
            }
        }
    } finally { $archive.Dispose() }
    Write-Host "[2/4] Package created: $archivePath"
    if ($BuildOnly) { return }

    $stage = "$remoteDir/.deploy/releases/$releaseId"
    $bootstrapSettings = @{ root = $remoteDir; stage = $stage; port = $appPort; timeout = $readyTimeout; service = $serviceName } | ConvertTo-Json -Compress
    $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($bootstrapSettings))
    $bootstrap = @"
import base64, json, os, pathlib, subprocess, zipfile
config = json.loads(base64.b64decode('$encoded'))
root, stage = pathlib.Path(config['root']), pathlib.Path(config['stage'])
os.environ['PATH'] = str(pathlib.Path.home() / '.local/bin') + ':' + os.environ.get('PATH', '')
with zipfile.ZipFile(stage / 'release.zip') as archive:
    for item in archive.infolist():
        if not (stage / 'source' / item.filename).resolve().is_relative_to((stage / 'source').resolve()):
            raise RuntimeError('Invalid archive path')
    archive.extractall(stage / 'source')
command = [str(root / '.venv/bin/python'), str(stage / 'source/deploy/update.py'),
           '--project-root', str(root), '--bundle', str(stage / 'source'),
           '--port', str(config['port']), '--timeout', str(config['timeout'])]
if config['service']:
    command += ['--service', config['service']]
raise SystemExit(subprocess.call(command, cwd=root))
"@
    $bootstrapPath = Join-Path $outputDir 'bootstrap.py'
    [IO.File]::WriteAllText($bootstrapPath, $bootstrap, (New-Object Text.UTF8Encoding($false)))
    Invoke-Checked $ssh ($sshArgs + @($target, "test -f $remoteDir/.env && test -x $remoteDir/.venv/bin/python && mkdir -p $stage"))
    Write-Host '[3/4] Upload source and frontend assets'
    Invoke-Checked $scp ($scpArgs + @($archivePath, $bootstrapPath, "${target}:${stage}/"))
    Write-Host '[4/4] Back up, update, restart and check /readyz on server'
    Invoke-Checked $ssh ($sshArgs + @($target, "$remoteDir/.venv/bin/python $stage/bootstrap.py"))
    Write-Host 'Deployment succeeded: remote /readyz returned status=ready.' -ForegroundColor Green
} finally {
    Pop-Location
}

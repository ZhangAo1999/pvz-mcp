param(
    [string]$InstallDir = 'D:\Programs\PvZ-MCP',
    [string]$GamePath,
    [switch]$NonInteractive,
    [switch]$InstallToolsOnly,
    [switch]$AllowUnsupportedGame
)
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$packageRoot = $PSScriptRoot
$package = Get-Content -LiteralPath (Join-Path $packageRoot 'release.json') -Raw | ConvertFrom-Json
if ($package.version -notmatch '^\d+\.\d+\.\d+(-[a-z0-9.]+)?$') { throw 'Invalid package version' }
$InstallDir = [IO.Path]::GetFullPath($InstallDir)
if ($InstallDir -eq [IO.Path]::GetPathRoot($InstallDir)) { throw 'Select a dedicated installation folder' }
if (!(Test-Path -LiteralPath (Join-Path $InstallDir 'data\bridge-manifest.json')) -and !$GamePath -and !$InstallToolsOnly) {
    if ($NonInteractive) { throw 'GamePath is required for first installation' }
    Add-Type -AssemblyName System.Windows.Forms
    $dialog = New-Object System.Windows.Forms.OpenFileDialog
    $dialog.Title = 'Select your own PlantsVsZombies.exe (supported original 1.2.0.1073)'
    $dialog.Filter = 'Game executable|*.exe'
    if ($dialog.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) { throw 'Installation canceled' }
    $GamePath = $dialog.FileName
}
# Verify every distributed payload file before copying or running a bundled executable.
$payload = [IO.Path]::GetFullPath((Join-Path $packageRoot 'payload'))
$listed = @{}
foreach ($property in $package.files.PSObject.Properties) {
    $relative = $property.Name
    $file = [IO.Path]::GetFullPath((Join-Path $payload $relative))
    if (!$file.StartsWith($payload + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe package path' }
    if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() -ne $property.Value) { throw ('Package hash mismatch: ' + $relative) }
    $listed[$relative.Replace('\','/')] = $true
}
foreach ($file in Get-ChildItem -LiteralPath $payload -File -Recurse) {
    $relative = $file.FullName.Substring($payload.Length+1).Replace('\','/')
    if (!$listed.ContainsKey($relative)) { throw ('Unlisted payload file: ' + $relative) }
}
New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
$versions = Join-Path $InstallDir 'versions'
New-Item -ItemType Directory -Path $versions -Force | Out-Null
$target = Join-Path $versions $package.version
$staged = Join-Path $versions ($package.version + '.staged-' + [guid]::NewGuid().ToString('N'))
Copy-Item -LiteralPath $payload -Destination $staged -Recurse
if (Test-Path -LiteralPath $target) {
    foreach ($property in $package.files.PSObject.Properties) {
        if ((Get-FileHash -LiteralPath (Join-Path $target $property.Name)).Hash.ToLowerInvariant() -ne $property.Value) {
            throw 'This version is already installed with different files. Use a new version number.'
        }
    }
    # Preserve duplicate staging files rather than deleting them.
    $discard = 'D:\Temp\Codex\待删除\PvZ-MCP-duplicate-installs'
    New-Item -ItemType Directory -Path $discard -Force | Out-Null
    Move-Item -LiteralPath $staged -Destination (Join-Path $discard ([IO.Path]::GetFileName($staged)))
} else { Move-Item -LiteralPath $staged -Destination $target }
$env:PVZ_DATA_DIR = Join-Path $InstallDir 'data'
New-Item -ItemType Directory -Path $env:PVZ_DATA_DIR -Force | Out-Null
$env:PYTHONPYCACHEPREFIX = Join-Path $InstallDir 'data\cache\python'
$python = Join-Path $target 'runtime\python\python.exe'
if ($GamePath) {
    if ($AllowUnsupportedGame) {
        & $python (Join-Path $target 'app\configure.py') $GamePath --allow-unsupported
    } else {
        & $python (Join-Path $target 'app\configure.py') $GamePath
    }
    if ($LASTEXITCODE -ne 0) { throw 'Game configuration rejected; active version was not changed' }
}
if (!(Test-Path -LiteralPath (Join-Path $InstallDir 'data\presentation.json'))) {
    '{"speed":1,"show_overlay":true,"advance_ms":2000,"commentary_ms":3000,"decision_hold_ms":0}' | Set-Content -LiteralPath (Join-Path $InstallDir 'data\presentation.json') -Encoding Ascii
}
# Configurable user state is never copied from a release or overwritten on upgrade.
$active = Join-Path $InstallDir 'active.json'
if (Test-Path -LiteralPath $active) {
    $old = Get-Content -LiteralPath $active -Raw | ConvertFrom-Json
    if ($old.version -eq $package.version) { $previous = $old.previous } else { $previous = $old.version }
} else { $previous = $null }
Copy-Item -LiteralPath (Join-Path $packageRoot 'mcp.mjs') -Destination (Join-Path $InstallDir 'mcp.mjs') -Force
Copy-Item -LiteralPath (Join-Path $packageRoot 'Manage.ps1') -Destination (Join-Path $InstallDir 'Manage.ps1') -Force
$next = @{version=$package.version;previous=$previous;installed_at=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json
[IO.File]::WriteAllText(($active+'.new'),$next,(New-Object Text.UTF8Encoding($false)))
if (Test-Path -LiteralPath $active) { [IO.File]::Replace(($active+'.new'),$active,($active+'.previous')) }
else { [IO.File]::Move(($active+'.new'),$active) }
$config = @{mcpServers=@{pvz=@{command=(Join-Path $InstallDir ('versions\'+$package.version+'\runtime\node\node.exe'));args=@((Join-Path $InstallDir 'mcp.mjs'))}}} | ConvertTo-Json -Depth 6
[IO.File]::WriteAllText((Join-Path $InstallDir 'mcp-config.json'),$config,(New-Object Text.UTF8Encoding($false)))
Write-Host ('Installed '+$package.version+' at '+$InstallDir)
Write-Host ('Import '+(Join-Path $InstallDir 'mcp-config.json')+' in your MCP client, then reconnect.')
Write-Host 'No game, save or existing MCP client configuration was modified.'
if (!(Test-Path -LiteralPath (Join-Path $InstallDir 'data\bridge-manifest.json'))) {
    Write-Host 'Tools installed without an active game. Game control remains disabled until a supported EXE is configured.'
}
if (!$NonInteractive) { Read-Host 'Press Enter to finish' | Out-Null }

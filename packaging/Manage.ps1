param([ValidateSet('Status','Rollback','CheckUpdate')][string]$Action='Status')
$ErrorActionPreference='Stop'
$file=Join-Path $PSScriptRoot 'active.json'
$active=Get-Content -LiteralPath $file -Raw | ConvertFrom-Json
if ($Action -eq 'CheckUpdate') {
    if ($active.version -notmatch '^\d+\.\d+\.\d+(-[a-z0-9.]+)?$') { throw 'Invalid active version' }
    $versionRoot=Join-Path $PSScriptRoot ('versions\'+$active.version)
    $env:PVZ_DATA_DIR=Join-Path $PSScriptRoot 'data'
    & (Join-Path $versionRoot 'runtime\node\node.exe') (Join-Path $versionRoot 'app\updates.mjs')
    exit $LASTEXITCODE
}
if($Action -eq 'Rollback') {
    if(!$active.previous -or $active.previous -notmatch '^\d+\.\d+\.\d+(-[a-z0-9.]+)?$') { throw 'No previous installed version' }
    if(!(Test-Path -LiteralPath (Join-Path $PSScriptRoot ('versions\'+$active.previous)))) { throw 'Previous version missing' }
    $next=@{version=$active.previous;previous=$active.version;installed_at=[DateTime]::UtcNow.ToString('o')}|ConvertTo-Json
    [IO.File]::WriteAllText(($file+'.new'),$next,(New-Object Text.UTF8Encoding($false)))
    [IO.File]::Replace(($file+'.new'),$file,($file+'.previous'))
    Write-Host 'Rollback selected. Restart the game through MCP and reconnect the MCP client.'
}
Get-Content -LiteralPath $file

param([ValidateSet('Status','Rollback')][string]$Action='Status')
$ErrorActionPreference='Stop'
$file=Join-Path $PSScriptRoot 'active.json'
$active=Get-Content -LiteralPath $file -Raw | ConvertFrom-Json
if($Action -eq 'Rollback') {
    if(!$active.previous -or $active.previous -notmatch '^\d+\.\d+\.\d+(-[a-z0-9.]+)?$') { throw 'No previous installed version' }
    if(!(Test-Path -LiteralPath (Join-Path $PSScriptRoot ('versions\'+$active.previous)))) { throw 'Previous version missing' }
    $next=@{version=$active.previous;previous=$active.version;installed_at=[DateTime]::UtcNow.ToString('o')}|ConvertTo-Json
    [IO.File]::WriteAllText(($file+'.new'),$next,(New-Object Text.UTF8Encoding($false)))
    [IO.File]::Replace(($file+'.new'),$file,($file+'.previous'))
    Write-Host 'Rollback selected. Restart the game through MCP and reconnect the MCP client.'
}
Get-Content -LiteralPath $file

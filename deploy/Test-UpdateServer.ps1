# Offline control-flow checks. SSH/SCP/Git/Python are replaced with local fixtures.
$ErrorActionPreference = 'Stop'
$OriginalProfile = $env:USERPROFILE
$TestRoot = Join-Path (Resolve-Path (Join-Path $PSScriptRoot '..')).Path ('data\deploy-script-tests-' + [guid]::NewGuid().ToString('N'))
$Source = Join-Path $PSScriptRoot 'Update-Server.ps1'
$RemoteRelease = '123456789abc-20260930-120000'

function git {
    Set-Variable LASTEXITCODE 0 -Scope Global
    if ($args[0] -eq 'status') { ' M dzmm_bot/core.py' }
    else { '123456789abc0000000000000000000000000000' }
}
function ssh-keygen { Set-Variable LASTEXITCODE 0 -Scope Global }
function scp { $global:Uploads++; Set-Variable LASTEXITCODE 0 -Scope Global }
function Get-Command { [pscustomobject]@{ Source = $global:PythonFixture } }
function Read-Host {
    param([string]$Prompt)
    $global:Prompts++
    if ($Prompt -match 'REVIEW ([0-9a-f]{12})') { "REVIEW $($Matches[1])" }
    else { 'DEPLOY' }
}
function ssh {
    $input | Out-Null
    $code = 0
    if ($args -contains 'sudo -n cat /srv/dzmm/app/DEPLOYED_RELEASE_ID /srv/dzmm/app/DEPLOYED_PACKAGE_SHA256') {
        $RemoteRelease
        if ($global:Case -eq 'WrongBaseline') { '0' * 64 } else { $global:BaselineSha }
    }
    elseif ($args -contains 'bash -s --') {
        if ($global:Case -eq 'CandidateFail') { $code = 1 } else { 'CANDIDATE_OK fixture' }
    }
    elseif ($args -contains 'sudo -n flock -n /run/lock/dzmm-deploy.lock bash -s --') {
        $global:Switches++
        if ($global:Case -eq 'SwitchFail') { $code = 1 }
        else { 'GATE worker.healthy=true'; 'RELEASE_ID=fixture' }
    }
    else { $global:Postchecks++; 'active' }
    Set-Variable LASTEXITCODE $code -Scope Global
}

try {
    foreach ($Case in @('Validate', 'Deploy', 'Legacy', 'LocalFail', 'CandidateFail', 'SwitchFail', 'WrongBaseline', 'MissingBaseline')) {
        $global:Case = $Case
        $global:Uploads = 0; $global:Switches = 0; $global:Postchecks = 0; $global:Prompts = 0
        $fixture = Join-Path $TestRoot $Case
        New-Item -ItemType Directory -Force -Path (Join-Path $fixture 'deploy'), (Join-Path $fixture 'data'), (Join-Path $fixture 'profile\.ssh') | Out-Null
        Copy-Item -LiteralPath $Source -Destination (Join-Path $fixture 'deploy\Update-Server.ps1')
        Set-Content -LiteralPath (Join-Path $fixture 'profile\.ssh\known_hosts') -Value 'offline fixture'
        Set-Content -LiteralPath (Join-Path $fixture 'key.pem') -Value 'offline fixture'
        $baseline = Join-Path $fixture "data\deploy-$RemoteRelease.tar.gz"
        Set-Content -LiteralPath $baseline -Value 'baseline fixture'
        $global:BaselineSha = (Get-FileHash -LiteralPath $baseline).Hash.ToLowerInvariant()
        if ($Case -eq 'MissingBaseline') { Remove-Item -LiteralPath $baseline }
        $global:PythonFixture = Join-Path $fixture 'python.cmd'
        @'
@echo off
if "%~1"=="-m" (
  echo Ran fixture tests >&2
  if "%DYBOT_FIXTURE_MODE%"=="LocalFail" goto fail
  echo OK
  exit /b 0
)
if "%~1"=="-c" exit /b 0
goto loop
:fail
exit /b 1
:loop
if "%~1"=="" goto write
if "%~1"=="--output" set "fixture_output=%~2"
if "%~1"=="--report-json" set "fixture_review=%~2"
shift
goto loop
:write
echo candidate>"%fixture_output%"
echo {"added":[],"changed":["dzmm_bot/core.py"],"removed":[]}>"%fixture_review%"
exit /b 0
'@ | Set-Content -LiteralPath $PythonFixture -Encoding ASCII
        $env:USERPROFILE = Join-Path $fixture 'profile'
        $env:DYBOT_FIXTURE_MODE = $Case
        $options = @{ AllowDirtyWorktree = $true; KeyPath = (Join-Path $fixture 'key.pem') }
        if ($Case -eq 'Validate') { $options.ValidateOnly = $true }
        elseif ($Case -ne 'Legacy') { $options.Deploy = $true }
        $failure = $null
        try { & (Join-Path $fixture 'deploy\Update-Server.ps1') @options }
        catch { $failure = $_ }
        $expectedFailure = $Case -notin @('Validate','Deploy','Legacy')
        if (($null -ne $failure) -ne $expectedFailure) { throw "Unexpected result for $Case : $failure" }
        $receipt = Get-ChildItem -LiteralPath (Join-Path $fixture 'data\deployment-logs') -Filter '*.json' |
            Where-Object { $_.Name -notlike '*package-review*' } | Select-Object -First 1
        $state = (Get-Content -Raw -LiteralPath $receipt.FullName | ConvertFrom-Json).state
        $expectedState = if ($Case -eq 'Validate') { 'validated' } elseif ($expectedFailure) { 'failed' } else { 'deployed' }
        if ($state -ne $expectedState) { throw "Wrong receipt state in $Case" }
        if ($Case -in @('Validate','LocalFail','CandidateFail','WrongBaseline','MissingBaseline') -and $Switches -ne 0) { throw "Unexpected production switch in $Case" }
        if ($Case -in @('LocalFail','WrongBaseline','MissingBaseline') -and $Uploads -ne 0) { throw "Unexpected upload in $Case" }
        if ($Case -eq 'SwitchFail' -and $Postchecks -ne 1) { throw 'Failed switch did not trigger read-only postcheck' }
        if ($Case -eq 'Deploy' -and $Prompts -ne 1) { throw 'Explicit deployment should retain only package review prompt' }
        if ($Case -eq 'Legacy' -and $Prompts -ne 2) { throw 'Legacy interactive confirmations changed' }
        Write-Host "PASS $Case"
    }
    Write-Host "8 offline deployment scenarios passed. Fixtures: $TestRoot"
}
finally {
    $env:USERPROFILE = $OriginalProfile
    Remove-Item Env:DYBOT_FIXTURE_MODE -ErrorAction SilentlyContinue
}




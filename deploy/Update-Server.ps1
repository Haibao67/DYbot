[CmdletBinding()]
param(
    [string]$Server = '119.28.232.86',
    [string]$User = 'ubuntu',
    [string]$KeyPath = (Join-Path $env:USERPROFILE 'Downloads\bot.pem'),
    [switch]$AllowDirtyWorktree,
    [string]$BaselinePackage,
    [switch]$ValidateOnly,
    [switch]$Deploy,
    [ValidateRange(30, 240)][int]$WorkerTimeoutSeconds = 60
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($ValidateOnly -and $Deploy) { throw '-ValidateOnly and -Deploy cannot be combined.' }
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Target = "$User@$Server"
$DeployKey = (Resolve-Path -LiteralPath $KeyPath).Path
$KnownHosts = Join-Path $env:USERPROFILE '.ssh\known_hosts'
$Python = Join-Path $RepoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) { $Python = (Get-Command python).Source }
$SshOptions = @('-i', $DeployKey, '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes',
                '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=15',
                '-o', "UserKnownHostsFile=$KnownHosts")

Push-Location $RepoRoot
$TranscriptStarted = $false
$Phase = 'preflight'
$Receipt = [ordered]@{ server = $Server; state = 'preflight'; started_at = (Get-Date).ToString('o') }
$RunId = (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 8)
$LogDirectory = Join-Path $RepoRoot 'data\deployment-logs'
New-Item -ItemType Directory -Force -Path $LogDirectory | Out-Null
$LogPath = Join-Path $LogDirectory "$RunId.log"
$ReceiptPath = Join-Path $LogDirectory "$RunId.json"
try {
    Start-Transcript -Path $LogPath | Out-Null
    $TranscriptStarted = $true
    Write-Host '[1/5] Verify trusted SSH and current release'
    $Pending = (& git status --porcelain --untracked-files=all)
    if ($LASTEXITCODE -ne 0) { throw 'Cannot read Git worktree status.' }
    if ($Pending -and -not $AllowDirtyWorktree) {
        throw "Worktree is not clean. Commit/review changes, or explicitly pass -AllowDirtyWorktree.`n$($Pending -join "`n")"
    }
    if ($AllowDirtyWorktree -and -not $Pending) {
        throw 'Worktree is clean; use the standard committed release without -AllowDirtyWorktree.'
    }
    if (-not $AllowDirtyWorktree -and $BaselinePackage) {
        throw '-BaselinePackage is only used with -AllowDirtyWorktree.'
    }
    try { $KnownHostsAvailable = Test-Path -LiteralPath $KnownHosts -PathType Leaf }
    catch { throw "Trusted SSH known_hosts is not readable: $KnownHosts. Use the correct Windows user session; do not disable host-key checking." }
    if (-not $KnownHostsAvailable) {
        throw "Trusted SSH known_hosts is unavailable: $KnownHosts. Use a shell that can read it; do not disable host-key checking."
    }
    & ssh-keygen -F $Server -f $KnownHosts | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "No trusted SSH host key for $Server in $KnownHosts. Verify the fingerprint through the server console before adding it."
    }
    $RemoteHashLines = @(& ssh @SshOptions $Target 'sudo -n cat /srv/dzmm/app/DEPLOYED_RELEASE_ID /srv/dzmm/app/DEPLOYED_PACKAGE_SHA256')
    if ($LASTEXITCODE -ne 0 -or $RemoteHashLines.Count -ne 2) {
        throw 'SSH preflight failed or the server release markers are unavailable; production was not changed.'
    }
    $RemoteRelease = $RemoteHashLines[0].Trim()
    if ($RemoteRelease -notmatch '^[0-9a-f]{12}-[0-9]{8}-[0-9]{6}$') { throw 'Server release ID is invalid.' }
    $RemotePackageSha = $RemoteHashLines[1].Trim().ToLowerInvariant()
    if ($RemotePackageSha -notmatch '^[0-9a-f]{64}$') {
        throw 'Server release SHA256 marker is invalid; production was not changed.'
    }
    if ($AllowDirtyWorktree) {
        if (-not $BaselinePackage) {
            $BaselinePackage = Join-Path $RepoRoot "data\deploy-$RemoteRelease.tar.gz"
            if (-not (Test-Path -LiteralPath $BaselinePackage -PathType Leaf)) {
                throw "Matching baseline archive is missing: $BaselinePackage. Restore it or pass -BaselinePackage; no upload was attempted."
            }
        }
        $BaselinePath = (Resolve-Path -LiteralPath $BaselinePackage).Path
        $BaselineSha = (Get-FileHash -LiteralPath $BaselinePath -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($BaselineSha -cne $RemotePackageSha) {
            throw "Baseline archive SHA256 does not match the current server release ($RemotePackageSha). Choose the matching archive."
        }
    }
    $Receipt['baseline_release'] = $RemoteRelease
    $Receipt['baseline_sha256'] = $RemotePackageSha
    $Phase = 'local-tests'
    Write-Host '[2/5] Run local tests (full output saved to log)'
    $TestLog = Join-Path $LogDirectory "$RunId-local-tests.log"
    # Windows PowerShell 5.1 wraps redirected native stderr as error records.
    # Warnings must not abort a passing test process; use its exit code instead.
    $SavedErrorAction = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $Python -m unittest discover -s . -p 'test_*.py' -q *> $TestLog
        $TestExit = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $SavedErrorAction }
    if ($TestExit -ne 0) {
        Get-Content -LiteralPath $TestLog -Tail 60
        throw "Local tests failed; production unchanged. Full log: $TestLog"
    }
    Get-Content -LiteralPath $TestLog -Tail 4

    $Commit = (& git rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or $Commit -notmatch '^[0-9a-f]{40}$') { throw 'Cannot resolve Git HEAD.' }
    $Release = '{0}-{1}' -f $Commit.Substring(0, 12), (Get-Date -Format 'yyyyMMdd-HHmmss')
    $Package = Join-Path $RepoRoot "data\deploy-$Release.tar.gz"
    if (Test-Path -LiteralPath $Package) { throw "Release package already exists: $Package" }
    $ReviewPath = Join-Path $LogDirectory "$RunId-package-review.json"
    $PackageArgs = @('deploy\package_release.py', '--output', $Package, '--report-json', $ReviewPath)
    if ($AllowDirtyWorktree) { $PackageArgs += @('--worktree', '--compare-to', $BaselinePath) }
    & $Python @PackageArgs
    if ($LASTEXITCODE -ne 0) { throw 'Release package creation failed.' }
    $PackageSha = (Get-FileHash -LiteralPath $Package -Algorithm SHA256).Hash.ToLowerInvariant()
    $Receipt['release'] = $Release
    $Receipt['package'] = $Package
    $Receipt['package_sha256'] = $PackageSha
    $Receipt['local_tests'] = 'passed'
    if ($AllowDirtyWorktree) {
        & $Python -c 'import json,sys; from deploy.package_release import validate_code_only_changes; validate_code_only_changes(json.load(open(sys.argv[1], encoding="utf-8")))' $ReviewPath
        if ($LASTEXITCODE -ne 0) { throw 'Package requires the manual dependency/migration/removal procedure; production unchanged.' }
    }
    Write-Host "Candidate package SHA256: $PackageSha"
    if ($AllowDirtyWorktree) {
        $Review = Read-Host "Review the exact package changes above; type REVIEW $($PackageSha.Substring(0, 12)) to allow upload"
        if ($Review -cne "REVIEW $($PackageSha.Substring(0, 12))") {
            throw 'Dirty release review was not confirmed; no upload was attempted.'
        }
    }
    $Phase = 'candidate'
    Write-Host '[3/5] Upload and validate candidate; production remains online'
    $UploadPath = "/home/$User/deploy-$Release.tar.gz"
    & scp @SshOptions $Package "${Target}:$UploadPath"
    if ($LASTEXITCODE -ne 0) { throw 'SCP upload failed; production services were not changed.' }

    $CandidateScript = @'
set -euo pipefail
RELEASE="$1"; COMMIT="$2"; EXPECTED_SHA="$3"; UPLOAD="$4"
BASELINE_RELEASE="$5"; BASELINE_SHA="$6"
STAGE="/srv/dzmm/releases/$RELEASE"
BACKUP="/var/backups/dzmm/$RELEASE"
PACKAGE="/srv/dzmm/releases/deploy-$RELEASE.tar.gz"
sudo -n true
systemctl is-active dzmm-core dzmm-worker postgresql
[[ "$RELEASE" =~ ^[0-9a-f]{12}-[0-9]{8}-[0-9]{6}$ ]]
[[ "$COMMIT" =~ ^[0-9a-f]{40}$ ]]
test "$(sha256sum "$UPLOAD" | awk '{print tolower($1)}')" = "$EXPECTED_SHA"
test ! -e "$STAGE"; sudo test ! -e "$BACKUP"
sudo install -d -o dzmmbot -g dzmmbot -m 700 "$STAGE"
sudo install -d -o postgres -g postgres -m 700 "$BACKUP"
sudo install -o dzmmbot -g dzmmbot -m 600 "$UPLOAD" "$PACKAGE"
sudo -u dzmmbot tar -xzf "$PACKAGE" -C "$STAGE"
sudo sed -i 's/\r$//' "$STAGE/deploy/manage.sh"
sudo -u dzmmbot sh -n "$STAGE/deploy/manage.sh"
sudo cmp /srv/dzmm/app/requirements.lock.txt "$STAGE/requirements.lock.txt"
TEST_PY='/srv/dzmm/app/.venv/bin/python'
BASELINE_PACKAGE="/srv/dzmm/releases/deploy-$BASELINE_RELEASE.tar.gz"
test "$(sudo cat /srv/dzmm/app/DEPLOYED_PACKAGE_SHA256)" = "$BASELINE_SHA"
test "$(sudo sha256sum "$BASELINE_PACKAGE" | awk '{print $1}')" = "$BASELINE_SHA"
sudo -u dzmmbot env PYTHONPATH="$STAGE" "$TEST_PY" -c 'import sys; from deploy.package_release import describe_changes, validate_code_only_changes; validate_code_only_changes(describe_changes(sys.argv[1],sys.argv[2]))' "$BASELINE_PACKAGE" "$PACKAGE"
PROD_HEAD=$(sudo -u postgres psql -d dzmm -Atc 'SELECT version_num FROM alembic_version')
TARGET_HEAD=$(sudo -u dzmmbot env PYTHONPATH="$STAGE" "$TEST_PY" -c 'import sys; from alembic.config import Config; from alembic.script import ScriptDirectory; c=Config(); c.set_main_option("script_location", sys.argv[1] + "/dzmm_bot/persistence/migrations"); print(ScriptDirectory.from_config(c).get_current_head())' "$STAGE")
if [ "$PROD_HEAD" != "$TARGET_HEAD" ]; then
  printf 'Migration head changes (%s -> %s); use the migration rehearsal procedure in docs/update-maintenance-guide.md.\n' "$PROD_HEAD" "$TARGET_HEAD" >&2
  exit 3
fi
if ! sudo -u dzmmbot sh -c 'cd "$1" && "$2" -m unittest discover -s . -p "test_*.py" -q > candidate-tests.log 2>&1' sh "$STAGE" "$TEST_PY"; then
  sudo tail -60 "$STAGE/candidate-tests.log"
  exit 1
fi
sudo tail -4 "$STAGE/candidate-tests.log"
if ! sudo -u dzmmbot sh -c 'cd "$1" && "$2" smoke_test.py > candidate-smoke.log 2>&1' sh "$STAGE" "$TEST_PY"; then
  sudo tail -60 "$STAGE/candidate-smoke.log"
  exit 1
fi
sudo tail -2 "$STAGE/candidate-smoke.log"
printf 'CANDIDATE_OK %s %s\n' "$RELEASE" "$TARGET_HEAD"
'@
    $CandidateScript = $CandidateScript.Replace("`r", '') + "`n# end of candidate script"
    $CandidateScript | & ssh @SshOptions $Target 'bash -s --' $Release $Commit $PackageSha $UploadPath $RemoteRelease $RemotePackageSha
    if ($LASTEXITCODE -ne 0) { throw 'Candidate validation failed; production services remain running.' }

    Write-Host "Candidate $Release passed server tests and smoke validation."
    $Receipt['state'] = 'validated'
    $Receipt['candidate_tests'] = 'passed'
    if ($ValidateOnly) {
        Write-Host 'Validation complete; production code and services were not changed.'
        return
    }
    if (-not $Deploy) {
        $Approval = Read-Host 'Type DEPLOY to back up production, update, start Core/Worker, and verify health'
        if ($Approval -cne 'DEPLOY') { throw 'Deployment cancelled; candidate and package were retained.' }
    }
    $Phase = 'deploy'
    Write-Host '[4/5] Back up, switch release, start Core then Worker'

    $DeployScript = @'
set -euo pipefail
RELEASE="$1"; COMMIT="$2"; PACKAGE_SHA="$3"
EXPECTED_BASELINE="$4"; WORKER_TIMEOUT="$5"
BACKUP="/var/backups/dzmm/$RELEASE"
PACKAGE="/srv/dzmm/releases/deploy-$RELEASE.tar.gz"
STAGE="/srv/dzmm/releases/$RELEASE"
STOPPED=0; UPDATED=0; COMPLETE=0; LOGIN_REQUIRED=0; WORKER_START_EPOCH=0
rollback() {
  rc=$?
  if [ "$LOGIN_REQUIRED" = 1 ]; then
    printf "Login recovery required; keeping new code and release markers. No rollback.\n" >&2
    exit "$rc"
  fi
  if [ "$COMPLETE" != 1 ]; then
    set +e
    if [ "$STOPPED" = 1 ]; then
      sudo systemctl stop dzmm-worker dzmm-core
      if [ "$UPDATED" = 1 ] && sudo test -s "$BACKUP/app-before.tar.gz"; then
        sudo test ! -e "$BACKUP/app-failed"
        sudo mv /srv/dzmm/app "$BACKUP/app-failed"
        sudo tar -xzf "$BACKUP/app-before.tar.gz" -C /srv/dzmm
        printf 'Automatic code rollback restored the previous app.\n' >&2
      fi
      sudo systemctl start dzmm-core
      sudo systemctl start dzmm-worker
      printf 'Services were restarted after deployment failure.\n' >&2
    fi
    set -e
  fi
  exit "$rc"
}
trap rollback EXIT
test -s "$PACKAGE"; test -d "$STAGE"
test "$(sudo cat /srv/dzmm/app/DEPLOYED_PACKAGE_SHA256)" = "$EXPECTED_BASELINE"
test "$(sudo sha256sum "$PACKAGE" | awk '{print $1}')" = "$PACKAGE_SHA"
sudo test ! -e "$BACKUP/app-before.tar.gz"
sudo test ! -e "$BACKUP/production-before.dump"
BASELINE=$(sudo -u dzmmbot sh /srv/dzmm/app/deploy/manage.sh status)
BASELINE_FAILED=$(printf '%s' "$BASELINE" | python3 -c 'import ast,sys; print(ast.literal_eval(sys.stdin.read())["outbound"].get("failed",0))')
BASELINE_UNCERTAIN=$(printf '%s' "$BASELINE" | python3 -c 'import ast,sys; print(ast.literal_eval(sys.stdin.read())["outbound"].get("uncertain",0))')
BASELINE_GAME=$(printf '%s' "$BASELINE" | python3 -c 'import ast,json,sys; g=ast.literal_eval(sys.stdin.read())["game"]; print(json.dumps({"balance_difference":g.get("balance_difference"),"pool_tax_difference":g.get("pool_tax_difference"),"pool_ledger_difference":g.get("pool_ledger_difference"),"account_mismatches":sorted(g.get("account_mismatches",[]))},sort_keys=True,separators=(",",":")))')
BASELINE_CORE_RESTARTS=$(systemctl show dzmm-core -p NRestarts --value)
BASELINE_WORKER_RESTARTS=$(systemctl show dzmm-worker -p NRestarts --value)
STOPPED=1
sudo systemctl stop dzmm-worker
sudo systemctl stop dzmm-core
test "$(systemctl show dzmm-worker -p ActiveState --value)" = inactive
test "$(systemctl show dzmm-core -p ActiveState --value)" = inactive
sudo tar -czf "$BACKUP/app-before.tar.gz" -C /srv/dzmm app
sudo chmod 600 "$BACKUP/app-before.tar.gz"
sudo -u postgres pg_dump -Fc -f "$BACKUP/production-before.dump" dzmm
sudo chmod 600 "$BACKUP/production-before.dump"
sudo -u postgres pg_restore --list "$BACKUP/production-before.dump" >/dev/null
sudo tar -tzf "$BACKUP/app-before.tar.gz" >/dev/null
UPDATED=1
sudo tar --no-same-owner -xzf "$PACKAGE" -C /srv/dzmm/app
sudo sed -i 's/\r$//' /srv/dzmm/app/deploy/manage.sh
sudo -u dzmmbot sh -n /srv/dzmm/app/deploy/manage.sh
printf '%s\n' "$COMMIT" | sudo tee /srv/dzmm/app/DEPLOYED_COMMIT >/dev/null
printf '%s\n' "$RELEASE" | sudo tee /srv/dzmm/app/DEPLOYED_RELEASE_ID >/dev/null
printf '%s\n' "$PACKAGE_SHA" | sudo tee /srv/dzmm/app/DEPLOYED_PACKAGE_SHA256 >/dev/null
sudo systemctl start dzmm-core
curl --fail --silent --show-error --retry 10 --retry-connrefused --retry-delay 1 http://127.0.0.1:18120/healthz
WORKER_START_EPOCH=$(date +%s)
sudo systemctl start dzmm-worker
CONNECTED=0
WORKER_DEADLINE=$((WORKER_START_EPOCH + WORKER_TIMEOUT))
while [ "$(date +%s)" -lt "$WORKER_DEADLINE" ]; do
  STATUS=$(sudo -u dzmmbot sh /srv/dzmm/app/deploy/manage.sh status)
  if printf '%s' "$STATUS" | python3 -c 'import ast,sys; d=ast.literal_eval(sys.stdin.read()); w=d["worker"]; assert w["state"] == "connected" and d["worker_stale"] is False and w["time"] >= float(sys.argv[1])' "$WORKER_START_EPOCH" >/dev/null 2>&1; then CONNECTED=1; break; fi
  if systemctl is-active --quiet dzmm-core && systemctl is-active --quiet dzmm-worker &&
     sudo journalctl -u dzmm-worker --since "@$WORKER_START_EPOCH" --no-pager | grep -F 'connection_not_ready: login_or_identity_required' >/dev/null; then
    LOGIN_REQUIRED=1
    exit 1
  fi
  printf 'Waiting for fresh connected Worker heartbeat (%ss elapsed, limit %ss)...\n' "$(( $(date +%s) - WORKER_START_EPOCH ))" "$WORKER_TIMEOUT"
  sleep 5
done
if [ "$CONNECTED" != 1 ]; then
  if systemctl is-active --quiet dzmm-core && systemctl is-active --quiet dzmm-worker &&
     sudo journalctl -u dzmm-worker --since "@$WORKER_START_EPOCH" --no-pager | grep -F 'connection_not_ready: login_or_identity_required' >/dev/null; then
    LOGIN_REQUIRED=1
  fi
  exit 1
fi
CONNECTED_WORKER_RESTARTS=$(systemctl show dzmm-worker -p NRestarts --value)
STATUS=$(sudo -u dzmmbot sh /srv/dzmm/app/deploy/manage.sh status)
FINAL_FAILED=$(printf '%s' "$STATUS" | python3 -c 'import ast,sys; print(ast.literal_eval(sys.stdin.read())["outbound"].get("failed",0))')
FINAL_UNCERTAIN=$(printf '%s' "$STATUS" | python3 -c 'import ast,sys; print(ast.literal_eval(sys.stdin.read())["outbound"].get("uncertain",0))')
FINAL_GAME=$(printf '%s' "$STATUS" | python3 -c 'import ast,json,sys; g=ast.literal_eval(sys.stdin.read())["game"]; print(json.dumps({"balance_difference":g.get("balance_difference"),"pool_tax_difference":g.get("pool_tax_difference"),"pool_ledger_difference":g.get("pool_ledger_difference"),"account_mismatches":sorted(g.get("account_mismatches",[]))},sort_keys=True,separators=(",",":")))')
FINAL_CORE_RESTARTS=$(systemctl show dzmm-core -p NRestarts --value)
FINAL_WORKER_RESTARTS=$(systemctl show dzmm-worker -p NRestarts --value)
FINAL_WORKER_HEALTHY=$(printf '%s' "$STATUS" | python3 -c 'import ast,sys; d=ast.literal_eval(sys.stdin.read()); w=d["worker"]; print("true" if w["state"] == "connected" and d["worker_stale"] is False else "false")')
WORKER_STARTUP_RESTART_DELTA=$((CONNECTED_WORKER_RESTARTS - BASELINE_WORKER_RESTARTS))
printf 'GATE outbound.failed baseline=%s actual=%s\n' "$BASELINE_FAILED" "$FINAL_FAILED"
printf 'GATE outbound.uncertain baseline=%s actual=%s\n' "$BASELINE_UNCERTAIN" "$FINAL_UNCERTAIN"
if [ "$FINAL_UNCERTAIN" -gt "$BASELINE_UNCERTAIN" ]; then
  printf 'WARNING: New uncertain messages require separate platform delivery investigation; do not resend automatically.\n' >&2
fi
printf 'GATE accounting baseline=%s actual=%s\n' "$BASELINE_GAME" "$FINAL_GAME"
printf 'GATE core.restarts baseline=%s actual=%s\n' "$BASELINE_CORE_RESTARTS" "$FINAL_CORE_RESTARTS"
printf 'GATE worker.restarts baseline=%s at_connected=%s final=%s startup_delta=%s\n' "$BASELINE_WORKER_RESTARTS" "$CONNECTED_WORKER_RESTARTS" "$FINAL_WORKER_RESTARTS" "$WORKER_STARTUP_RESTART_DELTA"
printf 'GATE worker.healthy=%s\n' "$FINAL_WORKER_HEALTHY"
if [ "$FINAL_FAILED" != "$BASELINE_FAILED" ] || [ "$FINAL_GAME" != "$BASELINE_GAME" ] || \
   [ "$FINAL_CORE_RESTARTS" != "$BASELINE_CORE_RESTARTS" ] || \
   [ "$FINAL_WORKER_RESTARTS" != "$CONNECTED_WORKER_RESTARTS" ] || \
   [ "$WORKER_STARTUP_RESTART_DELTA" -gt 3 ] || [ "$FINAL_WORKER_HEALTHY" != "true" ]; then
  printf 'Final deployment invariant gate failed; see the GATE lines above.\n' >&2
  exit 1
fi
systemctl is-active dzmm-core dzmm-worker postgresql
curl --fail --silent --show-error --retry 5 --retry-connrefused --retry-delay 1 http://127.0.0.1:18120/healthz
printf '%s' "$STATUS" | python3 -c 'import ast,json,sys; d=ast.literal_eval(sys.stdin.read()); print("STATUS=" + json.dumps({"outbound":d["outbound"],"worker":d["worker"],"worker_stale":d["worker_stale"],"balance_difference":d["game"]["balance_difference"],"pool_tax_difference":d["game"]["pool_tax_difference"],"pool_ledger_difference":d["game"]["pool_ledger_difference"],"account_mismatches":len(d["game"]["account_mismatches"])},ensure_ascii=False))'
printf 'Accounting reconciliation snapshot unchanged from pre-deploy baseline.\n'
printf 'RELEASE_ID='; sudo cat /srv/dzmm/app/DEPLOYED_RELEASE_ID
printf 'PACKAGE_SHA256='; sudo cat /srv/dzmm/app/DEPLOYED_PACKAGE_SHA256
printf 'BACKUP=%s\n' "$BACKUP"
COMPLETE=1
trap - EXIT
'@
    # PowerShell appends a CRLF to piped strings. Keep its CR on a final comment,
    # not on the final Bash command (which previously made `trap - EXIT` fail).
    $DeployScript = $DeployScript.Replace("`r", '') + "`n# end of deployment script"
    # Root owns the lock; it spans the baseline recheck, backup, switch and health gates.
    $DeploymentOutput = @()
    $DeployScript | & ssh @SshOptions $Target 'sudo -n flock -n /run/lock/dzmm-deploy.lock bash -s --' $Release $Commit $PackageSha $RemotePackageSha $WorkerTimeoutSeconds | Tee-Object -Variable DeploymentOutput
    $Receipt['gates'] = @($DeploymentOutput | Where-Object { $_ -like 'GATE *' })
    if ($LASTEXITCODE -ne 0) {
        Write-Warning 'Deployment command returned an error. Reading the current release and service state before reporting.'
        & ssh @SshOptions $Target 'systemctl is-active dzmm-core dzmm-worker postgresql; sudo -n cat /srv/dzmm/app/DEPLOYED_RELEASE_ID; curl --fail --silent --show-error http://127.0.0.1:18120/healthz'
        throw 'Deployment command failed; inspect the read-only postcheck and server rollback output before retrying.'
    }
    $Receipt['state'] = 'deployed'
    $Receipt['backup'] = "/var/backups/dzmm/$Release"
    Write-Host '[5/5] Release markers, accounting and fresh Worker heartbeat verified'
    Write-Host "Deployment complete: $Release"
    Write-Host "Package SHA256: $PackageSha"
}
catch {
    $Receipt['failed_phase'] = $Phase
    $Receipt['error'] = $_.Exception.Message
    $Receipt['state'] = 'failed'
    throw
}
finally {
    $Receipt['finished_at'] = (Get-Date).ToString('o')
    $Receipt['log'] = $LogPath
    $Receipt | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $ReceiptPath -Encoding UTF8
    Write-Host "Deployment receipt: $ReceiptPath"
    if ($TranscriptStarted) { Stop-Transcript | Out-Null }
    Pop-Location
}


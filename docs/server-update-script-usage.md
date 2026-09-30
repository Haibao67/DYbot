# DYbot Server Update Script: Usage

This guide covers the repeatable Windows-to-Ubuntu deployment flow implemented by `deploy/Update-Server.ps1`. Read it together with [`update-maintenance-guide.md`](update-maintenance-guide.md), which remains the source of truth for backups, migrations, recovery, and server operations.

## Requirements

- Run from a Windows machine with PowerShell, Git, OpenSSH `ssh`/`scp`, and the project Python environment (`.venv\Scripts\python.exe`) available.
- The server must be reachable as the configured SSH user and key, with non-interactive `sudo` permission.
- The default key is `C:\Users\ADMIN\Downloads\bot.pem`; SSH keeps its normal host-key verification enabled.
- Run in a Windows session that can read `C:\Users\ADMIN\.ssh\known_hosts`. The script verifies the existing host entry and uses this file explicitly for SSH/SCP. A sandbox that cannot read it stops at preflight; do not disable host-key checking or auto-accept a new key.
- The standard package expects the existing server layout and services documented in `update-maintenance-guide.md`.

## Standard update

From the repository root:

```powershell
powershell -NoProfile -File .\deploy\Update-Server.ps1
```

The script performs these steps:

1. Checks Git status, the trusted SSH host entry and the server's current release SHA256 **before** local tests. Requires a clean worktree for the standard path, then runs the local full test suite and packages committed `HEAD` under a unique release name.
2. Uploads the archive with SCP and verifies its SHA256 on the server.
3. Extracts a separate candidate release, checks that dependency lock files match, and compares the candidate Alembic head with production.
4. Runs the candidate full test suite and isolated Core/Worker smoke check while the production Bot stays online.
5. Shows the candidate result and asks for the exact word `DEPLOY` before touching production.
6. Stops Worker then Core, backs up the application and PostgreSQL database, installs the candidate, starts Core then Worker, and checks Core health, Worker connection/heartbeat, ledger reconciliation, failed-message count, and service restart counts.
7. Saves `DEPLOYED_COMMIT`, `DEPLOYED_RELEASE_ID`, and `DEPLOYED_PACKAGE_SHA256` in `/srv/dzmm/app` for traceability.

The script leaves release packages, candidate directories, and backups in place. Review and clean those artifacts only under the retention procedure in the maintenance guide.

## Server and key overrides

The destination and SSH key can be supplied as parameters:

```powershell
powershell -NoProfile -File .\deploy\Update-Server.ps1 `
  -Server 119.28.232.86 `
  -User ubuntu `
  -KeyPath 'C:\Users\ADMIN\Downloads\bot.pem'
```

Do not put private-key contents or passphrases in the command line or repository. If SSH reports an unknown host key, verify the server fingerprint through a trusted channel before accepting it.

## Worktree releases

The default refuses tracked or untracked changes. Review the intended changes first. For an explicitly authorized worktree deployment:

```powershell
powershell -NoProfile -File .\deploy\Update-Server.ps1 -AllowDirtyWorktree -Deploy
```

The script reads the actual server release ID and automatically locates `data/deploy-<current-release-id>.tar.gz`. It verifies the archive against the current server SHA256; a missing or mismatched archive stops before upload. Use `-BaselinePackage` only to supply the verified archive at another location.

Review every added, changed and removed package file, then enter `REVIEW <first 12 characters of candidate SHA256>`. `-Deploy` explicitly authorizes the production switch and removes the later `DEPLOY` prompt; it does not remove package review or any safety gate. Without `-Deploy`, the original interactive switch confirmation remains.

Packages exclude data, credentials, browser profiles and virtual environments. This overlay flow refuses removed files, dependency changes and any migration-file changes; use the manual procedure for those releases.

## Validate a candidate without switching production

```powershell
powershell -NoProfile -File .\deploy\Update-Server.ps1 -AllowDirtyWorktree -ValidateOnly
```

This runs local tests, package review, upload, server candidate tests and isolated smoke validation. It creates release artifacts but does not replace production code or stop its services. `-ValidateOnly` and `-Deploy` cannot be combined. A later deployment performs validation again; there is no unsafe test bypass.

## Concurrency, connection wait and diagnostic records

- The candidate is compared with the verified server baseline. Before switching, the script checks the baseline again and holds `/run/lock/dzmm-deploy.lock` through backup, switch and health gates. Another deployment or changed baseline stops the switch.
- Worker must report connected with a heartbeat newer than its startup. Default connection timeout is 60 seconds; set `-WorkerTimeoutSeconds` to 30–240 if needed. Login failures retain the new code and require browser login recovery.
- Each run writes a transcript, full local test log, package comparison JSON and result receipt under `data/deployment-logs/`. The receipt includes phase, release, hash and failure details. Console output shows a short summary; failures show the relevant log tail.
- Candidate test and smoke logs remain in `/srv/dzmm/releases/<release>/candidate-tests.log` and `candidate-smoke.log`.
- New failed messages still block the gate. New uncertain messages print a separate warning and require delivery investigation; never automatically resend them. A receipt cannot replace live service/marker verification after failure.

## Offline tooling checks

```powershell
powershell -NoProfile -File .\deploy\Test-UpdateServer.ps1
.\.venv\Scripts\python.exe -m unittest tests.test_deployment_release -q
```

The harness replaces SSH/SCP/Git/Python with fixtures and checks eight success/failure paths. It creates fixtures under `data/` and does not contact the server. These checks cover local control flow and package safety, not live Ubuntu service behavior.

## When the script stops safely

- **Dirty worktree:** review and commit the intended changes, or deliberately use `-AllowDirtyWorktree`.
- **No access to `known_hosts`:** run from the trusted Windows user session that can read it. Do not set `StrictHostKeyChecking=no` or copy a key obtained only from the unverified connection.
- **Baseline mismatch or unexpected package files:** stop before upload. Compare with the actual current server release, narrow the worktree or commit the intended version, then retry with a new release ID.
- **Local or candidate tests fail:** production remains untouched; inspect the failing tests and candidate output.
- **Dependency lock changed:** candidate validation stops. Follow the separate dependency-install procedure in the maintenance guide.
- **Alembic head changed:** candidate validation stops before production maintenance. Complete the production database-copy migration rehearsal and data-preservation checks in the maintenance guide, then perform the reviewed migration release using that procedure.
- **Candidate passes:** production changes only after interactive `DEPLOY` confirmation or an explicit `-Deploy` invocation.
- **Formal switch or health validation fails:** the script attempts to preserve the failed application tree, restore the pre-update application backup, and start the previous Core/Worker. It does not restore the database automatically. Check the server output and services; if restoration does not complete, use the maintenance guide's recovery section.
- **Nonzero exit after a switch:** read the script's read-only postcheck and independently verify release markers, Core/Worker health and the database before deciding whether rollback occurred. Exit code alone cannot prove which version is running. The 2026-09-27 `trap - EXIT` failure is consistent with a PowerShell pipeline CRLF on the last Bash command; the script now places a final comment after it so that an appended CRLF cannot change the command.

The script does not send a test message to a production group. It verifies the services and queue diagnostics; platform Reply rendering still requires a separately authorized real-message check.

## After a run

Confirm the printed release ID, package hash, backup path, active services, healthy Core response, fresh Worker heartbeat, and unchanged accounting reconciliation snapshot versus the pre-deployment baseline. A known pre-existing difference may remain, but any new difference blocks completion and must be investigated. Record the actual outcome in `docs/update-maintenance-guide.md`. A completed SCP transfer by itself is not a successful deployment.


## Login recovery after an update

If the updated Core is healthy and Worker logs report `login_or_identity_required`, retain the new code and release markers. The deployment remains incomplete until login is restored; do not roll back for this condition. Use maintenance guide section 12.1 to open the protected server browser, let the user complete login, save the session, then start Worker and verify its connection and fresh heartbeat. Other deployment failures retain the existing recovery path.

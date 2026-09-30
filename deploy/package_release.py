"""Package application code for upload; excludes credentials and runtime data."""
from pathlib import Path
import argparse
import hashlib
import json
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parent.parent


def archive_contents(path):
    with tarfile.open(path, "r:gz") as archive:
        return {member.name: hashlib.sha256(archive.extractfile(member).read()).hexdigest()
                for member in archive.getmembers() if member.isfile()}


def describe_changes(previous, current):
    before, after = archive_contents(previous), archive_contents(current)
    added = sorted(after.keys() - before.keys())
    changed = sorted(name for name in before.keys() & after.keys() if before[name] != after[name])
    removed = sorted(before.keys() - after.keys())
    print(f"Package changes: {len(added)} added, {len(changed)} changed, {len(removed)} removed")
    for label, paths in (("ADDED", added), ("CHANGED", changed), ("REMOVED", removed)):
        for name in paths:
            print(f"{label} {name}")
    return {"added": added, "changed": changed, "removed": removed}


def validate_code_only_changes(changes):
    """This deployment path cannot apply dependencies, migrations or removals."""
    blocked = list(changes["removed"])
    blocked.extend(name for name in changes["added"] + changes["changed"]
                   if name in {"requirements.txt", "requirements.lock.txt", "alembic.ini"}
                   or name.startswith("dzmm_bot/persistence/migrations/"))
    if blocked:
        raise SystemExit("Manual release procedure required for: " + ", ".join(sorted(set(blocked))))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "deploy-upload.tar.gz",
                        help="unique output archive path")
    parser.add_argument("--worktree", action="store_true",
                        help="include modified and untracked files instead of committed HEAD")
    parser.add_argument("--compare-to", type=Path,
                        help="list content changes against the verified current release archive")
    parser.add_argument("--report-json", type=Path,
                        help="write a machine-readable package review")
    args = parser.parse_args()
    destination = args.output if args.output.is_absolute() else ROOT / args.output
    previous = None
    if args.compare_to:
        previous = args.compare_to if args.compare_to.is_absolute() else ROOT / args.compare_to
        if not previous.is_file():
            raise SystemExit(f"Baseline release archive not found: {previous}")
        if previous.resolve() == destination.resolve():
            raise SystemExit("Baseline and output archive must be different files")
    destination.parent.mkdir(parents=True, exist_ok=True)
    files = ["app.py", "legacy_webhook.py", "requirements.txt", "requirements.lock.txt", "README.md",
             "alembic.ini", "test_game.py", "test_bot.py", "test_core.py", "smoke_test.py", "simulate.py", "install_socket_client.py"]

    if not args.worktree:
        status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"],
                                cwd=ROOT, check=True, capture_output=True, text=True).stdout
        if status:
            raise SystemExit("Worktree is dirty; review it or pass --worktree deliberately")
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
                                capture_output=True, text=True).stdout.strip()
        subprocess.run(["git", "archive", "--format=tar.gz", f"--output={destination}", commit, *files,
                        "dzmm_bot", "deploy", "docs", "tests"], cwd=ROOT, check=True)
        print(f"Created committed release archive: {destination} ({commit})")
        if previous:
            changes = describe_changes(previous, destination)
            if args.report_json:
                args.report_json.write_text(json.dumps(changes, indent=2), encoding="utf-8")
        return

    with tarfile.open(destination, "w:gz") as archive:
        for name in files:
            archive.add(ROOT / name, arcname=name)
        for directory in ("dzmm_bot", "deploy", "docs", "tests"):
            for path in (ROOT / directory).rglob("*"):
                if path.is_file() and not path.is_symlink() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                    archive.add(path, arcname=path.relative_to(ROOT))
    print(f"Created worktree release archive: {destination} (code only; no .env, database or browser profile)")
    if previous:
        changes = describe_changes(previous, destination)
        if args.report_json:
            args.report_json.write_text(json.dumps(changes, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()

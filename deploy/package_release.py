"""Package application code for upload; excludes credentials and runtime data."""
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parent.parent


def main():
    destination = ROOT / "data" / "deploy-upload.tar.gz"
    destination.parent.mkdir(exist_ok=True)
    files = ["app.py", "legacy_webhook.py", "requirements.txt", "requirements.lock.txt", "README.md",
             "alembic.ini", "test_game.py", "test_bot.py", "test_core.py", "smoke_test.py", "simulate.py", "install_socket_client.py"]
    with tarfile.open(destination, "w:gz") as archive:
        for name in files:
            archive.add(ROOT / name, arcname=name)
        for directory in ("dzmm_bot", "deploy", "docs", "tests"):
            for path in (ROOT / directory).rglob("*"):
                if path.is_file() and not path.is_symlink() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                    archive.add(path, arcname=path.relative_to(ROOT))
    print("Created data/deploy-upload.tar.gz (code only; no .env, database or browser profile)")


if __name__ == "__main__":
    main()

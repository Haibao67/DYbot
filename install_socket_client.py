"""Vendor the official Socket.IO browser client with npm integrity verification."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parent
VERSION = "4.8.1"


def main():
    cache = ROOT / "data" / "socket-client"
    cache.mkdir(parents=True, exist_ok=True)
    metadata = cache / "metadata.json"
    archive = cache / "client.tgz"
    def download(url, path):
        subprocess.run(["curl.exe", "-fsSL", "--connect-timeout", "15", "--max-time", "90",
                        "-o", str(path), url], check=True)
    download(f"https://registry.npmjs.org/socket.io-client/{VERSION}", metadata)
    info = json.loads(metadata.read_text(encoding="utf-8"))
    url = info["dist"]["tarball"]
    if not url.startswith("https://registry.npmjs.org/socket.io-client/-/"):
        raise RuntimeError("Unexpected package source")
    download(url, archive)
    integrity = "sha512-" + base64.b64encode(hashlib.sha512(archive.read_bytes()).digest()).decode()
    if integrity != info["dist"]["integrity"]:
        raise RuntimeError("Socket.IO package integrity check failed")
    vendor = ROOT / "dzmm_bot" / "vendor"
    vendor.mkdir(exist_ok=True)
    with tarfile.open(archive) as bundle:
        for source, dest in [("package/dist/socket.io.min.js", "socket.io.min.js"),
                             ("package/LICENSE", "socket.io.LICENSE")]:
            file = bundle.extractfile(source)
            if file is None:
                raise RuntimeError("Missing package file")
            (vendor / dest).write_bytes(file.read())
    (vendor / "socket.io.source.json").write_text(json.dumps({"name": "socket.io-client", "version": VERSION,
        "source": url, "integrity": integrity}, indent=2), encoding="utf-8")
    print("Official Socket.IO browser client installed; npm SHA-512 integrity verified.")


if __name__ == "__main__":
    main()

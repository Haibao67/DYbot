"""Fallback downloader for the exact Chromium build required by Playwright.

Uses Windows curl and official URLs when Playwright's Node downloader times out.
Keeps resumable chunks in the ignored data directory and verifies the published MD5.
"""
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parent


def main():
    import playwright
    package = Path(playwright.__file__).parent / "driver" / "package"
    browsers = json.loads((package / "browsers.json").read_text())["browsers"]
    spec = next(browser for browser in browsers if browser["name"] == "chromium")
    revision, version = spec["revision"], spec["browserVersion"]
    url = f"https://cdn.playwright.dev/builds/cft/{version}/win64/chrome-win64.zip"
    header = subprocess.check_output(["curl.exe", "-fsIL", "--connect-timeout", "15", "--max-time", "45", url], text=True)
    sizes = re.findall(r"(?im)^content-length:\s*(\d+)", header)
    hashes = re.findall(r"(?im)^x-goog-hash:\s*md5=([^\r\n]+)", header)
    if not sizes or not hashes:
        raise RuntimeError("Official archive size/checksum was not available")
    size = int(sizes[-1])
    expected = base64.b64decode(hashes[-1]).hex()
    cache = ROOT / "data" / "browsers"
    parts = cache / f"download-{revision}"
    parts.mkdir(parents=True, exist_ok=True)
    count = 8
    chunk = (size + count - 1) // count

    def download(i):
        start, end = i * chunk, min(size - 1, (i + 1) * chunk - 1)
        path = parts / f"part-{i}"
        needed = end - start + 1
        if path.exists() and path.stat().st_size == needed:
            return path
        subprocess.run(["curl.exe", "-fsSL", "--connect-timeout", "15", "--max-time", "600",
                        "--retry", "2", "--range", f"{start}-{end}", "-o", str(path), url], check=True)
        if path.stat().st_size != needed:
            raise RuntimeError(f"Archive chunk {i} has wrong size")
        print(f"Downloaded chunk {i + 1}/{count}", flush=True)
        return path

    with ThreadPoolExecutor(max_workers=count) as pool:
        paths = list(pool.map(download, range(count)))
    archive = cache / f"verified-chromium-{revision}.zip"
    digest = hashlib.md5()
    with archive.open("wb") as output:
        for path in paths:
            with path.open("rb") as source:
                while block := source.read(1024 * 1024):
                    digest.update(block)
                    output.write(block)
    if digest.hexdigest() != expected:
        raise RuntimeError("Archive checksum mismatch; refusing installation")
    destination = (cache / f"chromium-{revision}").resolve()
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            target = (destination / member.filename).resolve()
            if not target.is_relative_to(destination):
                raise RuntimeError("Archive contains an unsafe path")
        bundle.extractall(destination)
    if not (destination / "chrome-win64" / "chrome.exe").is_file():
        raise RuntimeError("Archive did not contain Chromium")
    (destination / "INSTALLATION_COMPLETE").touch()
    print(f"Verified and installed Playwright Chromium {version} (revision {revision})", flush=True)


if __name__ == "__main__":
    main()

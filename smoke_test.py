"""Start isolated Core/Worker processes and verify the local HTTP lifecycle."""
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time

import httpx


class WindowsTemporaryDirectory(tempfile.TemporaryDirectory):
    def cleanup(self):
        # Windows may release child process file handles shortly after wait().
        for attempt in range(25):
            try:
                return super().cleanup()
            except PermissionError:
                if attempt == 24:
                    raise
                time.sleep(0.2)


def main():
    with WindowsTemporaryDirectory() as temporary:
        env = dict(os.environ, DZMM_CORE_TOKEN=secrets.token_urlsafe(32),
                   DZMM_ADMIN_TOKEN=secrets.token_urlsafe(32), DZMM_WORKER_MODE="simulate",
                   DZMM_GAME_STAGE="full", DZMM_GAME_WHITELIST="",
                   DZMM_DATABASE_URL="sqlite:///" + str(Path(temporary) / "smoke.sqlite3"),
                   DZMM_CORE_URL="http://127.0.0.1:18129", PYTHONIOENCODING="utf-8")
        processes = []
        try:
            for args in (["-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", "18129"],
                         ["-m", "dzmm_bot.worker"]):
                processes.append(subprocess.Popen([sys.executable, *args], env=env,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            with httpx.Client(base_url=env["DZMM_CORE_URL"], timeout=2,
                              headers={"X-Admin-Token": env["DZMM_ADMIN_TOKEN"]}) as client:
                for attempt in range(40):
                    if any(p.poll() is not None for p in processes):
                        raise RuntimeError("Core/Worker exited before health check")
                    try:
                        r = client.get("/healthz")
                        r.raise_for_status()
                        break
                    except httpx.HTTPError:
                        time.sleep(0.25)
                else:
                    raise RuntimeError("Core did not start")
                for command in ("/加入", "/我的", "/牧场 购买 鸡", "/牧场 查看"):
                    client.post("/admin/simulate", json={"text": command}).raise_for_status()
                for attempt in range(40):
                    result = client.get("/admin/status").json()
                    if result["outbound"].get("simulated") == 4:
                        assert result["worker"]["state"] == "simulating"
                        assert result["game"]["balance_total"] == 47
                        assert result["game"]["pool"] == 3
                        assert result["game"]["balance_difference"] == 0
                        print("PASS: separate Core and Worker processes, HTTP commands, durable queue and heartbeat")
                        return
                    time.sleep(0.25)
                raise RuntimeError("Worker did not complete the local queue")
        finally:
            for process in reversed(processes):
                process.terminate()
                process.wait(timeout=10)


if __name__ == "__main__":
    main()

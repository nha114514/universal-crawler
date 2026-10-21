"""Start or reuse only this project's loopback service."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import subprocess
import time

import httpx

from .config import APPLICATION, DATA, ROOT, python_command, runtime_env, settings, token


@contextmanager
def startup_lock():
    DATA.mkdir(parents=True, exist_ok=True)
    with (DATA / "service-start.lock").open("a+b") as file:
        file.seek(0)
        file.write(b"0")
        file.flush()
        if os.name == "nt":
            import msvcrt
            deadline = time.monotonic() + 30
            while True:
                try:
                    file.seek(0)
                    msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Local service startup lock wait timed out")
                    time.sleep(0.1)
        try:
            yield
        finally:
            if os.name == "nt":
                file.seek(0)
                msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)


def alive(url):
    try:
        response = httpx.get(url + "/api/health", timeout=1, trust_env=False)
        data = response.json()
        return data.get("application") == APPLICATION and Path(data.get("data_directory", "")).resolve() == DATA
    except Exception:
        return False


def ensure_service():
    lease_file = DATA / "service.json"
    with startup_lock():
        if lease_file.exists():
            try:
                lease = json.loads(lease_file.read_text(encoding="utf-8"))
                if alive(lease["url"]):
                    return lease["url"], token()
            except (ValueError, KeyError):
                pass
        port = settings()["port"]
        while port < settings()["port"] + 30:
            with socket.socket() as sock:
                try:
                    sock.bind(("127.0.0.1", port))
                    break
                except OSError:
                    port += 1
        else:
            raise RuntimeError("Local service port is unavailable")
        url = f"http://127.0.0.1:{port}"
        api_token = token()
        process = None
        if os.name == "nt":
            # A client's Windows Job Object can kill every Popen descendant on
            # disconnect. Explorer's shell broker owns the shared API instead.
            import pythoncom
            import win32com.client
            pythoncom.CoInitialize()
            try:
                pythonw = Path(python_command()).with_name("pythonw.exe")
                arguments = subprocess.list2cmdline([str(ROOT / "run_service.py"), "--port", str(port), "--data-directory", str(DATA)])
                windows = win32com.client.Dispatch("Shell.Application").Windows()
                desktop = windows.FindWindowSW(0, 0, 8, 0, 1)
                shell = desktop.Document.Application
                shell.ShellExecute(str(pythonw), arguments, str(ROOT), "open", 0)
                del shell, desktop, windows
            finally:
                pythoncom.CoUninitialize()
        else:
            with (DATA / "service.log").open("ab") as log:
                process = subprocess.Popen([python_command(), "-m", "crawler_system.api", "--port", str(port)], cwd=ROOT,
                                           env=runtime_env(), stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                           start_new_session=True)
        for _ in range(150):
            if alive(url):
                health = httpx.get(url + "/api/health", timeout=2, trust_env=False).json()
                lease_file.write_text(json.dumps({"url": url, "pid": health["pid"]}), encoding="utf-8")
                return url, api_token
            if process is not None and process.poll() is not None:
                raise RuntimeError("Local service failed to start, please check data/service.log")
            time.sleep(0.1)
        if process is not None:
            process.terminate()
        raise RuntimeError("Local service startup timed out, please check data/service.log")

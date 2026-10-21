"""Small Windows launcher; packaged EXE locates the saved Python project."""
import ctypes
import os
from pathlib import Path
import subprocess
import sys
import webbrowser


def main():
    root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    if not (root / "crawler_system").is_dir() and (root.parent / "crawler_system").is_dir():
        root = root.parent
    if getattr(sys, "frozen", False):
        python = root / ".venv/Scripts/pythonw.exe"
        script = root / "desktop.py"
        if not python.exists() or not script.exists():
            raise RuntimeError("Project runtime environment not found, please run build/Setup.ps1")
        subprocess.Popen([str(python), str(script)], cwd=root, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return
    sys.path.insert(0, str(root))
    from crawler_system.config import DATA
    from crawler_system.service import ensure_service
    url, secret = ensure_service()
    target = url + "/#token=" + secret
    candidates = [Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
                  Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Microsoft/Edge/Application/msedge.exe"]
    edge = next((path for path in candidates if path.exists()), None)
    if edge:
        subprocess.Popen([str(edge), "--app=" + target, "--user-data-dir=" + str(DATA / "desktop-profile"), "--inprivate", "--disable-sync", "--disable-background-mode", "--no-first-run", "--no-default-browser-check", "--window-size=1440,960"],
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        webbrowser.open(target)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        if os.name == "nt":
            ctypes.windll.user32.MessageBoxW(0, str(error), "Universal Crawler Launch Failed", 0x10)
        else:
            raise

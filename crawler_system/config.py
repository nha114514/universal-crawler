import json
import os
from pathlib import Path
import secrets
import sys

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("CRAWLER_DATA_DIR", str(ROOT / "data"))).resolve()
PYTHON = Path(os.environ.get("CRAWLER_PYTHON", str(ROOT / ".venv/Scripts/python.exe")))
APPLICATION = "universal-crawler-v1"

DEFAULTS = {
    "port": 24761,
    "concurrency": 2,
    "request_timeout": 30,
    "worker_timeout": 150,
    "download_timeout": 3 * 60 * 60,
    "max_text_chars": 300000,
    "max_parse_bytes": 32 * 1024 * 1024,
    "max_download_mb": 0,
    "domain_interval": 2,
    "browser_executable": "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    "search_engines": ["google", "bing", "duckduckgo"],
    "source_region_overrides": {},
    "browser_cache": str(DATA / 'browser-cache'),
    "browser_profile": str(DATA / 'edge-crawler-profile'),
}


def settings():
    DATA.mkdir(parents=True, exist_ok=True)
    path = DATA / "settings.json"
    if not path.exists():
        path.write_text(json.dumps(DEFAULTS, ensure_ascii=False, indent=2), encoding="utf-8")
    config = {"storage_root": str(DATA), **DEFAULTS, **json.loads(path.read_text(encoding="utf-8-sig"))}
    for key in ('legacy_config', 'search_reconstruction_root', 'opencli_profile', 'opencli_browser'):
        config.pop(key, None)
    return config


def save_config(config):
    from .storage import atomic_text
    atomic_text(DATA / "settings.json", json.dumps(config, ensure_ascii=False, indent=2))


def token():
    path = DATA / "api-token"
    DATA.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as file:
            file.write(secrets.token_urlsafe(32))
    except FileExistsError:
        pass
    return path.read_text(encoding="utf-8").strip()


def runtime_env():
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith('OPENCLI_')}
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["CRAWLER_DATA_DIR"] = str(DATA)
    env["PLAYWRIGHT_BROWSERS_PATH"] = settings()["browser_cache"]
    return env


def python_command():
    return str(PYTHON if PYTHON.exists() else Path(sys.executable))


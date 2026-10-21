"""Background entry point launched by the Windows shell, independently of MCP."""
import argparse
import os
from pathlib import Path
import sys

root = Path(__file__).resolve().parent
os.chdir(root)
sys.path.insert(0, str(root))
parser = argparse.ArgumentParser()
parser.add_argument("--port", required=True, type=int)
parser.add_argument("--data-directory", required=True)
args = parser.parse_args()
os.environ["CRAWLER_DATA_DIR"] = args.data_directory
os.environ["PYTHONUTF8"] = "1"
from crawler_system.config import DATA, settings

DATA.mkdir(parents=True, exist_ok=True)
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = settings()["browser_cache"]
with (DATA / "service.log").open("a", encoding="utf-8", buffering=1) as log:
    sys.stdout = sys.stderr = log
    import uvicorn
    from crawler_system.api import create_app
    app = create_app()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning", access_log=False))
    app.state.server = server
    server.run()

from pathlib import Path
import os
import sys

root = Path(__file__).resolve().parent
os.chdir(root)
sys.path.insert(0, str(root))
from crawler_system.mcp_server import mcp

if __name__ == "__main__":
    mcp.run(transport="stdio")
